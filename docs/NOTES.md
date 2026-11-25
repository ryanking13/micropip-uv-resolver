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
  - `fetch.js` calls a host-provided `globalThis.__uvFetch(url, accept)` if present, and never throws into wasm
  - wheels only (`NoBuild::All`): an sdist can't be built here, and was otherwise selected without metadata
- micropip side (`UvResolver`):
  - uv's index URL is `micropip://project`. micropip answers `micropip://project/<name>/` with a PEP 691 page built from `micropip.sources.ordered_sources()` (so Source plugins work), and every other request goes through `compat_layer.fetch()` (so custom compat layers and credentials work).
  - **The lock file is not used**: `"PYODIDE"` is skipped in the index URLs, and with no package index left it falls back to micropip's resolver. An earlier version showed lock file packages to uv as virtual wheels and expressed micropip's "lock file first" rule (#314) as uv preferences; that only approximated micropip's behavior (uv also resolved the lock file packages' dependencies, which micropip leaves to `pyodide.loadPackage`), so it was dropped.
  - uv's output becomes a `Resolution` (wheels not downloaded; `requested_by` from uv's graph). micropip's `install_resolution()` installs it unchanged.

## Results (node 25, Pyodide 314.0.8)

Only Pyodide 314 (Python 3.14, `pyemscripten_2026_0`) and later are supported. An earlier
run on Pyodide 0.29.5 (with the lock file) found the greedy resolver bugs below (fixed in micropip#326).
Both resolvers use `index_urls=["PYPI"]`:

| Scenario | uv | micropip (greedy) |
|---|---|---|
| `requests`, `httpx` | same packages (charset-normalizer: pyemscripten wheel on PyPI), 1.8 s | same, 0.7 s |
| `pydantic` | same packages (pydantic-core: pyemscripten wheel on PyPI), 0.8 s | same, 0.5 s |
| `fastapi==0.110.0`, `starlette>=0.36` | **consistent**: starlette 0.36.3, 1.8 s | keeps starlette 1.10.0 and warns, 0.8 s |
| `markupsafe` | "no usable wheels" (only native wheels and an sdist) | — |
| `regex` | **hangs** (see finding 8) | — |
| install + import `requests` | works | — |

## Findings

### For micropip
1. ~~`LockFile` can't be enumerated.~~ Done: `LockFile.__iter__` (micropip#327).
2. **Greedy resolver bugs found by the comparison:**
   - the same lock file package can be added twice (`markupsafe`), like the index race fixed in #313;
   - no conflict detection between a pinned dependency and a lock file version (two `starlette` versions installed).

   Both fixed in micropip#326 (a conflict now keeps the first selection with a warning).
3. ~~Installed packages~~ Done: `ResolveRequest.installed` (micropip#328). They are passed to uv as preferences; a resolved version that differs from the installed one raises "already installed" unless `reinstall=True`, like micropip.
4. **Performance**: uv is 2–3x slower here. Fetches are sequential (prefetching is disabled on emscripten in the patch, and each fetch suspends the wasm stack), and each one crosses wasm → JS → Python.

### For the PoC
5. ~~**The module is unusable after a JS exception.**~~ Fixed in `fetch.js`. A JS exception thrown into wasm unwound the Rust frames without running destructors (`panic = "abort"`, so there are no landing pads). futures' `block_on` stayed marked as entered, the next call panicked with "cannot execute `LocalPool` executor from within another executor", the module aborted, and every later call failed with `RuntimeError: unreachable`. `fetch.js` now returns a null body (a failed fetch) instead of throwing; `tests/fetch-error.mjs` covers it. A panic inside uv still aborts the module; building with `panic = "unwind"` and `catch_unwind` in `uv_resolve`, or re-creating the module, would handle that.
6. ~~**Preferences aren't always honored**~~ (Pyodide 0.29 only). On 0.29.5, `anyio` came from PyPI (4.14.2) although the lock file's 4.9.0 satisfies the requirements. uv did pick 4.9.0 first, but its lock file dependencies go `anyio` → `ssl` (an unvendored stdlib module) → `libopenssl` (a shared library), and `libopenssl`'s version `1.1.1w` isn't PEP 440, so uv couldn't read its virtual wheel and backtracked out of `ssl` and `anyio` 4.9.0. Pyodide 314's lock file has no stdlib modules and no versions that aren't PEP 440, so this doesn't happen there.
7. Wheels without PEP 658 metadata are still "unavailable" (range requests are compiled in but the provider doesn't use them). micropip's side is ready (`FetchOptions.headers` for `Range`, `FetchResponse.status` for 206, #321).
8. ~~**uv hangs on `regex` from PyPI.**~~ Fixed in `patches/uv.patch`. regex has 361 versions without a usable wheel, so uv rules them out one by one, and each step narrows regex's range. `pre_visit` sends a `Request::Prefetch` for every new range with `blocking_send` on the request channel (capacity 300). Natively the fetcher drains it on another thread. In the patched solver the fetcher runs in the solver's task (`try_join!`), so it only runs while the solver awaits, and these steps never await (the metadata is already there): the 301st prefetch parked the solver on a full channel forever (the tokio fork's JSPI park, on a promise nothing resolves). On emscripten the channel now has `Semaphore::MAX_PERMITS` capacity, so sends never block; regex fails with "no usable wheels" in about 1 s. Found with a wasm build keeping function names (`--profiling-funcs`, no strip) and a log in `emscripten_promise_await_unchecked`: the stack was `uv_resolve` → `pre_visit` → `block_on(Sender::send)`.

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

### 3. uv: the Pyodide 2026 platform (small, independent)
- `wasm32-pyodide2026` target triple (Pyodide 314, Python 3.14), mapped to `Os::PyEmscripten { major: 2026, minor: 0 }`, which uv already has with its tags (`pyemscripten_2026_0_wasm32`). Emscripten 5.0.3 for the markers. Also in `uv.schema.json`. Upstream uv main only has 2024 and 2025.

### 4. uv: making the resolver usable from a custom provider (good case for upstream)
- `RegistryClient::from_pypi_files` (`SimpleDetailMetadata::from_pypi_files`), `OwnedArchive::from_unarchived`, `VersionMap::from_simple_metadata` made `pub`.
- uv already has `Resolver::new_custom_io` for custom I/O, but a custom `ResolverProvider` can't build a `VersionMap` from an index page without these. That's an argument for upstreaming independent of wasm: "make `new_custom_io` usable".

### 5. uv: compiling for emscripten (plausible, needs a CI target)
- `tokio` `process`/`signal` and `nix` features moved to target-specific deps (`uv-auth`, `uv-build-frontend`, `uv-installer`, `uv`, `uv-unix`); the subprocess keyring provider stubbed on emscripten.
- `#[async_trait]` → `async_trait(?Send)` on emscripten for the auth and client middleware (single-threaded runtime).
- Small, cfg-gated, no behavior change on native. Likely acceptable if uv wants to keep emscripten building, which would need a CI job to not regress.

### 6. uv: running the solver without a thread (hardest; needs design discussion)
- The patch makes `solve`, `choose_version*`, `get_dependencies*` `async` **on all targets**, replaces `wait_blocking()` with an async `wait()`, runs the solver in-task on emscripten (no thread), and disables prefetching there. On native it keeps the thread and wraps `solve` in `block_on`.
- Touches uv's hot path; maintainers would need to accept async in the solver (or a cfg'd variant, which duplicates code). Best started as an issue describing the use case (micropip/Pyodide), with numbers showing no native regression.
- Disabling prefetching is a big part of the slowness in finding 4; a single-threaded prefetch (spawn_local) would be a follow-up.
- Running the fetcher in the solver's task also means the solver must never block on the request channel: the patch makes the channel's capacity unbounded on emscripten (finding 8). An async solver upstream would rather `send().await`.

### Suggested order
1. uv: the `wasm32-pyodide2026` target (needed to run on Pyodide 314 at all)
2. Vendored crate PRs (astral-sh ones first)
3. uv: public constructors for custom providers (useful beyond wasm)
4. uv: cfg gates / `?Send` for emscripten, with a CI build job
5. uv: async solver — open an issue first
6. Drop git forks as tokio/mio/libc release emscripten support
