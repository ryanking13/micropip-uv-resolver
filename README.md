# micropip-uv-resolver

**Experimental.** A [micropip](https://github.com/pyodide/micropip) resolver backed by
[uv](https://github.com/astral-sh/uv)'s resolver, compiled to Emscripten
([uv-emscripten](https://github.com/ryanking13/uv-emscripten) PoC). It plugs into
micropip through `Client.resolver_class`, so micropip still installs the packages; uv
only decides which ones.

Unlike micropip's built-in resolver, uv backtracks. For example, for
`["fastapi==0.110.0", "starlette>=0.36"]` it picks `starlette 0.36.3` for both, where
micropip's resolver keeps Pyodide's newer starlette and warns.

## Requirements

- **micropip main** (unreleased): needs the resolver contract (`Client.resolver_class`,
  `ResolveRequest`, `Resolution`), `micropip.sources.ordered_sources()`,
  `LockFile.__iter__` and `ResolveRequest.installed`.
- A runtime with **WebAssembly JSPI** (`WebAssembly.Suspending`): recent Chrome, Node.js 25+.
- Pyodide 2025 (Python 3.13) was tested.

## Usage

```python
import micropip
from micropip_uv_resolver import UvResolver, is_supported, load_uv

await load_uv("https://example.com/uv/uv-resolver.mjs")  # or set globalThis.uvResolver yourself
micropip.default_client.resolver_class = UvResolver       # or a micropip.Client subclass
await micropip.install("fastapi")
```

`UvResolver` falls back to micropip's resolver when the uv module isn't loaded or JSPI
isn't available, and for requirements uv can't handle yet (direct URLs, `emfs:`, `file:`).

How it works:

- uv's index requests are answered with pages built from micropip's
  `ordered_sources(index_urls)`, so index URLs, `micropip.sources.Source` plugins and
  `"PYODIDE"` work as with micropip's resolver. Other requests (PEP 658 metadata) go
  through micropip's compatibility layer, so custom layers and `credentials` apply.
- Lock file packages are shown to uv as virtual wheels whose metadata comes from
  `LockedPackage.requires`. When the lock file comes first (the default), its versions
  are uv **preferences**: used when they satisfy the requirements, otherwise uv falls
  back to the index (micropip's rule from micropip#314).
- Installed packages (`ResolveRequest.installed`) are preferences too; packages that are
  already installed at the resolved version are skipped, and a different version raises
  "already installed" unless `reinstall=True`, like micropip.
- The result is a `micropip.resolution.Resolution` (with `requested_by`), installed by
  `Client.install_resolution()`.

## Building

The uv wasm is not committed (4.6 MB). Build it with:

```sh
./scripts/build-uv.sh
```

It clones uv-emscripten at the pinned commit into `build/uv-emscripten`, applies
`uv/micropip.patch` (index URL and preferences inputs, selected wheel in the output, a
host fetch hook), and builds it into `dist/uv/`. A fresh build checks out uv and needs
~15 GB. To reuse an existing uv-emscripten build:

```sh
UV_SRC_FROM=/path/to/uv-emscripten/uv-src EMSDK_DIR=/path/to/uv-emscripten/emsdk ./scripts/build-uv.sh
```

## Tests

```sh
MICROPIP_DIR=/path/to/micropip ./scripts/build-wheels.sh   # micropip (main) + this package
./scripts/build-uv.sh
PYODIDE_DIR=/path/to/pyodide-0.29.5 node tests/run.mjs
```

`tests/scenarios.py` resolves the same requirements with uv and with micropip's
resolver and checks the results: packages from PyPI, the lock file first,
`index_urls=["PYODIDE"]`, a case that needs backtracking, installing and importing what
uv resolved, installed packages and `reinstall`, and the fallback without the uv module.

## Known limitations

- **Slower** than micropip's resolver (3–4x in the scenarios): fetches are sequential
  (prefetching is disabled on Emscripten, and each fetch suspends the wasm stack), and
  each one crosses wasm → JS → Python.
- **Preferences aren't always honored**: e.g. `anyio` comes from PyPI although the lock
  file version satisfies the requirements. Not investigated yet.
- **Wheels without PEP 658 metadata** are reported as unavailable by the PoC (range
  requests are compiled in but not used). micropip's side is ready (`FetchResponse.status`,
  `FetchOptions.headers`).
- **The uv module is unusable after a JS exception** thrown through it; `UvResolver`
  never raises from its fetch callback to avoid that.
- The lock file rule is approximated with preferences (see above), and lock file
  packages are resolved by uv (including their dependencies), where micropip leaves
  those to `pyodide.loadPackage`.
- Uses micropip's vendored `packaging` (`micropip._vendored...`) so that `Version` types
  match micropip's.

See [docs/NOTES.md](docs/NOTES.md) for the results, the findings, and how the
uv-emscripten patches could be upstreamed.
