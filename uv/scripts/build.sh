#!/usr/bin/env bash
# Builds the wasm resolver and assembles the static site in dist/.
set -euo pipefail

source "$(cd "$(dirname "$0")" && pwd)/env.sh"

cd "$UV_SRC_DIR"
cargo build --release -p uv-resolver-wasm --target wasm32-unknown-emscripten

target_dir="${CARGO_TARGET_DIR:-$UV_SRC_DIR/target}/wasm32-unknown-emscripten/release"
dist="$REPO_ROOT/dist"
rm -rf "$dist"
mkdir -p "$dist"
cp "$REPO_ROOT"/web/* "$dist/"
cp "$target_dir/uv-resolver-wasm.js" "$target_dir/uv_resolver_wasm.wasm" "$dist/"

echo "Built $dist ($(du -h "$dist/uv_resolver_wasm.wasm" | cut -f1) wasm)"
