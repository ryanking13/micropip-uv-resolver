# Notes: plugging uv's resolver into micropip

Checks that micropip's resolver contract (`Resolver(compat_layer, request).resolve() -> Resolution`,
micropip#320–#324) is enough to plug in uv's resolver compiled to Emscripten. Not a PR.

## Status of the micropip side

The micropip changes this needs are on micropip main (not released yet):
the resolver contract (#320), `FetchResponse.status` (#321), `LockedPackage.requires`
(#322), `sources.ordered_sources()` (#323), downloading separated from resolving (#324),
no duplicate selections (#326), `LockFile.__iter__` (#327, used for the lock file
preferences) and `ResolveRequest.installed` (#328, used for installed packages).

## How it fits together

- uv runs as a separate Emscripten module, loaded with `load_uv()` and passed to `UvResolver` as a setting (`Client(resolver=functools.partial(UvResolver, uv=uv))`, micropip#330).
- Changes to the uv-emscripten PoC for micropip (in `uv/crates/uv-resolver-wasm` and `uv/web/uv-resolver.mjs`, nothing in uv):
  - input: `index_url` (instead of hard-coded PyPI), `preferences`
  - output: the selected wheel's `url`, `filename`, `sha256`
  - `fetch.js` calls a host-provided `globalThis.__uvFetch(url, accept)` if present
- micropip side (`UvResolver`):
  - uv's index URL is `micropip://project`. micropip answers `micropip://project/<name>/` with a PEP 691 page built from `micropip.sources.ordered_sources()` (so Source plugins work), and every other request goes through `compat_layer.fetch()` (so custom compat layers and credentials work).
  - Lock file packages are shown as a virtual wheel tagged with the most specific Pyodide tag, whose METADATA is built from `LockedPackage.requires`.
  - micropip's "lock file first, fall through if no satisfying version" rule (#314) is expressed as uv **preferences** for the lock file versions, plus a page that has both the lock file version and the index files.
  - uv's output becomes a `Resolution` (wheels not downloaded; lock file packages; `requested_by` from uv's graph). micropip's `install_resolution()` installs it unchanged.

## Results (node 25, Pyodide 0.29.5)

| Scenario | uv | micropip (greedy) |
|---|---|---|
| `flask`, `httpx` | same packages, 1.3 s | 0.5 s; **lists `markupsafe` (lock file) twice** |
| `regex`, `attrs` (lock file) | lock file versions, also lists `six` (follows `LockedPackage.requires`) | lock file versions (`loadPackage` resolves `six` itself) |
| `index_urls=["PYODIDE"]`, `numpy` | same | same |
| `fastapi==0.110.0`, `starlette>=0.36` | **consistent**: starlette 0.36.3 from PyPI (lock file has 0.47.2, which fastapi doesn't allow), the rest mostly from the lock file, 1.8 s | **installs two starlette versions** (0.36.3 and 0.47.2) |
| install + import `requests`, `regex` | works | — |

## Findings

### For micropip
1. ~~`LockFile` can't be enumerated.~~ Done: `LockFile.__iter__` (micropip#327).
2. **Greedy resolver bugs found by the comparison:**
   - the same lock file package can be added twice (`markupsafe`), like the index race fixed in #313;
   - no conflict detection between a pinned dependency and a lock file version (two `starlette` versions installed).

   Both fixed in micropip#326 (a conflict now keeps the first selection with a warning).
3. ~~Installed packages~~ Done: `ResolveRequest.installed` (micropip#328). They are passed to uv as preferences; a resolved version that differs from the installed one raises "already installed" unless `reinstall=True`, like micropip.
4. **Performance**: uv is 3–4x slower here. Fetches are sequential (prefetching is disabled on emscripten in the patch, and each fetch suspends the wasm stack), and each one crosses wasm → JS → Python.

### For the PoC
5. ~~**The module is unusable after a JS exception.**~~ Fixed in `fetch.js`. A JS exception thrown into wasm unwound the Rust frames without running destructors (`panic = "abort"`, so there are no landing pads). futures' `block_on` stayed marked as entered, the next call panicked with "cannot execute `LocalPool` executor from within another executor", the module aborted, and every later call failed with `RuntimeError: unreachable`. `fetch.js` now returns a null body (a failed fetch) instead of throwing; `tests/fetch-error.mjs` covers it. A panic inside uv still aborts the module; building with `panic = "unwind"` and `catch_unwind` in `uv_resolve`, or re-creating the module, would handle that.
6. **Preferences aren't always honored**: `anyio` came from PyPI (4.14.2) although the lock file's 4.9.0 satisfies the requirements; `pydantic`, `idna`, ... did use lock file versions. Not investigated yet.
7. Wheels without PEP 658 metadata are still "unavailable" (range requests are compiled in but the provider doesn't use them). micropip's side is ready (`FetchOptions.headers` for `Range`, `FetchResponse.status` for 206, #321).

## Upstreaming the PoC's patches

### 1. Dependency forks (`[patch.crates-io]` git entries): wait for releases
| Patch | Why | Path |
|---|---|---|
| `libc` from git (`libc-0.2` branch) | unreleased emscripten fixes | drop when the next libc 0.2 release has them |
| `tokio`, `tokio-macros`, `mio` from guybedford's `*-cf.emscripten` tags | emscripten support (single-threaded runtime, I/O) | depends on tokio/mio accepting emscripten support; outside uv. Track upstream PRs. |
| `.cargo/config.toml`: `--cfg=tokio_unstable`, wasm exception flags | needed by the tokio fork and Rust's wasm EH | belongs to the wasm build (the PoC crate / CI), or a `[target.wasm32-unknown-emscripten]` section in uv if uv adds an emscripten CI job |

### 2. Vendored crates: small, independent PRs (easiest)
| Crate | Fix | Upstream |
|---|---|---|
| `astral-tokio-tar` | `cfg(unix)` and `cfg(wasm32)` both match on emscripten → exclude wasm32 from unix-only helpers | astral-sh (owned by Astral, easy) |
| `astral_async_http_range_reader` | `tokio::spawn` needs `Send`; use `spawn_local` on emscripten | astral-sh (owned by Astral). Only matters once range requests are used. |
| `reqsign-core` | duplicate `home_dir` (same cfg overlap); `jwt` gated off wasm32 although it builds | apache/opendal (reqsign) |
| `wasmtimer` | newer wasm-bindgen needs `UnwindSafe` closures → `AssertUnwindSafe` | wasmtimer-rs; not emscripten-specific |

### 3. uv: making the resolver usable from a custom provider (good case for upstream)
- `RegistryClient::from_pypi_files` (`SimpleDetailMetadata::from_pypi_files`), `OwnedArchive::from_unarchived`, `VersionMap::from_simple_metadata` made `pub`.
- uv already has `Resolver::new_custom_io` for custom I/O, but a custom `ResolverProvider` can't build a `VersionMap` from an index page without these. That's an argument for upstreaming independent of wasm: "make `new_custom_io` usable".

### 4. uv: compiling for emscripten (plausible, needs a CI target)
- `tokio` `process`/`signal` and `nix` features moved to target-specific deps (`uv-auth`, `uv-build-frontend`, `uv-installer`, `uv`, `uv-unix`); the subprocess keyring provider stubbed on emscripten.
- `#[async_trait]` → `async_trait(?Send)` on emscripten for the auth and client middleware (single-threaded runtime).
- Small, cfg-gated, no behavior change on native. Likely acceptable if uv wants to keep emscripten building, which would need a CI job to not regress.

### 5. uv: running the solver without a thread (hardest; needs design discussion)
- The patch makes `solve`, `choose_version*`, `get_dependencies*` `async` **on all targets**, replaces `wait_blocking()` with an async `wait()`, runs the solver in-task on emscripten (no thread), and disables prefetching there. On native it keeps the thread and wraps `solve` in `block_on`.
- Touches uv's hot path; maintainers would need to accept async in the solver (or a cfg'd variant, which duplicates code). Best started as an issue describing the use case (micropip/Pyodide), with numbers showing no native regression.
- Disabling prefetching is a big part of the slowness in finding 4; a single-threaded prefetch (spawn_local) would be a follow-up.

### Suggested order
1. Vendored crate PRs (astral-sh ones first)
2. uv: public constructors for custom providers (useful beyond wasm)
3. uv: cfg gates / `?Send` for emscripten, with a CI build job
4. uv: async solver — open an issue first
5. Drop git forks as tokio/mio/libc release emscripten support
