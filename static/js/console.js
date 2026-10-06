// Live console: polling, rendering, filtering and the command box.

const CONSOLE_MAX_LINES = 4000;
const HISTORY_KEY = "mc-manager-cmd-history";
const LEVEL_RANK = { info: 0, event: 0, warn: 1, error: 2 };
let consoleBuffer = [];
let consoleQuery = "";
let consoleLevel = "all";
let cmdHistory = [];
let historyIndex = -1;
let suggestions = [];
let suggestIndex = -1;

try { cmdHistory = JSON.parse(localStorage.getItem(HISTORY_KEY) || "[]").filter(item => typeof item === "string").slice(-50); } catch { cmdHistory = []; }

function updateConsoleControls() {
  const toggle = document.getElementById("console-refresh-toggle");
  const live = document.getElementById("console-live");
  if (toggle) toggle.textContent = consoleAutoRefresh ? "Pause" : "Resume";
  if (toggle) toggle.title = consoleAutoRefresh ? "Pause live updates" : "Resume live updates";
  if (live) live.classList.toggle("paused", !consoleAutoRefresh);
  if (live) live.innerHTML = `<i></i>${consoleAutoRefresh ? "Live" : "Paused"}`;
}

function clearConsole() {
  const output = document.getElementById("console-output");
  if (output) output.textContent = "";
  consoleBuffer = [];
  consoleLineCount = 0;
  updateJumpButton();
}

function consoleLineLevel(text) {
  if (/\b(ERROR|SEVERE|FATAL)\b|Exception|\bat [\w.$]+\(/.test(text)) return "error";
  if (/\bWARN(ING)?\b/.test(text)) return "warn";
  if (/ joined the game| left the game|Done \(|For help, type/.test(text)) return "event";
  return "info";
}

function consoleLineVisible(entry) {
  if (consoleLevel === "warn" && LEVEL_RANK[entry.level] < 1) return false;
  if (consoleLevel === "error" && entry.level !== "error") return false;
  return !consoleQuery || entry.text.toLowerCase().includes(consoleQuery);
}

function appendConsoleLines(out, lines) {
  const fragment = document.createDocumentFragment();
  lines.forEach(text => {
    const entry = { text, level: consoleLineLevel(text) };
    consoleBuffer.push(entry);
    const row = document.createElement("div");
    row.className = `log-line${entry.level === "info" ? "" : ` log-${entry.level}`}`;
    row.textContent = text || " ";
    row.hidden = !consoleLineVisible(entry);
    fragment.append(row);
  });
  out.appendChild(fragment);
  consoleLineCount = consoleBuffer.length;
  // Keep memory and the DOM bounded: a busy server prints thousands of lines an hour.
  if (consoleBuffer.length > CONSOLE_MAX_LINES) {
    const extra = consoleBuffer.length - CONSOLE_MAX_LINES + 500;
    consoleBuffer.splice(0, extra);
    for (let i = 0; i < extra && out.firstChild; i++) out.firstChild.remove();
    consoleLineCount = consoleBuffer.length;
  }
}

function applyConsoleFilter() {
  const out = document.getElementById("console-output");
  if (!out) return;
  const rows = out.children;
  for (let i = 0; i < rows.length; i++) rows[i].hidden = !consoleLineVisible(consoleBuffer[i] || { text: "", level: "info" });
  out.scrollTop = out.scrollHeight;
}

function isConsoleAtEnd(out) {
  return out.scrollHeight - out.clientHeight <= out.scrollTop + 50;
}

function updateJumpButton() {
  const out = document.getElementById("console-output");
  const jump = document.getElementById("console-jump");
  if (out && jump) jump.hidden = isConsoleAtEnd(out);
}

function jumpConsoleToEnd() {
  const out = document.getElementById("console-output");
  out.scrollTo({ top: out.scrollHeight, behavior: "smooth" });
}

function toggleConsoleRefresh() {
  consoleAutoRefresh = !consoleAutoRefresh;
  const settings = getSettings();
  settings.consoleAutoRefresh = consoleAutoRefresh;
  localStorage.setItem("mc-manager-settings", JSON.stringify(settings));
  if (consoleAutoRefresh) startConsolePolling();
  else stopConsolePolling();
  updateConsoleControls();
}

function downloadConsoleLog() {
  const text = consoleBuffer.map(entry => entry.text).join("\n");
  const blob = new Blob([text], { type: "text/plain;charset=utf-8" });
  const link = document.createElement("a");
  link.href = URL.createObjectURL(blob);
  link.download = `${currentServerId || "server"}-console.log`;
  link.click();
  URL.revokeObjectURL(link.href);
}

// ----- command box: history and completion -----

function rememberCommand(command) {
  if (cmdHistory[cmdHistory.length - 1] !== command) cmdHistory.push(command);
  cmdHistory = cmdHistory.slice(-50);
  historyIndex = -1;
  try { localStorage.setItem(HISTORY_KEY, JSON.stringify(cmdHistory)); } catch {}
}

function hideSuggestions() {
  const box = document.getElementById("cmd-suggest");
  if (box) box.hidden = true;
  suggestions = [];
  suggestIndex = -1;
}

function renderSuggestions() {
  const box = document.getElementById("cmd-suggest");
  if (!box) return;
  if (!suggestions.length) return hideSuggestions();
  box.innerHTML = suggestions.map((item, i) =>
    `<button type="button" role="option" class="cmd-option${i === suggestIndex ? " active" : ""}" data-index="${i}"><strong>/${escapeHtml(item.name)}</strong><span>${escapeHtml(item.usage)}</span></button>`).join("");
  box.hidden = false;
}

function updateSuggestions() {
  const input = document.getElementById("cmd-input");
  const typed = input.value.replace(/^\//, "");
  if (!typed || typed.includes(" ")) {
    // Past the command name: show its usage as a hint instead of a list.
    const name = typed.split(" ")[0].toLowerCase();
    const known = typed.includes(" ") ? minecraftCommands.find(command => command[0] === name) : null;
    suggestions = known ? [{ name: known[0], usage: known[2] }] : [];
    suggestIndex = -1;
    return renderSuggestions();
  }
  const lower = typed.toLowerCase();
  suggestions = minecraftCommands.filter(command => command[0].startsWith(lower)).slice(0, 8).map(command => ({ name: command[0], usage: command[2] }));
  suggestIndex = suggestions.length ? 0 : -1;
  renderSuggestions();
}

function acceptSuggestion(index = suggestIndex) {
  const item = suggestions[index];
  if (!item) return false;
  const input = document.getElementById("cmd-input");
  input.value = `${item.name} `;
  hideSuggestions();
  input.focus();
  return true;
}

function onCommandKey(event) {
  const input = event.target;
  const open = suggestions.length > 0 && !document.getElementById("cmd-suggest").hidden;
  if (event.key === "Enter") {
    event.preventDefault();
    sendCmd();
  } else if (event.key === "Tab" && open && input.value && !input.value.includes(" ")) {
    event.preventDefault();
    acceptSuggestion(suggestIndex < 0 ? 0 : suggestIndex);
  } else if (event.key === "Escape") {
    hideSuggestions();
  } else if (event.key === "ArrowDown" || event.key === "ArrowUp") {
    const down = event.key === "ArrowDown";
    if (open && suggestions.length > 1) {
      event.preventDefault();
      suggestIndex = (suggestIndex + (down ? 1 : -1) + suggestions.length) % suggestions.length;
      renderSuggestions();
    } else if (!open || !input.value) {
      event.preventDefault();
      if (!cmdHistory.length) return;
      historyIndex = down ? Math.max(-1, historyIndex - 1) : Math.min(cmdHistory.length - 1, historyIndex + 1);
      input.value = historyIndex < 0 ? "" : cmdHistory[cmdHistory.length - 1 - historyIndex];
    }
  }
}

async function sendCmd() {
  const input = document.getElementById("cmd-input");
  const cmd = input.value.trim().replace(/^\//, "");
  if (!cmd || !currentServerId) return;
  hideSuggestions();
  try {
    const data = await (await fetch(`/api/server/${currentServerId}/command`, {
      method: "POST", headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ command: cmd })
    })).json();
    if (!data.ok) {
      showToast(data.error || data.message || "Command was not sent", "error");
      return;
    }
    rememberCommand(cmd);
    input.value = "";
  } catch (error) {
    showToast(`Command failed: ${error.message}`, "error");
  }
}

function startConsolePolling() {
  stopConsolePolling();
  if (!consoleAutoRefresh) { updateConsoleControls(); return; }
  pollConsole();
  consoleTimer = setInterval(pollConsole, 1200);
}

function stopConsolePolling() {
  if (consoleTimer) { clearInterval(consoleTimer); consoleTimer = null; }
}

async function pollConsole() {
  const serverId = currentServerId;
  if (!serverId) return;
  try {
    const data = await (await fetch(`/api/server/${serverId}/console?since=${consoleOffset}`)).json();
    if (serverId !== currentServerId) return;
    updateStatusBadge(data.running);
    const out = document.getElementById("console-output");
    if (data.reset) clearConsole();
    if (data.lines?.length) {
      const atEnd = isConsoleAtEnd(out);
      appendConsoleLines(out, data.lines);
      if (atEnd) out.scrollTop = out.scrollHeight;
      updateJumpButton();
    }
    if (typeof data.total === "number") consoleOffset = data.total;
  } catch {}
}

function initConsoleControls() {
  const out = document.getElementById("console-output");
  out.addEventListener("scroll", updateJumpButton, { passive: true });
  document.getElementById("console-search").addEventListener("input", event => {
    consoleQuery = event.target.value.trim().toLowerCase();
    applyConsoleFilter();
  });
  document.getElementById("console-levels").addEventListener("click", event => {
    const pill = event.target.closest(".filter-pill");
    if (!pill) return;
    consoleLevel = pill.dataset.level;
    document.querySelectorAll("#console-levels .filter-pill").forEach(p => p.classList.toggle("active", p === pill));
    applyConsoleFilter();
  });
  const input = document.getElementById("cmd-input");
  input.addEventListener("keydown", onCommandKey);
  input.addEventListener("input", () => { historyIndex = -1; updateSuggestions(); });
  input.addEventListener("blur", () => setTimeout(hideSuggestions, 150));
  document.getElementById("cmd-suggest").addEventListener("mousedown", event => {
    const option = event.target.closest(".cmd-option");
    if (option) { event.preventDefault(); acceptSuggestion(Number(option.dataset.index)); }
  });
}

// ----- extra console tools -----

function applyConsoleScale() {
  const scale = Number(getSettings().consoleScale) || 1;
  document.documentElement.style.setProperty("--console-scale", scale);
}

function changeConsoleFont(direction) {
  const settings = getSettings();
  settings.consoleScale = Math.max(0.8, Math.min(1.7, Math.round(((Number(settings.consoleScale) || 1) + direction * 0.1) * 10) / 10));
  try { localStorage.setItem("mc-manager-settings", JSON.stringify(settings)); } catch {}
  applyConsoleScale();
}

async function copyConsole() {
  const visible = consoleBuffer.filter(consoleLineVisible).map(entry => entry.text).join("\n");
  if (!visible) { showToast("Nothing to copy", "warning"); return; }
  await copyText(visible);
}

async function loadConsoleHistory() {
  if (!currentServerId) return;
  try {
    const data = await requestJson(`/api/server/${currentServerId}/logs/latest?lines=500`);
    const first = consoleBuffer[0]?.text;
    const cut = first ? data.lines.lastIndexOf(first) : -1;
    const older = cut >= 0 ? data.lines.slice(0, cut) : data.lines;
    if (!older.length) { showToast("No earlier lines on disk", "warning"); return; }
    const out = document.getElementById("console-output");
    const current = [...consoleBuffer];
    clearConsole();
    appendConsoleLines(out, older);
    appendConsoleLines(out, current.map(entry => entry.text));
    out.scrollTop = 0;
    showToast(`Loaded ${older.length} earlier lines from logs/latest.log`, "success");
  } catch (error) {
    showToast(error.message, "error");
  }
}

async function quickCommand(command) {
  if (!currentServerId) return;
  try {
    const data = await (await fetch(`/api/server/${currentServerId}/command`, {
      method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ command })
    })).json();
    if (!data.ok) showToast(data.error || data.message || "Command was not sent", "error");
  } catch (error) {
    showToast(error.message, "error");
  }
}
