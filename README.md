# micropip-uv-resolver

**Experimental.** A [micropip](https://github.com/pyodide/micropip) resolver backed by
[uv](https://github.com/astral-sh/uv)'s resolver, compiled to Emscripten (in `uv/`,
which started as the [uv-emscripten](https://github.com/ryanking13/uv-emscripten) PoC).
It plugs into
micropip through `Client.resolver`, so micropip still installs the packages; uv
only decides which ones.

Unlike micropip's built-in resolver, uv backtracks. For example, for
`["fastapi==0.110.0", "starlette>=0.36"]` it picks `starlette 0.36.3` for both, where
micropip's resolver keeps Pyodide's newer starlette and warns.

## Requirements

- **micropip main** (unreleased): needs the resolver contract (`Client.resolver`,
  `ResolveRequest`, `Resolution`), `micropip.sources.ordered_sources()`,
  `LockFile.__iter__` and `ResolveRequest.installed`.
- A runtime with **WebAssembly JSPI** (`WebAssembly.Suspending`): recent Chrome, Node.js 25+.
- Pyodide 2025 (Python 3.13) was tested.

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
PYODIDE_DIR=/path/to/pyodide-0.29.5 node tests/run.mjs
node tests/fetch-error.mjs                                 # the uv module alone
```

`tests/scenarios.py` resolves the same requirements with uv and with micropip's
resolver and checks the results: packages from PyPI, the lock file first,
`index_urls=["PYODIDE"]`, a case that needs backtracking, installing and importing what
uv resolved, installed packages and `reinstall`, and the fallback without the uv module.
`tests/fetch-error.mjs` checks that the uv module still works after the host fetch throws.

## Known limitations

- **Slower** than micropip's resolver (3–4x in the scenarios): fetches are sequential
  (prefetching is disabled on Emscripten, and each fetch suspends the wasm stack), and
  each one crosses wasm → JS → Python.
- **Preferences aren't always honored**: e.g. `anyio` comes from PyPI although the lock
  file version satisfies the requirements. Not investigated yet.
- **Wheels without PEP 658 metadata** are reported as unavailable by the PoC (range
  requests are compiled in but not used). micropip's side is ready (`FetchResponse.status`,
  `FetchOptions.headers`).
- **A panic inside uv makes the uv module unusable** (uv is built with
  `panic = "abort"`): later calls fail with `RuntimeError: unreachable` until the page
  or process is reloaded. Failed fetches don't cause this: `fetch.js` turns a JS
  exception into a failed fetch instead of throwing it into wasm.
- The lock file rule is approximated with preferences (see above), and lock file
  packages are resolved by uv (including their dependencies), where micropip leaves
  those to `pyodide.loadPackage`.
- Uses micropip's vendored `packaging` (`micropip._vendored...`) so that `Version` types
  match micropip's.

See [docs/NOTES.md](docs/NOTES.md) for the results, the findings, and how the
patches to uv could be upstreamed.
