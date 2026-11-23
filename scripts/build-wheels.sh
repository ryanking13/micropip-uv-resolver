#!/usr/bin/env bash
# Build the wheels the tests load into Pyodide, into ./dist/wheels:
#   - micropip, from MICROPIP_DIR (a micropip checkout at main; needs its
#     vendored packaging submodule populated)
#   - micropip-uv-resolver, from this repository
set -euo pipefail

HERE="$(cd "$(dirname "$0")/.." && pwd)"
: "${MICROPIP_DIR:?set MICROPIP_DIR to a micropip checkout at main}"

rm -rf "$HERE/dist/wheels"
mkdir -p "$HERE/dist/wheels"
(cd "$MICROPIP_DIR" && uv build -q --wheel -o "$HERE/dist/wheels" .)
(cd "$HERE" && uv build -q --wheel -o "$HERE/dist/wheels" .)
ls "$HERE/dist/wheels"
