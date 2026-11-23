#!/usr/bin/env bash
# Build the uv resolver wasm (uv-emscripten PoC + uv/micropip.patch) into ./dist/uv.
#
# Environment:
#   UV_EMSCRIPTEN_DIR  uv-emscripten checkout to use (default: ./build/uv-emscripten,
#                      cloned at UV_EMSCRIPTEN_COMMIT if missing)
#   UV_SRC_FROM        an existing uv-emscripten uv-src/ to clone (copy-on-write on APFS)
#                      instead of running setup.sh, which checks out uv and needs ~15 GB
#                      for a fresh build
#   EMSDK_DIR          an existing emsdk to use (passed to uv-emscripten's scripts)
set -euo pipefail

HERE="$(cd "$(dirname "$0")/.." && pwd)"
UV_EMSCRIPTEN_REPO="${UV_EMSCRIPTEN_REPO:-https://github.com/ryanking13/uv-emscripten.git}"
UV_EMSCRIPTEN_COMMIT="${UV_EMSCRIPTEN_COMMIT:-41b63b50200d6f9b5f87059e3351470d1d06336a}"
REPO="${UV_EMSCRIPTEN_DIR:-$HERE/build/uv-emscripten}"
PATCH="$HERE/uv/micropip.patch"

if [ ! -d "$REPO/.git" ]; then
  echo "[build-uv] cloning uv-emscripten@$UV_EMSCRIPTEN_COMMIT into $REPO"
  git clone -q "$UV_EMSCRIPTEN_REPO" "$REPO"
  git -C "$REPO" checkout -q "$UV_EMSCRIPTEN_COMMIT"
fi

if git -C "$REPO" apply --check "$PATCH" 2>/dev/null; then
  echo "[build-uv] applying uv/micropip.patch"
  git -C "$REPO" apply "$PATCH"
elif git -C "$REPO" apply --check --reverse "$PATCH" 2>/dev/null; then
  echo "[build-uv] uv/micropip.patch is already applied"
else
  echo "[build-uv] uv/micropip.patch doesn't apply to $REPO" >&2
  exit 1
fi

if [ ! -d "$REPO/uv-src" ]; then
  if [ -n "${UV_SRC_FROM:-}" ]; then
    echo "[build-uv] cloning uv-src from $UV_SRC_FROM"
    cp -cR "$UV_SRC_FROM" "$REPO/uv-src" 2>/dev/null || cp -R "$UV_SRC_FROM" "$REPO/uv-src"
  else
    (cd "$REPO" && ./scripts/setup.sh)
  fi
fi

(cd "$REPO" && ./scripts/build.sh)

mkdir -p "$HERE/dist/uv"
cp "$REPO"/dist/uv-resolver.mjs "$REPO"/dist/uv-resolver-wasm.js "$REPO"/dist/uv_resolver_wasm.wasm "$HERE/dist/uv/"
echo "[build-uv] built $HERE/dist/uv"
