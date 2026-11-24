# Vendored crates

Unmodified crates.io sources plus small emscripten fixes. They are wired in through
`[patch.crates-io]` in the patched uv `Cargo.toml` (`patches/uv.patch`), which refers to
them as `../vendor/<crate>`. The exact changes against the published crates are in
`patches/vendor/<crate>.diff`.

| Crate | Version | Why it needed a change |
|---|---|---|
| `reqsign-core` | 3.3.1 | Emscripten matches both `cfg(unix)` and `cfg(target_arch = "wasm32")`, so `home_dir` was defined twice. The Unix branch now excludes wasm32. The `jwt` module was hidden on wasm32 even though its dependencies build, so it is enabled there (and by default). |
| `astral-tokio-tar` | 0.7.0 | Same `unix` + `wasm32` overlap produced duplicate helpers. Unix-only implementations now exclude wasm32. |
| `astral_async_http_range_reader` | 0.11.1 | Spawned its streamer with `tokio::spawn`, which requires `Send` futures. On emscripten (single-threaded tokio, `?Send` middleware) it uses `tokio::task::spawn_local`. |
| `wasmtimer` | 0.4.3 | Newer wasm-bindgen requires `UnwindSafe` closures for `Closure::once_into_js`; callbacks are wrapped in `AssertUnwindSafe`. |

All four are licensed MIT and/or Apache-2.0; their license files are kept in each directory.
