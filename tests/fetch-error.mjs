// The uv module must stay usable after the host fetch throws.
//
//   ./scripts/build-uv.sh
//   node tests/fetch-error.mjs
//
// Runs the uv module directly (no Pyodide) against PyPI. Needs a Node.js with
// WebAssembly JSPI (Node.js 25+).
//
// A JS exception thrown into wasm unwinds the Rust frames without running
// their destructors (uv is built with panic = "abort"). Before fetch.js caught
// it, futures' block_on stayed marked as entered, so the next resolution
// panicked ("cannot execute `LocalPool` executor from within another
// executor"), the module aborted, and every later call failed with
// "RuntimeError: unreachable".
import assert from "node:assert/strict";
import { dirname, join } from "node:path";
import { fileURLToPath } from "node:url";

const root = join(dirname(fileURLToPath(import.meta.url)), "..");
const uv = await import(join(root, "dist", "uv", "uv-resolver.mjs"));
assert.ok(uv.isSupported(), "WebAssembly JSPI is unavailable");

const panics = [];
const onEvent = (event) => {
  if (event.type === "log" && /panicked|Aborted/.test(event.message)) {
    panics.push(event.message);
  }
};

function resolveSix() {
  return uv.resolve({
    requirements: ["six"],
    pythonVersion: "3.14",
    indexUrl: "https://pypi.org/simple",
    onEvent,
  });
}

globalThis.__uvFetch = async () => {
  throw new Error("host fetch failed");
};
let output;
try {
  output = await resolveSix();
} finally {
  delete globalThis.__uvFetch;
}
console.log("host fetch throws ->", output.ok ? "ok" : output.error);
assert.equal(output.ok, false, "resolving without any successful fetch should fail");

output = await resolveSix();
console.log("next resolution ->", output.ok ? "ok" : output.error);
assert.equal(output.ok, true, output.error);
assert.deepEqual(
  output.packages.map((p) => p.name),
  ["six"],
);
assert.deepEqual(panics, []);

console.log("fetch-error test passed");
