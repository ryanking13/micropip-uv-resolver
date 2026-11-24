const $ = (selector) => document.querySelector(selector);
const timeline = $("#timeline");
const form = $("#resolver-form");
const button = $("#resolve");
const state = { rows: [], queued: [], requests: 0, bytes: 0, selected: 0, started: 0, timer: 0, hovering: false, running: false };
const MAX_ROWS = 2000;

const normalize = (name) => String(name || "").toLowerCase().replace(/[-_.]+/g, "-");
const formatBytes = (bytes) => bytes < 1024 ? `${bytes} B` : `${(bytes / 1024).toFixed(bytes < 10240 ? 1 : 0)} KB`;
const shortUrl = (url) => { try { const value = new URL(url).pathname; return value.includes("/simple/") ? value.split("/").filter(Boolean).pop() : value.split("/").pop() || url; } catch { return String(url); } };

function element(tag, className, text) { const node = document.createElement(tag); if (className) node.className = className; if (text !== undefined) node.textContent = text; return node; }
function updateMetrics() { $("#elapsed").textContent = `${Math.round(performance.now() - state.started)} ms`; $("#requests").textContent = state.requests; $("#bytes").textContent = formatBytes(state.bytes); $("#selected").textContent = state.selected; }
function enqueue(row) { state.queued.push(row); if (!state.frame) state.frame = requestAnimationFrame(flushRows); }
function flushRows() {
  state.frame = 0;
  if (!state.queued.length) return;
  const fragment = document.createDocumentFragment();
  for (const row of state.queued.splice(0)) { fragment.append(row); state.rows.push(row); }
  timeline.append(fragment);
  while (state.rows.length > MAX_ROWS) state.rows.shift().remove();
  if (!state.hovering) timeline.scrollTop = timeline.scrollHeight;
}
function logRow(message) {
  const lower = String(message).toLowerCase();
  const type = lower.startsWith("selecting:") ? "select" : lower.startsWith("searching") ? "search" : lower.includes("dependency") ? "dependency" : lower.includes("backtrack") || lower.includes("no compatible") ? "conflict" : "other";
  const icon = type === "select" ? "✓" : type === "search" ? "⌕" : type === "dependency" ? "+" : type === "conflict" ? "!" : "·";
  const row = element("div", `event event-${type}`); row.append(element("span", "event-icon", icon), element("span", "event-message", String(message)), element("span", "event-meta", type === "select" ? "chosen" : "resolver")); enqueue(row);
  if (type === "select") { state.selected += 1; updateMetrics(); }
}
function requestRow(event) {
  if (event.type !== "fetch-end") return;
  state.requests += 1; state.bytes += Number(event.bytes) || 0; updateMetrics();
  const row = element("div", "request");
  row.append(element("span", `request-badge ${event.kind === "metadata" ? "metadata" : ""}`, String(event.kind || "index").toUpperCase()), element("span", "request-name", shortUrl(event.url)), element("span", "request-meta", `${event.status || "—"} · ${formatBytes(Number(event.bytes) || 0)} · ${Math.round(Number(event.ms) || 0)}ms`)); enqueue(row);
}
function resetProcess() { state.rows.forEach((row) => row.remove()); state.rows = []; state.queued = []; state.requests = 0; state.bytes = 0; state.selected = 0; state.started = performance.now(); updateMetrics(); }
function eventHandler(event) { if (!event || typeof event !== "object") return; if (event.type === "log") logRow(event.message); else if (event.type === "fetch-end") requestRow(event); }
function viaLines(pkg, packages, roots) {
  const parents = packages.filter((candidate) => candidate.dependencies.map(normalize).includes(normalize(pkg.name))).map((candidate) => candidate.name);
  return parents.length ? `# via ${parents.join(", ")}` : roots.map(normalize).includes(normalize(pkg.name)) ? "# requested" : "# resolved";
}
function makeTree(packages, roots) {
  const byName = new Map(packages.map((pkg) => [normalize(pkg.name), pkg]));
  const build = (name, ancestry = new Set()) => {
    const pkg = byName.get(normalize(name)); const li = element("li"); if (!pkg) { li.append(element("span", "tree-node static", name)); return li; }
    const children = pkg.dependencies.filter((child) => byName.has(normalize(child)) && !ancestry.has(normalize(child)));
    const node = element("button", `tree-node${children.length ? "" : " static"}`); if (children.length) { node.type = "button"; node.append(element("span", "tree-caret", "⌄")); node.addEventListener("click", () => { li.classList.toggle("collapsed"); node.firstChild.textContent = li.classList.contains("collapsed") ? "›" : "⌄"; }); } else node.append(element("span", "tree-caret", "·")); node.append(element("span", "", pkg.name), element("span", "tree-version", `==${pkg.version}`)); li.append(node);
    if (children.length) { const list = element("ul"); const next = new Set(ancestry); next.add(normalize(pkg.name)); children.forEach((child) => list.append(build(child, next))); li.append(list); }
    return li;
  };
  const list = element("ul"); roots.forEach((root) => list.append(build(root))); return list;
}
function showSuccess(result) {
  const wrapper = element("div", "result-success"); const outputCard = element("section", "result-card"); const header = element("div", "result-header"); const heading = element("div"); heading.append(element("h2", "", "Resolved packages")); const copy = element("button", "copy-button", "Copy requirements.txt"); copy.type = "button"; const text = result.packages.map((pkg) => `${pkg.name}==${pkg.version}\n    ${viaLines(pkg, result.packages, result.roots)}`).join("\n"); copy.addEventListener("click", async () => { try { await navigator.clipboard.writeText(result.packages.map((pkg) => `${pkg.name}==${pkg.version}`).join("\n") + "\n"); copy.textContent = "Copied"; setTimeout(() => { copy.textContent = "Copy requirements.txt"; }, 1600); } catch { copy.textContent = "Copy unavailable"; } }); header.append(heading, copy); outputCard.append(header, element("p", "result-summary", `${result.packages.length} packages · ${Math.round(Number(result.elapsedMs) || 0)} ms`), element("pre", "compiled-output", text)); const treeCard = element("section", "result-card"); treeCard.append(element("h2", "", "Dependency tree")); const tree = element("div", "tree"); tree.append(makeTree(result.packages, result.roots)); treeCard.append(tree); wrapper.append(outputCard, treeCard); $("#results").replaceChildren(wrapper);
}
function showFailure(result) { const wrapper = element("div", "result-failure"); const card = element("section", "result-card"); card.append(element("h2", "", "No solution found"), element("pre", "error-report", result.error || "Unknown resolver error.")); wrapper.append(card); $("#results").replaceChildren(wrapper); }
function setRunning(running) { state.running = running; form.classList.toggle("is-running", running); button.disabled = running || !window.resolverSupported; $("#process-state").textContent = running ? "Resolving live" : "Complete"; $("#process-state").className = `status ${running ? "running" : "ready"}`; if (running) { state.timer = window.setInterval(updateMetrics, 50); } else { window.clearInterval(state.timer); updateMetrics(); } }
async function boot() {
  try {
    const resolver = await import("./uv-resolver.mjs");
    window.resolverSupported = resolver.isSupported();
    if (!window.resolverSupported) { $("#unsupported").hidden = false; $("#support-note").textContent = "Resolution is disabled because this browser does not support JSPI."; $("#ready-status").textContent = "JSPI unavailable"; $("#ready-status").className = "status"; button.disabled = true; return; }
    await resolver.init(); window.resolver = resolver; $("#ready-status").textContent = "Ready"; $("#ready-status").className = "status ready";
  } catch (error) { window.resolverSupported = false; button.disabled = true; $("#ready-status").textContent = "Unable to load resolver"; $("#ready-status").className = "status"; $("#support-note").textContent = `Resolver load failed: ${error instanceof Error ? error.message : "unknown error"}`; }
}
document.querySelectorAll(".preset").forEach((preset) => preset.addEventListener("click", () => { $("#requirements").value = preset.dataset.requirements || ""; $("#requirements").focus(); }));
$("#platform").addEventListener("change", () => { const pinned = $("#platform").selectedOptions[0]?.dataset.python; if (pinned) $("#python-version").value = pinned; $("#python-version").disabled = Boolean(pinned); });
timeline.addEventListener("mouseenter", () => { state.hovering = true; }); timeline.addEventListener("mouseleave", () => { state.hovering = false; });
form.addEventListener("submit", async (event) => { event.preventDefault(); if (!window.resolver || state.running) return; const requirements = $("#requirements").value.split("\n").map((line) => line.trim()).filter(Boolean); if (!requirements.length) { $("#support-note").textContent = "Enter at least one requirement before resolving."; return; } resetProcess(); $("#results").replaceChildren(); setRunning(true); try { const result = await window.resolver.resolve({ requirements, pythonVersion: $("#python-version").value, pythonPlatform: $("#platform").value, onEvent: eventHandler }); flushRows(); result && result.ok ? showSuccess(result) : showFailure(result || { error: "Resolver did not return a result." }); } catch (error) { showFailure({ error: error instanceof Error ? error.message : "Unexpected resolver failure." }); } finally { setRunning(false); } });
boot();
