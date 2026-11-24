#!/usr/bin/env bash
# One-time setup: installs emsdk, checks out the pinned uv commit, applies the
# emscripten patch and links crates/uv-resolver-wasm into the uv workspace.
set -euo pipefail

REPO_ROOT="$(cd "$(dirname "$0")/.." && pwd)"
EMSDK_DIR="${EMSDK_DIR:-$REPO_ROOT/emsdk}"
UV_SRC_DIR="${UV_SRC_DIR:-$REPO_ROOT/uv-src}"

UV_REPO="https://github.com/astral-sh/uv.git"
UV_COMMIT="4d33236839c31f2d82416f75e877c0ec545a39a9"
EMSDK_VERSION="6.0.10"

if [ ! -d "$EMSDK_DIR" ]; then
  git clone --depth 1 https://github.com/emscripten-core/emsdk.git "$EMSDK_DIR"
fi
"$EMSDK_DIR/emsdk" install "$EMSDK_VERSION"
"$EMSDK_DIR/emsdk" activate "$EMSDK_VERSION" >/dev/null

if [ ! -d "$UV_SRC_DIR/.git" ]; then
  git init -q "$UV_SRC_DIR"
  git -C "$UV_SRC_DIR" remote add origin "$UV_REPO"
  git -C "$UV_SRC_DIR" fetch -q --depth 1 origin "$UV_COMMIT"
  git -C "$UV_SRC_DIR" checkout -q FETCH_HEAD
  git -C "$UV_SRC_DIR" apply "$REPO_ROOT/patches/uv.patch"
  echo "Applied patches/uv.patch on uv@$UV_COMMIT"
else
  echo "$UV_SRC_DIR already exists; skipping checkout and patch"
fi

# The patched uv Cargo.toml points [patch.crates-io] at ../vendor/<crate>.
if [ "$(cd "$UV_SRC_DIR/.." && pwd)" != "$REPO_ROOT" ]; then
  ln -sfn "$REPO_ROOT/vendor" "$(dirname "$UV_SRC_DIR")/vendor"
fi
ln -sfn "$REPO_ROOT/crates/uv-resolver-wasm" "$UV_SRC_DIR/crates/uv-resolver-wasm"

toolchain="$(sed -n 's/^channel = "\(.*\)"/\1/p' "$UV_SRC_DIR/rust-toolchain.toml")"
rustup toolchain install "$toolchain" --profile minimal --target wasm32-unknown-emscripten

echo "Setup complete. Next: scripts/build.sh"
