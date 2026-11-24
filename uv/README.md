# uv's resolver in the browser

Compiles [uv](https://github.com/astral-sh/uv)'s dependency resolver to `wasm32-unknown-emscripten`.
`micropip_uv_resolver` uses it through `web/uv-resolver.mjs`.

This directory started as the [uv-emscripten](https://github.com/ryanking13/uv-emscripten)
proof of concept (commit `41b63b5`) and is now maintained here. Compared to the PoC, the
resolver takes an index URL and preferences, returns the selected wheel (`url`,
`filename`, `sha256`), and routes fetches through a host hook (`globalThis.__uvFetch`)
that never throws into wasm.

The commands below run from this directory; `../scripts/build-uv.sh` runs setup and build
and copies the result to `../dist/uv/`.

## How it works

```
JS  resolve({ requirements, pythonVersion, pythonPlatform })
 └─ uv_resolve(json)                      C ABI export, JSPI (returns a Promise to JS)
     └─ uv_resolver::Resolver::new_custom_io(.., WasmResolverProvider, ..)
         ├─ get_package_versions  ─┐
         └─ get_wheel_metadata    ─┴─ uv_js_fetch(url)     JSPI import: suspends the wasm stack
                                        └─ fetch()  https://pypi.org/simple/<pkg>/ (PEP 691 JSON)
                                                    https://files.pythonhosted.org/...whl.metadata (PEP 658)
```

1. `uv-resolver` is compiled to `wasm32-unknown-emscripten`.
2. Patched PubGrub solver to run in a single thread.
3. Replaced uv's `reqwest` with a custom `fetch` api based resolver.

## Repository layout

```
crates/uv-resolver-wasm/   The wasm entry crate: provider, C ABI, JSPI fetch bridge (fetch.js), link flags (build.rs)
patches/uv.patch           Changes to uv
patches/vendor/*.diff      Changes made to the vendored crates
vendor/                    crates with small emscripten fixes
web/                       Static demo site
examples/                  Node.js examples
scripts/                   Setup and build scripts
```

## Building

### Prerequisites

- `git`, `rustup`, `python`
- Node.js 25+ to run the examples
- About 15 GB of free disk for the uv build directory

### 1. Setup (once)

```sh
./scripts/setup.sh
```

### 2. Build

```sh
./scripts/build.sh
```

### 3. Run

```sh
node examples/node.mjs      # resolves a few requirement sets, including a conflict
node examples/events.mjs    # prints the live event stream (fetches + resolver decisions)

python3 -m http.server -d dist 8000   # demo site at http://localhost:8000
```

### 4. Deploy (Cloudflare)

`dist/` is deployed as static assets:

```sh
export CLOUDFLARE_ACCOUNT_ID=<your account id>
npx wrangler deploy
```

## JS API

Fetches go through `globalThis.__uvFetch(url, accept)` when the host defines it (it
returns `{ status, body }`), otherwise through `fetch()`.

```js
import { init, isSupported, resolve } from "./uv-resolver.mjs";

isSupported();   // false if WebAssembly JSPI (WebAssembly.Suspending) is unavailable
await init();    // optional: download and instantiate the wasm ahead of time

const result = await resolve({
  requirements: ["fastapi", "pydantic<2"],   // PEP 508 strings
  pythonVersion: "3.12",
  pythonPlatform: "x86_64-manylinux_2_28",   // uv --python-platform values
  indexUrl: "https://pypi.org/simple",      // optional: a PEP 691 JSON index (default PyPI)
  preferences: [{ name: "anyio", version: "4.9.0" }],   // optional: versions to prefer
  onEvent(event) {
    // { type: "fetch-start", id, url, kind: "index" | "metadata" }
    // { type: "fetch-end", id, url, kind, status, bytes, ms, error? }
    // { type: "log", level, target, message }      // uv_resolver tracing (DEBUG)
  },
});
// { ok: true, packages: [{ name, version, dependencies: [...], url?, filename?, sha256? }], roots: [...], elapsedMs }
// { ok: false, error: "<uv's resolution error report>", elapsedMs }
```

## Limitations

- **Resolution only.** No installs, no virtual environments, no interpreter discovery.
- **Wheels with PEP 658 metadata only.** Packages that publish only sdists, or wheels without
  `.metadata` files, are reported as unavailable. No sdist builds, git, path or URL requirements.
- **Sequential fetches.** Each `fetch` suspends the whole wasm stack (JSPI).

## Browser support

JSPI (WebAssembly.Suspending) is required. Browsers without it are detected and shown a notice.

