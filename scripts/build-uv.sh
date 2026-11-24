#!/usr/bin/env bash
# Build the uv resolver wasm from ./uv into ./dist/uv.
#
# The first run sets up ./uv (uv/scripts/setup.sh): installs emsdk into uv/emsdk,
# checks out uv at the pinned commit into uv/uv-src and applies uv/patches/uv.patch.
# A fresh build needs ~15 GB of disk.
#
# Environment:
#   EMSDK_DIR   an existing emsdk to use instead of uv/emsdk
#   UV_SRC_DIR  where to check out uv (default: uv/uv-src)
set -euo pipefail

HERE="$(cd "$(dirname "$0")/.." && pwd)"
UV_DIR="$HERE/uv"

"$UV_DIR/scripts/setup.sh"
"$UV_DIR/scripts/build.sh"

mkdir -p "$HERE/dist/uv"
cp "$UV_DIR"/dist/uv-resolver.mjs "$UV_DIR"/dist/uv-resolver-wasm.js "$UV_DIR"/dist/uv_resolver_wasm.wasm "$HERE/dist/uv/"
echo "[build-uv] built $HERE/dist/uv"
