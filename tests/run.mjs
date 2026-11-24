// Run the scenarios in Pyodide (Node.js) with micropip + micropip-uv-resolver + uv.
//
//   MICROPIP_DIR=/path/to/micropip ./scripts/build-wheels.sh
//   ./scripts/build-uv.sh
//   ./scripts/get-pyodide.sh
//   node tests/run.mjs
//
// PYODIDE_DIR: a Pyodide 314+ distribution (default build/pyodide, from get-pyodide.sh).
// Needs a Node.js with WebAssembly JSPI (Node.js 25+).
import { existsSync, readFileSync, readdirSync } from "node:fs";
import { dirname, join } from "node:path";
import { fileURLToPath } from "node:url";

const root = join(dirname(fileURLToPath(import.meta.url)), "..");
const pyodideDir = process.env.PYODIDE_DIR ?? join(root, "build", "pyodide");
const wheelDir = join(root, "dist", "wheels");
const uvModule = join(root, "dist", "uv", "uv-resolver.mjs");

const { loadPyodide } = await import(join(pyodideDir, "pyodide.mjs"));
// The npm package doesn't contain the lock file packages: load them from the CDN.
// PYODIDE_PACKAGE_BASE_URL overrides where they are loaded from.
let packageBaseUrl = process.env.PYODIDE_PACKAGE_BASE_URL;
const npmManifest = join(pyodideDir, "package.json");
if (!packageBaseUrl && existsSync(npmManifest)) {
  const { version } = JSON.parse(readFileSync(npmManifest, "utf8"));
  packageBaseUrl = `https://cdn.jsdelivr.net/pyodide/v${version}/full/`;
}
// Node.js caches downloaded packages in packageCacheDir, which defaults to
// packageBaseUrl (and would create an "https:" directory here).
const pyodide = await loadPyodide({
  indexURL: pyodideDir,
  packageBaseUrl,
  packageCacheDir: join(root, "build", "pyodide-cache"),
});

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
