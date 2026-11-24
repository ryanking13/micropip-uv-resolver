# Source this file (do not execute it): `source scripts/env.sh`
REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]:-${(%):-%x}}")/.." && pwd)"
export REPO_ROOT
export EMSDK_DIR="${EMSDK_DIR:-$REPO_ROOT/emsdk}"
export UV_SRC_DIR="${UV_SRC_DIR:-$REPO_ROOT/uv-src}"

if [ ! -f "$EMSDK_DIR/emsdk_env.sh" ]; then
  echo "emsdk not found at $EMSDK_DIR; run scripts/setup.sh first" >&2
  return 1 2>/dev/null || exit 1
fi
source "$EMSDK_DIR/emsdk_env.sh" >/dev/null 2>&1

# The emscripten flags live in uv-src/.cargo/config.toml; an exported RUSTFLAGS would override them.
unset RUSTFLAGS

# Optional macOS workaround: if host build scripts fail to link with
# "tapi error: malformed file ... unknown architecture", point SDKROOT at an
# older Command Line Tools SDK, e.g.
#   export UV_EMSCRIPTEN_SDKROOT=/Library/Developer/CommandLineTools/SDKs/MacOSX26.5.sdk
if [ -n "${UV_EMSCRIPTEN_SDKROOT:-}" ]; then
  export SDKROOT="$UV_EMSCRIPTEN_SDKROOT"
fi
