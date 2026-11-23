// Run the scenarios in Pyodide (Node.js) with micropip + micropip-uv-resolver + uv.
//
//   MICROPIP_DIR=/path/to/micropip ./scripts/build-wheels.sh
//   ./scripts/build-uv.sh
//   node tests/run.mjs
//
// PYODIDE_DIR: a Pyodide distribution (default /tmp/pyodide-dl/pyodide).
// Needs a Node.js with WebAssembly JSPI (Node.js 25+).
import { readFileSync, readdirSync } from "node:fs";
import { dirname, join } from "node:path";
import { fileURLToPath } from "node:url";

const root = join(dirname(fileURLToPath(import.meta.url)), "..");
const pyodideDir = process.env.PYODIDE_DIR ?? "/tmp/pyodide-dl/pyodide";
const wheelDir = join(root, "dist", "wheels");
const uvModule = join(root, "dist", "uv", "uv-resolver.mjs");

const { loadPyodide } = await import(join(pyodideDir, "pyodide.mjs"));
const pyodide = await loadPyodide({ indexURL: pyodideDir });

const wheels = readdirSync(wheelDir).filter((f) => f.endsWith(".whl"));
const micropipWheel = wheels.find((f) => f.startsWith("micropip-"));
const pluginWheel = wheels.find((f) => f.startsWith("micropip_uv_resolver-"));
if (!micropipWheel || !pluginWheel) {
  throw new Error(`run scripts/build-wheels.sh first (found: ${wheels})`);
}
// micropip first, so the version from main replaces the one in Pyodide's lock file
await pyodide.loadPackage(join(wheelDir, micropipWheel));
await pyodide.loadPackage(join(wheelDir, pluginWheel));

pyodide.globals.set("UV_MODULE", uvModule);
const script = readFileSync(join(root, "tests", process.argv[2] ?? "scenarios.py"), "utf8");
await pyodide.runPythonAsync(script);
