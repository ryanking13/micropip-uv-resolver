#!/usr/bin/env bash
# Download the Pyodide npm package that the tests run on into ./build/pyodide.
# It doesn't contain the lock file packages; tests/run.mjs loads those from the CDN.
set -euo pipefail

HERE="$(cd "$(dirname "$0")/.." && pwd)"
PYODIDE_VERSION="${PYODIDE_VERSION:-314.0.8}"
DEST="$HERE/build/pyodide"

rm -rf "$DEST"
mkdir -p "$DEST"
(cd "$DEST" && npm pack -s "pyodide@$PYODIDE_VERSION" >/dev/null && tar xzf "pyodide-$PYODIDE_VERSION.tgz" --strip-components 1 && rm "pyodide-$PYODIDE_VERSION.tgz")
echo "[get-pyodide] Pyodide $PYODIDE_VERSION in $DEST"
