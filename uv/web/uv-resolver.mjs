import createUvResolver from "./uv-resolver-wasm.js";

let modulePromise;
let resolutionQueue = Promise.resolve();
let currentOnEvent;
let stderrBuffer = "";

function emitLog(line) {
  if (!line) {
    return;
  }
  const match = /^(TRACE|DEBUG|INFO|WARN|ERROR)\s+(?:(.*?)\s+)?([A-Za-z_][\w:.-]*):\s*(.*)$/.exec(line);
  if (match) {
    currentOnEvent?.({ type: "log", level: match[1], target: match[3], message: match[4] });
    return;
  }
  currentOnEvent?.({ type: "log", level: "INFO", target: "", message: line });
}

function printErr(chunk) {
  const text = String(chunk);
  if (!text.includes("\n")) {
    flushStderr();
    emitLog(text);
    return;
  }
  stderrBuffer += text;
  const lines = stderrBuffer.split(/\r?\n/);
  stderrBuffer = lines.pop();
  for (const line of lines) {
    emitLog(line);
  }
}

function flushStderr() {
  if (stderrBuffer) {
    emitLog(stderrBuffer);
    stderrBuffer = "";
  }
}

async function resolverModule() {
  globalThis.__uvOnFetchEvent ??= (event) => {
    currentOnEvent?.(event);
  };
  modulePromise ??= createUvResolver({
    printErr,
  });
  return modulePromise;
}

export async function init() {
  await resolverModule();
}

export function isSupported() {
  return typeof WebAssembly.Suspending === "function";
}

export async function resolve({
  requirements,
  pythonVersion = "3.12",
  pythonPlatform = "x86_64-manylinux_2_28",
  indexUrl,
  preferences = [],
  onEvent,
}) {
  const task = resolutionQueue.then(async () => {
    const module = await resolverModule();
    const startedAt = performance.now();
    currentOnEvent = onEvent;
    const input = module.stringToNewUTF8(
      JSON.stringify({
        requirements,
        python_version: pythonVersion,
        python_platform: pythonPlatform,
        index_url: indexUrl,
        preferences,
      }),
    );
    try {
      const output = await module._uv_resolve(input);
      try {
        return { ...JSON.parse(module.UTF8ToString(output)), elapsedMs: performance.now() - startedAt };
      } finally {
        module._uv_free(output);
      }
    } catch (error) {
      return { ok: false, error: String(error), elapsedMs: performance.now() - startedAt };
    } finally {
      module._free(input);
      flushStderr();
      currentOnEvent = undefined;
    }
  });
  resolutionQueue = task.catch(() => {});
  return task;
}
