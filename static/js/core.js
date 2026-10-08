// Shared state, settings, toasts, dialogs and small helpers used by every other file.

// Any 401 from the API means the session ended (signed out, password reset, account removed).
const nativeFetch = window.fetch.bind(window);
window.fetch = async (...args) => {
  const response = await nativeFetch(...args);
  const url = String(args[0]?.url || args[0] || "");
  if (response.status === 401 && !url.startsWith("/api/auth/")) window.location.reload();
  return response;
};

let currentServerId = null;

let consoleOffset = 0;

let consoleTimer = null;

let fsPath = "";

let fsEditPath = null;

let pluginConfigPath = null;

let serverFilter = "all";

let settingsRefreshTimer = null;

let pingRefreshTimer = null;

let detailPingTimer = null;

let detailPingInFlight = false;

let consoleAutoRefresh = true;

let createLogo = { mark: "MC", style: "avatar-lime", library: "", libraryUrl: "" };

let backupJobTimer = null;

let serversHtml = "";

let serversSettled = false;

let summaryKey = "";

let consoleLineCount = 0;
let currentServerRam = 2048;
let currentServerPort = 25565;
let currentServerVersion = "";
let knownServers = [];

const IS_OWNER = document.body.dataset.role === "owner";
const PERMISSIONS = (document.body.dataset.perms || "").split(" ").filter(Boolean);
const APP_VERSION = document.body.dataset.version || "";

// The server enforces permissions; this only decides what to offer.
function can(permission) {
  return IS_OWNER || PERMISSIONS.includes(permission);
}

const OWNER_WATERMARK = "MC-SERVER-MANAGER / Mrkraps aka orgeco";

const prefersReducedMotion = window.matchMedia?.("(prefers-reduced-motion: reduce)").matches;
const defaultSettings = { theme: "control", font: "dm", accent: "lime", density: "comfortable", motion: !prefersReducedMotion, confirmActions: true, autoRefresh: true, refreshInterval: "30", autoPing: true, pingInterval: "5", consoleAutoRefresh: true, backdrop3d: true };

function getSettings() {
  try { return { ...defaultSettings, ...JSON.parse(localStorage.getItem("mc-manager-settings") || "{}") }; }
  catch { return { ...defaultSettings }; }
}

function applySettings() {
  const settings = getSettings();
  document.body.dataset.accent = settings.accent;
  document.body.dataset.theme = settings.theme === "auto"
    ? (window.matchMedia?.("(prefers-color-scheme: light)").matches ? "light" : "control")
    : settings.theme;
  document.body.dataset.font = settings.font;
  document.body.classList.toggle("density-compact", settings.density === "compact");
  document.body.classList.toggle("reduced-motion", !settings.motion);
  consoleAutoRefresh = settings.consoleAutoRefresh !== false;
  updateConsoleControls();
  document.querySelectorAll(".setting-input").forEach(input => {
    const value = settings[input.dataset.setting];
    if (input.type === "checkbox") input.checked = Boolean(value);
    else if (value !== undefined) input.value = value;
  });
  renderAccentPicker(settings.accent);
  if (settingsRefreshTimer) clearInterval(settingsRefreshTimer);
  settingsRefreshTimer = settings.autoRefresh ? setInterval(() => {
    if (document.getElementById("tab-servers")?.classList.contains("active")) loadServers();
  }, Number(settings.refreshInterval) * 1000) : null;
  if (pingRefreshTimer) clearInterval(pingRefreshTimer);
  pingRefreshTimer = settings.autoPing ? setInterval(() => {
    if (document.getElementById("tab-servers")?.classList.contains("active")) refreshServerPingsFromCards();
  }, Number(settings.pingInterval) * 1000) : null;
  if (currentServerId && document.getElementById("tab-detail")?.classList.contains("active")) startDetailPing(currentServerId);
  window.mcScene?.applyPreferences({ enabled: settings.backdrop3d, motion: settings.motion });
}

// The accent colour is chosen from a grid of swatches; the hidden select next to it is what the settings code reads and saves.
function renderAccentPicker(current) {
  const select = document.querySelector('.setting-input[data-setting="accent"]');
  const grid = document.getElementById("accent-grid");
  if (!select || !grid) return;
  if (!grid.children.length) {
    grid.innerHTML = [...select.options].map(option => `<button type="button" class="accent-swatch" role="radio" data-accent-key="${escapeHtml(option.value)}" style="--swatch:${escapeHtml(option.dataset.color || "#a3e635")}" title="${escapeHtml(option.textContent)}" aria-label="${escapeHtml(option.textContent)}"></button>`).join("");
    grid.addEventListener("click", event => {
      const swatch = event.target.closest("[data-accent-key]");
      if (!swatch) return;
      select.value = swatch.dataset.accentKey;
      select.dispatchEvent(new Event("change", { bubbles: true }));
    });
  }
  const chosen = [...select.options].find(option => option.value === current) || select.options[0];
  grid.querySelectorAll("[data-accent-key]").forEach(swatch => {
    const active = swatch.dataset.accentKey === chosen.value;
    swatch.classList.toggle("active", active);
    swatch.setAttribute("aria-checked", String(active));
  });
  const name = document.getElementById("accent-name");
  if (name) name.textContent = `${chosen.textContent}. ${select.options.length} colors to choose from.`;
}

function initSettings() {
  applySettings();
  document.querySelectorAll(".setting-input").forEach(input => input.addEventListener("change", () => {
    const settings = getSettings();
    settings[input.dataset.setting] = input.type === "checkbox" ? input.checked : input.value;
    localStorage.setItem("mc-manager-settings", JSON.stringify(settings));
    applySettings();
  }));
}

function resetSettings() {
  localStorage.removeItem("mc-manager-settings");
  applySettings();
  showToast("Preferences reset", "success");
}

const TOAST_ICONS = {
  success: '<svg viewBox="0 0 20 20" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M4.5 10.5l3.5 3.5 7.5-8"/></svg>',
  error: '<svg viewBox="0 0 20 20" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M5.5 5.5l9 9M14.5 5.5l-9 9"/></svg>',
  warning: '<svg viewBox="0 0 20 20" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M10 6v5M10 14.2v.1"/></svg>'
};

function showToast(message, type = "success") {
  const container = document.getElementById("toast-container");
  if (!container) return;
  while (container.children.length >= 4) container.firstElementChild.remove();
  const life = type === "error" ? 6500 : 3800;
  const toast = document.createElement("div");
  toast.className = `toast ${type}`;
  toast.setAttribute("role", type === "error" ? "alert" : "status");
  toast.style.setProperty("--life", `${life}ms`);
  toast.innerHTML = `<span class="toast-icon">${TOAST_ICONS[type] || TOAST_ICONS.success}</span><span class="toast-text"></span>`;
  toast.querySelector(".toast-text").textContent = message;
  const dismiss = () => {
    if (toast.classList.contains("leaving")) return;
    toast.classList.add("leaving");
    setTimeout(() => toast.remove(), 260);
  };
  toast.addEventListener("click", dismiss);
  container.appendChild(toast);
  setTimeout(dismiss, life);
}

function escapeHtml(str) {
  return String(str).replace(/[&<>"']/g, m => ({ "&":"&amp;","<":"&lt;",">":"&gt;",'"':"&quot;","'":"&#39;" }[m]));
}

// Modal replacement for confirm() and prompt(). Resolves true/false, or the typed text when `input` is set (null if cancelled).
function uiAsk({ title = "Are you sure?", message = "", confirmText = "OK", danger = false, input = null } = {}) {
  const dialog = document.getElementById("ask-dialog");
  const field = document.getElementById("ask-input");
  const ok = document.getElementById("ask-ok");
  document.getElementById("ask-title").textContent = title;
  document.getElementById("ask-message").textContent = message;
  ok.textContent = confirmText;
  ok.className = `btn ${danger ? "danger" : "primary"}`;
  field.hidden = !input;
  field.value = input?.value || "";
  field.placeholder = input?.placeholder || "";
  field.type = input?.type || "text";
  field.required = Boolean(input?.required);
  return new Promise(resolve => {
    dialog.addEventListener("close", () => {
      if (dialog.returnValue !== "ok") return resolve(input ? null : false);
      resolve(input ? field.value : true);
    }, { once: true });
    dialog.showModal();
    (input ? field : ok).focus();
  });
}

// Honours the "Confirm destructive actions" preference.
async function uiConfirm(message, options = {}) {
  if (!getSettings().confirmActions && !options.always) return true;
  return uiAsk({ message, ...options });
}

async function copyText(text) {
  try {
    await navigator.clipboard.writeText(text);
    showToast("Copied", "success");
  } catch {
    showToast("Could not copy. Select the text and copy it manually.", "warning");
  }
}

function formatUptime(seconds) {
  const s = Math.max(0, Math.floor(Number(seconds) || 0));
  if (s < 60) return `${s}s`;
  const m = Math.floor(s / 60);
  if (m < 60) return `${m}m ${s % 60}s`;
  const h = Math.floor(m / 60);
  if (h < 48) return `${h}h ${m % 60}m`;
  return `${Math.floor(h / 24)}d ${h % 24}h`;
}

// Safe for use inside an inline onclick="fn(...)": JSON gives a valid JS string literal, escapeHtml makes it attribute-safe.
function jsArg(value) {
  return escapeHtml(JSON.stringify(String(value)));
}

async function withBusy(button, task) {
  if (!button || button.classList.contains("is-busy")) return undefined;
  button.classList.add("is-busy");
  button.disabled = true;
  try { return await task(); }
  finally { button.classList.remove("is-busy"); button.disabled = false; }
}

async function requestJson(url, options = {}) {
  const response = await fetch(url, options);
  const body = await response.text();
  if (!(response.headers.get("content-type") || "").includes("json")) {
    const detail = body.replace(/<[^>]*>/g, " ").replace(/\s+/g, " ").trim().slice(0, 160);
    throw new Error(`The manager returned ${response.status} ${response.statusText || "error"}${detail ? ` - ${detail}` : ""}`);
  }
  let data;
  try { data = JSON.parse(body); } catch { throw new Error("The manager sent a malformed response"); }
  if (!response.ok && data.error) throw new Error(data.error);
  return data;
}

function rememberView(name) {
  try { sessionStorage.setItem("mc-manager-view", JSON.stringify({ tab: name, server: name === "detail" ? currentServerId : null })); } catch {}
}

// After a reload (new version, F5) put the user back where they were.
function restoreView() {
  let view = null;
  try { view = JSON.parse(sessionStorage.getItem("mc-manager-view") || "null"); } catch {}
  if (!view || view.tab === "servers") return;
  const target = document.getElementById(`tab-${view.tab}`);
  if (!target || (target.hasAttribute("data-owner-only") && !IS_OWNER)) return;
  if (view.tab === "detail" && view.server) openServer(view.server);
  else if (document.getElementById(`tab-${view.tab}`)) switchTab(view.tab);
}

function switchTab(name) {
  rememberView(name);
  if (name !== "detail") { stopDetailPing(); stopBackupPolling(); }
  document.querySelectorAll(".tab").forEach(t => t.classList.remove("active"));
  document.querySelectorAll(".nav-btn").forEach(b => b.classList.remove("active"));
  const tab = document.getElementById("tab-" + name);
  if (tab) tab.classList.add("active");
  window.scrollTo({ top: 0 });
  const btn = document.querySelector(`.nav-btn[data-tab="${name}"]`);
  if (btn) btn.classList.add("active");
  if (name === "servers") { serversSettled = false; loadServers(); }
  if (name === "staff") loadStaff();
  if (name === "create") { loadVersions(); loadHostMemory(); }
  if (name === "settings") loadSettingsTab();
  if (name === "map") window.mcMap?.onShow();
  else window.mcMap?.onHide();
  if (name === "browser") {
    loadServerSelect();
    loadFeatured();
  }
}

function formatBytes(bytes) {
  const units = ["B", "KB", "MB", "GB"];
  let value = Number(bytes) || 0;
  let unit = 0;
  while (value >= 1024 && unit < units.length - 1) { value /= 1024; unit += 1; }
  return `${value < 10 && unit ? value.toFixed(1) : Math.round(value)} ${units[unit]}`;
}

function setTaskProgress(prefix, percent, text) {
  const wrapper = document.getElementById(`${prefix}-progress`);
  if (!wrapper) return;
  wrapper.hidden = false;
  const fill = document.getElementById(`${prefix}-bar-fill`);
  const clamped = Math.max(0, Math.min(100, percent));
  fill.style.width = `${clamped}%`;
  fill.classList.toggle("active", clamped < 100);
  document.getElementById(`${prefix}-progress-text`).textContent = text;
}

function hideTaskProgress(prefix) {
  const wrapper = document.getElementById(`${prefix}-progress`);
  if (wrapper) wrapper.hidden = true;
  const fill = document.getElementById(`${prefix}-bar-fill`);
  if (fill) fill.classList.remove("active");
}

function relativeTime(iso) {
  if (!iso) return "never";
  const seconds = Math.max(0, (Date.now() - new Date(iso).getTime()) / 1000);
  if (seconds < 60) return "just now";
  if (seconds < 3600) return `${Math.floor(seconds / 60)} min ago`;
  if (seconds < 86400) return `${Math.floor(seconds / 3600)} h ago`;
  return new Date(iso).toLocaleDateString();
}

// Press ripple and cursor spotlight. Pure decoration, skipped when motion is off.
const RIPPLE_SELECTOR = ".btn, .mini-btn, .nav-btn, .dtab, .filter-pill, .ram-preset, .dim-tab, .console-tool, .icon-btn";

const SPOTLIGHT_SELECTOR = ".server-card, .summary-pill, .panel-card, .settings-card, .info-card, .featured-item, .result-card, .form-section, .create-commit";

let spotlightFrame = null;

function tickLiveClock() {
  const el = document.getElementById("live-clock");
  if (el) el.textContent = new Date().toLocaleTimeString([], { hour12: false });
}


// ----- sidebar, title, shortcuts -----

function toggleSidebar(force) {
  const collapsed = force ?? !document.body.classList.contains("sidebar-collapsed");
  document.body.classList.toggle("sidebar-collapsed", collapsed);
  const button = document.querySelector(".sidebar-toggle");
  if (button) {
    button.setAttribute("aria-label", collapsed ? "Expand sidebar" : "Collapse sidebar");
    button.title = collapsed ? "Expand sidebar" : "Collapse sidebar";
  }
  try { localStorage.setItem("mc-manager-sidebar", collapsed ? "1" : "0"); } catch {}
}

function restoreSidebar() {
  try { if (localStorage.getItem("mc-manager-sidebar") === "1") toggleSidebar(true); } catch {}
}

let faviconBase = null;

// Browser tab title and icon show whether anything is running, so a pinned tab is useful at a glance.
function updateTitle(online) {
  document.title = `${online ? `(${online} online) ` : ""}MC Server Manager v2`;
  const icon = document.getElementById("favicon");
  if (!icon) return;
  if (!faviconBase) {
    faviconBase = new Image();
    faviconBase.onload = () => updateTitle(online);
    faviconBase.src = "/static/img/favicon.png";
    return;
  }
  if (!faviconBase.complete || !faviconBase.naturalWidth) return;
  // the logo, plus a green dot in the corner while any server is running
  const canvas = document.createElement("canvas");
  canvas.width = canvas.height = 64;
  const context = canvas.getContext("2d");
  context.drawImage(faviconBase, 0, 0, 64, 64);
  if (online) {
    context.fillStyle = "#a3e635";
    context.strokeStyle = "#0b0b0e";
    context.lineWidth = 5;
    context.beginPath();
    context.arc(50, 50, 12, 0, Math.PI * 2);
    context.stroke();
    context.fill();
  }
  icon.href = canvas.toDataURL("image/png");
}

function openShortcuts() {
  document.getElementById("shortcuts-dialog").showModal();
}

const TAB_KEYS = { s: "servers", c: "create", m: "map", p: "browser", t: "settings" };
let pendingGo = 0;

function typingInField(event) {
  const el = event.target;
  return el instanceof HTMLElement && (el.isContentEditable || ["INPUT", "TEXTAREA", "SELECT"].includes(el.tagName));
}

document.addEventListener("keydown", event => {
  if ((event.ctrlKey || event.metaKey) && event.key.toLowerCase() === "k") {
    event.preventDefault();
    openPalette();
    return;
  }
  if ((event.ctrlKey || event.metaKey) && event.key.toLowerCase() === "s" && document.getElementById("fs-editor")?.style.display === "block") {
    event.preventDefault();
    fsSave();
    return;
  }
  if (typingInField(event) || event.ctrlKey || event.metaKey || event.altKey || document.querySelector("dialog[open]")) return;
  if (Date.now() < pendingGo && TAB_KEYS[event.key]) {
    pendingGo = 0;
    const tab = TAB_KEYS[event.key];
    const target = document.getElementById(`tab-${tab}`);
    if (target && !(tab === "create" && !can("manage"))) switchTab(tab);
    return;
  }
  if (event.key === "g") { pendingGo = Date.now() + 1200; return; }
  if (event.key === "/") {
    const search = document.getElementById("server-search");
    if (search && document.getElementById("tab-servers").classList.contains("active")) { event.preventDefault(); search.focus(); }
  } else if (event.key === "?") {
    openShortcuts();
  } else if (event.key === "[") {
    toggleSidebar();
  }
});

window.matchMedia?.("(prefers-color-scheme: light)").addEventListener?.("change", () => { if (getSettings().theme === "auto") applySettings(); });
