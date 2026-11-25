# micropip-uv-resolver

**Experimental.** A [micropip](https://github.com/pyodide/micropip) resolver backed by
[uv](https://github.com/astral-sh/uv)'s resolver, compiled to Emscripten (in `uv/`,
which started as the [uv-emscripten](https://github.com/ryanking13/uv-emscripten) PoC).
It plugs into
micropip through `Client.resolver`, so micropip still installs the packages; uv
only decides which ones.

Unlike micropip's built-in resolver, uv backtracks. For example, for
`["fastapi==0.110.0", "starlette>=0.36"]` it picks `starlette 0.36.3` for both, where
micropip's resolver keeps the newest starlette and warns.

`UvResolver` resolves from **package indexes only**: it doesn't use Pyodide's lock file
(`"PYODIDE"` in the index URLs is skipped). Packages need a pure Python or
`pyemscripten_2026_0` wheel on the index; e.g. `requests` and `pydantic` work from PyPI
(charset-normalizer and pydantic-core publish pyemscripten wheels), `numpy` and
`markupsafe` don't.

## Requirements

- **micropip main** (unreleased): needs the resolver contract (`Client.resolver`,
  `ResolveRequest`, `Resolution`), `micropip.sources.ordered_sources()` and
  `ResolveRequest.installed`.
- A runtime with **WebAssembly JSPI** (`WebAssembly.Suspending`): recent Chrome, Node.js 25+.
- **Pyodide 314** (Python 3.14, `pyemscripten_2026_0`) or later. Older Pyodide versions
  are not supported. uv doesn't know this platform yet; `uv/patches/uv.patch` adds
  `wasm32-pyodide2026`.

## Usage

```python
import functools

import micropip
from micropip_uv_resolver import UvResolver, load_uv

uv = await load_uv("https://example.com/uv/uv-resolver.mjs")
micropip.default_client.resolver = functools.partial(UvResolver, uv=uv)
await micropip.install("fastapi")
```

micropip creates a resolver for each request, so the uv module is passed with
`functools.partial`. A separate client works the same way:
`micropip.Client(resolver=functools.partial(UvResolver, uv=uv))`.

`UvResolver` falls back to micropip's resolver without the uv module (`uv=None`) or when JSPI
isn't available, for requirements uv can't handle yet (direct URLs, `emfs:`, `file:`), and
when the index URLs have no package index (e.g. `index_urls=["PYODIDE"]`).

How it works:

- uv's index requests are answered with pages built from micropip's
  `ordered_sources(index_urls)` (without the lock file), so index URLs and
  `micropip.sources.Source` plugins work as with micropip's resolver. Other requests
  (PEP 658 metadata) go through micropip's compatibility layer, so custom layers and
  `credentials` apply.
- uv only considers wheels (no sdists), since nothing can be built here.
- Installed packages (`ResolveRequest.installed`) are preferences too; packages that are
  already installed at the resolved version are skipped, and a different version raises
  "already installed" unless `reinstall=True`, like micropip.
- The result is a `micropip.resolution.Resolution` (with `requested_by`), installed by
  `Client.install_resolution()`.

## Repository layout

```
micropip_uv_resolver/   The Python package: UvResolver (micropip side)
uv/                     uv's resolver compiled to Emscripten (see uv/README.md)
  crates/uv-resolver-wasm/  the wasm entry crate: resolver provider, C ABI, JSPI fetch bridge
  patches/uv.patch          changes to uv, applied to a checkout of uv at a pinned commit
  vendor/                   crates.io crates with small emscripten fixes
  web/uv-resolver.mjs       the JS wrapper around the wasm module (also a demo site)
  scripts/                  setup (emsdk, uv checkout) and build
scripts/                build-uv.sh, build-wheels.sh
tests/                  scenarios against micropip's resolver, uv module tests
```

This repository is the source of truth for all of it; uv itself is checked out at the
commit pinned in `uv/scripts/setup.sh`.

## Building

The uv wasm is not committed (4.6 MB). Build it with:

```sh
./scripts/build-uv.sh
```

The first run installs emsdk into `uv/emsdk`, checks out uv into `uv/uv-src` and
applies `uv/patches/uv.patch`; then it builds the wasm into `dist/uv/`. A fresh build
needs ~15 GB. To use an existing emsdk: `EMSDK_DIR=/path/to/emsdk ./scripts/build-uv.sh`.

## Tests

```sh
MICROPIP_DIR=/path/to/micropip ./scripts/build-wheels.sh   # micropip (main) + this package
./scripts/build-uv.sh
./scripts/get-pyodide.sh                                   # Pyodide 314 (npm package) into build/pyodide
node tests/run.mjs                                         # or PYODIDE_DIR=/path/to/pyodide
node tests/fetch-error.mjs                                 # the uv module alone
```

`tests/scenarios.py` resolves the same requirements with uv and with micropip's
resolver, from PyPI only, and checks the results: pure Python packages and pyemscripten
wheels on PyPI, a case that needs backtracking, packages without a usable wheel (including regex, which used to hang), that
the lock file isn't used, installing and importing what uv resolved, installed packages
and `reinstall`, and the fallback without the uv module.
`tests/fetch-error.mjs` checks that the uv module still works after the host fetch throws.

## Known limitations

- **Slower** than micropip's resolver (2–3x in the scenarios): fetches are sequential
  (prefetching is disabled on Emscripten, and each fetch suspends the wasm stack), and
  each one crosses wasm → JS → Python.
- **Wheels without PEP 658 metadata** are reported as unavailable by the PoC (range
  requests are compiled in but not used). micropip's side is ready (`FetchResponse.status`,
  `FetchOptions.headers`).
- **A panic inside uv makes the uv module unusable** (uv is built with
  `panic = "abort"`): later calls fail with `RuntimeError: unreachable` until the page
  or process is reloaded. Failed fetches don't cause this: `fetch.js` turns a JS
  exception into a failed fetch instead of throwing it into wasm.
- **No lock file**: packages that only Pyodide's lock file provides (e.g. `numpy`,
  `markupsafe`, so also `flask`) can't be resolved with `UvResolver` from PyPI.
- Uses micropip's vendored `packaging` (`micropip._vendored...`) so that `Version` types
  match micropip's.

See [docs/NOTES.md](docs/NOTES.md) for the results, the findings, and how the
patches to uv could be upstreamed.
