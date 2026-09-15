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
let devReloadVersion = null;
let createLogo = { mark: "MC", style: "avatar-lime" };
let backupJobTimer = null;
const OWNER_WATERMARK = "MC-SERVER-MANAGER / Mrkraps aka orgeco";
const defaultSettings = { theme: "control", font: "dm", accent: "lime", density: "comfortable", motion: true, confirmActions: true, autoRefresh: true, refreshInterval: "30", autoPing: true, pingInterval: "5", consoleAutoRefresh: true, backdrop3d: true };

function getSettings() {
  try { return { ...defaultSettings, ...JSON.parse(localStorage.getItem("mc-manager-settings") || "{}") }; }
  catch { return { ...defaultSettings }; }
}

function applySettings() {
  const settings = getSettings();
  document.body.dataset.accent = settings.accent;
  document.body.dataset.theme = settings.theme;
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

function initSettings() {
  applySettings();
  document.querySelectorAll(".setting-input").forEach(input => input.addEventListener("change", () => {
    const settings = getSettings();
    settings[input.dataset.setting] = input.type === "checkbox" ? input.checked : input.value;
    localStorage.setItem("mc-manager-settings", JSON.stringify(settings));
    applySettings();
  }));
}

async function watchDevelopmentFiles() {
  try {
    const response = await fetch("/api/dev-version", { cache: "no-store" });
    const data = await response.json();
    if (!data.enabled) return;
    if (devReloadVersion === null) devReloadVersion = data.version;
    else if (devReloadVersion !== data.version) window.location.reload();
  } catch {}
}

function resetSettings() {
  localStorage.removeItem("mc-manager-settings");
  applySettings();
  showToast("Preferences reset", "success");
}

function updateCreateLogo() {
  const name = document.getElementById("create-name")?.value.trim() || "MC";
  const preview = document.getElementById("create-logo-preview");
  if (!preview) return;
  createLogo.mark = name.slice(0, 2).toUpperCase();
  preview.textContent = createLogo.mark;
  preview.className = `server-avatar ${createLogo.style}`;
}

function shuffleServerLogo() {
  const styles = ["avatar-lime", "avatar-blue", "avatar-amber", "avatar-red"];
  const marks = ["MC", "XP", "GG", "24", "OP", "SV"];
  createLogo.style = styles[Math.floor(Math.random() * styles.length)];
  createLogo.mark = marks[Math.floor(Math.random() * marks.length)];
  const preview = document.getElementById("create-logo-preview");
  preview.textContent = createLogo.mark;
  preview.className = `server-avatar ${createLogo.style}`;
}

function showToast(message, type = 'success') {
  const container = document.getElementById('toast-container');
  if (!container) return;
  const toast = document.createElement('div');
  toast.className = `toast ${type}`;
  toast.textContent = message;
  container.appendChild(toast);
  
  setTimeout(() => {
    toast.style.opacity = '0';
    toast.style.transform = 'translateX(100%)';
    setTimeout(() => toast.remove(), 300);
  }, 3500);
}

function escapeHtml(str) {
  return String(str).replace(/[&<>"']/g, m => ({ "&":"&amp;","<":"&lt;",">":"&gt;",'"':"&quot;","'":"&#39;" }[m]));
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

function switchTab(name) {
  if (name !== "detail") { stopDetailPing(); stopBackupPolling(); }
  document.querySelectorAll(".tab").forEach(t => t.classList.remove("active"));
  document.querySelectorAll(".nav-btn").forEach(b => b.classList.remove("active"));
  const tab = document.getElementById("tab-" + name);
  if (tab) tab.classList.add("active");
  const btn = document.querySelector(`.nav-btn[data-tab="${name}"]`);
  if (btn) btn.classList.add("active");
  if (name === "servers") loadServers();
  if (name === "create") { loadVersions(); loadHostMemory(); }
  if (name === "settings") loadManagerUpdate();
  if (name === "map") window.mcMap?.onShow();
  else window.mcMap?.onHide();
  if (name === "browser") {
    loadServerSelect();
    loadFeatured();
  }
}

document.querySelectorAll(".nav-btn").forEach(btn => {
  btn.addEventListener("click", () => switchTab(btn.dataset.tab));
});

function renderServerSummary(servers) {
  const summary = document.getElementById("server-summary");
  if (!summary) return;
  const total = servers.length;
  const online = servers.filter(s => s.running).length;
  const totalRam = servers.reduce((sum, s) => sum + (Number(s.ram) || 2048), 0);
  summary.innerHTML = `
    <div class="summary-pill">
      <span class="summary-label">Servers</span>
      <strong>${total}</strong>
    </div>
    <div class="summary-pill online-pill">
      <span class="summary-label">Online</span>
      <strong>${online}</strong>
    </div>
    <div class="summary-pill">
      <span class="summary-label">Memory</span>
      <strong>${totalRam} MB</strong>
    </div>
    <div class="summary-pill">
      <span class="summary-label">Ports</span>
      <strong>${servers.filter(s => s.port).length ? servers.map(s => s.port || 25565).join(', ') : '—'}</strong>
    </div>
  `;
}

async function loadServers() {
  const el = document.getElementById("servers-list");
  const filter = document.getElementById("server-filter");
  if (filter) serverFilter = filter.value || "all";
  el.innerHTML = '<div class="loading">Loading...</div>';
    try {
    const res = await fetch("/api/servers");
    if (!res.ok) throw new Error("Failed to load servers");
    const servers = await res.json();
    renderServerSummary(servers);
    const filtered = servers.filter(s => {
      if (serverFilter === "online") return s.running;
      if (serverFilter === "offline") return !s.running;
      return true;
    });
    if (!filtered.length) {
      el.innerHTML = '<div class="empty">No servers in this view. Create one or switch filters.</div>';
      return;
    }
    el.innerHTML = filtered.map((s, i) => `
      <div class="server-card ${s.running ? 'online' : 'offline'}" style="--i:${i}" onclick="openServer('${s.id}')">
        <div class="card-top">
          <div class="server-status">
            <span class="status-dot ${s.running ? 'online' : 'offline'}"></span>
            <span class="badge ${s.running ? "online" : "offline"}">${s.running ? "Online" : "Offline"}</span>
          </div>
          <div class="card-actions">
            <button class="mini-btn" onclick="event.stopPropagation(); togglePowerFromCard(this, '${s.id}', ${s.running})">${s.running ? '⏹ Stop' : '▶ Start'}</button>
            <button class="mini-btn danger" onclick="event.stopPropagation(); deleteServerFromCard(this, '${s.id}')">✕ Delete</button>
          </div>
        </div>
        <div class="server-name-line">${s.logo?.file ? `<img class="server-avatar server-avatar-image" src="/api/server/${encodeURIComponent(s.id)}/logo/${encodeURIComponent(s.logo.file)}" alt="" />` : `<div class="server-avatar ${escapeHtml(s.logo?.style || 'avatar-lime')}" aria-hidden="true">${escapeHtml(s.logo?.mark || s.name.slice(0, 2).toUpperCase())}</div>`}<h3>${escapeHtml(s.name)}</h3></div>
        <div class="meta">
          <span class="badge type">${escapeHtml(s.type)}</span>
          <span>${escapeHtml(s.version)}</span>
            <span class="server-ping" data-ping-for="${escapeHtml(s.id)}">-- ms</span>
        </div>
        <div class="server-meta-grid">
          <div><span>RAM</span><strong>${s.ram || 2048} MB</strong></div>
          <div><span>Port</span><strong>${s.port || 25565}</strong></div>
          <div><span>Created</span><strong>${new Date(s.created || Date.now()).toLocaleDateString()}</strong></div>
        </div>
      </div>`).join("");
    refreshServerPings(filtered);
  } catch (e) {
    el.innerHTML = `<div class="empty">Error: ${e.message}</div>`;
  }
}

async function refreshServerPings(servers) {
  if (!getSettings().autoPing) return;
  await Promise.all(servers.map(async server => {
    try {
      const data = await (await fetch(`/api/server/${encodeURIComponent(server.id)}/ping`)).json();
      document.querySelectorAll(`[data-ping-for="${CSS.escape(server.id)}"]`).forEach(node => {
        node.textContent = data.ping == null ? "Offline" : `${data.ping} ms`;
        node.classList.toggle("ping-good", data.ping != null && data.ping < 100);
      });
    } catch {}
  }));
}

async function refreshServerPingsFromCards() {
  try {
    const servers = await (await fetch("/api/servers")).json();
    refreshServerPings(servers);
  } catch {}
}

async function togglePowerFromCard(button, id, running) {
  if (button.disabled) return;
  button.disabled = true;
  button.textContent = running ? "Stopping..." : "Starting...";
  try {
    const data = await (await fetch(`/api/server/${id}/${running ? "stop" : "start"}`, {
      method: "POST", headers: { "Content-Type": "application/json" }, body: "{}"
    })).json();
    if (data.ok) {
      showToast(running ? "Server stopped" : "Server started", "success");
      if (currentServerId === id) updateStatusBadge(!running);
    } else {
      showToast(data.message || `Failed to ${running ? "stop" : "start"}`, running ? "warning" : "error");
    }
  } catch (error) {
    showToast(error.message, "error");
  } finally {
    loadServers();
  }
}

async function deleteServerFromCard(button, id) {
  if (button.disabled) return;
  if (getSettings().confirmActions && !confirm("Delete this server permanently? This cannot be undone.")) return;
  button.disabled = true;
  button.textContent = "Deleting...";
  try {
    const data = await (await fetch(`/api/server/${id}/delete`, { method: "POST" })).json();
    if (!data.ok) throw new Error(data.error || "Could not delete this server");
    showToast("Server deleted", "success");
    if (currentServerId === id) {
      currentServerId = null;
      stopConsolePolling();
    }
  } catch (error) {
    showToast(error.message, "error");
    button.disabled = false;
    button.textContent = "✕ Delete";
  } finally {
    loadServers();
  }
}

document.getElementById("server-filter").addEventListener("change", loadServers);
document.getElementById("create-type").addEventListener("change", loadVersions);
document.getElementById("create-name").addEventListener("input", updateCreateLogo);
document.getElementById("create-max-players").addEventListener("input", event => {
  document.getElementById("max-players-display").textContent = event.target.value;
});
const portHint = document.getElementById("port-hint");
const portHintText = portHint.textContent;

document.getElementById("create-port").addEventListener("input", event => {
  const port = Number(event.target.value);
  const blank = event.target.value === "";
  const valid = port >= 1024 && port <= 65535;
  event.target.setCustomValidity(valid ? "" : "Use a port between 1024 and 65535");
  event.target.classList.toggle("invalid", !valid && !blank);
  event.target.setAttribute("aria-invalid", valid || blank ? "false" : "true");
  portHint.classList.toggle("hint-warn", !valid && !blank);
  portHint.textContent = valid || blank ? portHintText : `${event.target.value} is outside 1024–65535`;
  updateCreateReview();
});

["create-name", "create-type", "create-version", "create-max-players"].forEach(id => {
  document.getElementById(id).addEventListener("input", updateCreateReview);
});
document.getElementById("import-folder").addEventListener("change", event => {
  const label = document.querySelector(".import-folder-label");
  const count = event.target.files?.length || 0;
  if (label) label.childNodes[0].textContent = count ? `${count} files selected` : "Choose folder";
});

const RAM_MIN = 512;
const RAM_HARD_MAX = 65536;
const RAM_STEP = 256;
let ramCeiling = 16384;
let ramHint = "Pick an amount your machine can spare.";

function formatRam(mb) {
  const amount = Number(mb) || 0;
  if (amount < 1024) return `${amount} MB`;
  const gb = amount / 1024;
  return `${Number.isInteger(gb) ? gb : gb.toFixed(1)} GB`;
}

function setCreateRam(value, source) {
  const exact = document.getElementById("create-ram-exact");
  const typed = Number(value);
  const amount = Math.max(RAM_MIN, Math.min(RAM_HARD_MAX, Math.round((typed || 2048) / RAM_STEP) * RAM_STEP));
  // Only the typed field can hold an out-of-range value; presets and the clamp above never produce one.
  const outOfRange = source === "exact" && value !== "" && (!Number.isFinite(typed) || typed < RAM_MIN || typed > RAM_HARD_MAX);
  if (source !== "exact") exact.value = amount;
  exact.classList.toggle("invalid", outOfRange);
  exact.setAttribute("aria-invalid", outOfRange ? "true" : "false");
  document.getElementById("ram-display").textContent = formatRam(amount);
  document.querySelectorAll(".ram-preset").forEach(preset => preset.classList.toggle("active", Number(preset.dataset.ram) === amount));
  const hint = document.getElementById("ram-hint");
  hint.className = "field-hint";
  if (outOfRange) {
    hint.classList.add("hint-warn");
    hint.textContent = `Enter between ${formatRam(RAM_MIN)} and ${formatRam(RAM_HARD_MAX)}.`;
  } else if (amount > ramCeiling) {
    hint.innerHTML = `${ramHint}<br /><span class="hint-warn">${formatRam(amount)} is more than this machine can comfortably give a server.</span>`;
  } else {
    hint.textContent = ramHint;
  }
  updateCreateReview();
  return amount;
}

function currentCreateRam() {
  return Math.max(RAM_MIN, Math.min(RAM_HARD_MAX, Number(document.getElementById("create-ram-exact").value) || 2048));
}

async function loadHostMemory() {
  try {
    const data = await requestJson("/api/system/memory");
    ramCeiling = data.suggested || ramCeiling;
    document.querySelectorAll(".ram-preset").forEach(preset => {
      const beyond = Boolean(data.total) && Number(preset.dataset.ram) > data.total;
      preset.classList.toggle("beyond", beyond);
      preset.title = beyond ? "More memory than this machine has installed" : `Allocate ${preset.textContent} to the server`;
    });
    ramHint = data.total
      ? `${formatRam(data.total)} installed, ${formatRam(data.available)} free right now. Leave 1–2 GB for the rest of the system.`
      : "Installed memory could not be read. Pick an amount your machine can spare.";
  } catch {
    ramHint = "Installed memory could not be read. Pick an amount your machine can spare.";
  }
  setCreateRam(currentCreateRam(), "init");
}

document.getElementById("create-ram-exact").addEventListener("input", event => setCreateRam(event.target.value, "exact"));
document.getElementById("create-ram-exact").addEventListener("blur", () => setCreateRam(currentCreateRam(), "blur"));
document.getElementById("ram-presets").addEventListener("click", event => {
  const preset = event.target.closest(".ram-preset");
  if (preset) setCreateRam(preset.dataset.ram, "preset");
});

async function loadVersions() {
  const type = document.getElementById("create-type").value;
  const sel = document.getElementById("create-version");
  sel.innerHTML = '<option value="">Loading...</option>';
  try {
    const versions = await (await fetch(`/api/versions/${type}`)).json();
    sel.innerHTML = versions.length
      ? versions.map(v => `<option value="${v}">${v}</option>`).join("")
      : '<option value="">No versions</option>';
  } catch {
    sel.innerHTML = '<option value="">Error fetching versions</option>';
  }
  updateCreateReview();
}

function updateCreateReview() {
  const version = document.getElementById("create-version").value;
  document.getElementById("review-name").textContent = document.getElementById("create-name").value.trim() || "unnamed";
  document.getElementById("review-platform").textContent = `${document.getElementById("create-type").value}${version ? ` ${version}` : ""}`;
  document.getElementById("review-port").textContent = document.getElementById("create-port").value || "—";
  document.getElementById("review-ram").textContent = formatRam(currentCreateRam());
  document.getElementById("review-slots").textContent = document.getElementById("create-max-players").value;
}

async function createServer() {
  const name = document.getElementById("create-name").value.trim();
  const type = document.getElementById("create-type").value;
  const version = document.getElementById("create-version").value;
  const ram = currentCreateRam();
  const options = {
    port: parseInt(document.getElementById("create-port").value, 10),
    max_players: parseInt(document.getElementById("create-max-players").value, 10),
    gamemode: document.getElementById("create-gamemode").value,
    difficulty: document.getElementById("create-difficulty").value,
    motd: document.getElementById("create-motd").value.trim() || name,
    pvp: document.getElementById("create-pvp").checked,
    online_mode: document.getElementById("create-online-mode").checked,
    command_blocks: document.getElementById("create-command-blocks").checked,
    whitelist: document.getElementById("create-whitelist").checked
  };
  const status = document.getElementById("create-status");
  const btn = document.getElementById("btn-create");
  if (!name) {
    status.className = "status-msg show err";
    status.textContent = "Give the server a name first";
    document.getElementById("create-name").focus();
    return;
  }
  if (!version) {
    status.className = "status-msg show err";
    status.textContent = "Pick a Minecraft version first";
    document.getElementById("create-version").focus();
    return;
  }
  if (options.port < 1024 || options.port > 65535) {
    status.className = "status-msg show err";
    status.textContent = "Server port must be between 1024 and 65535";
    document.getElementById("create-port").focus();
    return;
  }
  const typedRam = Number(document.getElementById("create-ram-exact").value);
  if (!Number.isFinite(typedRam) || typedRam < RAM_MIN || typedRam > RAM_HARD_MAX) {
    status.className = "status-msg show err";
    status.textContent = `Memory must be between ${formatRam(RAM_MIN)} and ${formatRam(RAM_HARD_MAX)}`;
    document.getElementById("create-ram-exact").focus();
    return;
  }
  btn.disabled = true;
  btn.textContent = "Downloading...";
  status.className = "status-msg show";
  status.textContent = "Downloading server JAR (may take a minute)...";
  try {
    const res = await fetch("/api/create", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ name, type, version, ram, logo: createLogo, ...options })
    });
    const data = await res.json();
    if (data.ok) {
      status.className = "status-msg show ok";
      status.textContent = `Created "${name}"`;
      document.getElementById("create-name").value = "";
      updateCreateReview();
      showToast(`Server ${name} created successfully!`, 'success');
      setTimeout(() => switchTab("servers"), 1000);
    } else {
      status.className = "status-msg show err";
      status.textContent = data.error || "Failed to create server";
      showToast(data.error || "Failed to create server", 'error');
    }
  } catch (e) {
    status.className = "status-msg show err";
    status.textContent = e.message;
  } finally {
    btn.disabled = false;
    btn.textContent = "Download & Create";
  }
}

const IMPORT_BATCH_BYTES = 24 * 1024 * 1024;
const IMPORT_BATCH_FILES = 150;
const IMPORT_FILE_LIMIT = 240 * 1024 * 1024;

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

function importRootFolder(files) {
  const roots = new Set(files.map(file => (file.webkitRelativePath || file.name).replace(/\\/g, "/").split("/")[0]));
  const nested = files.every(file => (file.webkitRelativePath || file.name).includes("/"));
  return roots.size === 1 && nested ? [...roots][0] : "";
}

function importRelativePath(file, root) {
  const path = (file.webkitRelativePath || file.name).replace(/\\/g, "/");
  return root && path.startsWith(`${root}/`) ? path.slice(root.length + 1) : path;
}

function buildImportBatches(files) {
  const batches = [];
  let batch = [];
  let size = 0;
  for (const file of files) {
    if (batch.length && (size + file.size > IMPORT_BATCH_BYTES || batch.length >= IMPORT_BATCH_FILES)) {
      batches.push(batch);
      batch = [];
      size = 0;
    }
    batch.push(file);
    size += file.size;
  }
  if (batch.length) batches.push(batch);
  return batches;
}

async function importServerFolder() {
  const input = document.getElementById("import-folder");
  const button = document.getElementById("btn-import");
  const status = document.getElementById("import-status");
  const files = [...(input.files || [])];
  const name = document.getElementById("import-name").value.trim();
  if (!files.length) {
    status.className = "status-msg show err";
    status.textContent = "Choose an existing server folder first";
    return;
  }
  const oversized = files.find(file => file.size > IMPORT_FILE_LIMIT);
  if (oversized) {
    status.className = "status-msg show err";
    status.textContent = `"${oversized.name}" is ${formatBytes(oversized.size)}, above the ${formatBytes(IMPORT_FILE_LIMIT)} single-file limit. Remove or archive it, then import again.`;
    return;
  }
  const root = importRootFolder(files);
  const batches = buildImportBatches(files);
  const totalBytes = files.reduce((sum, file) => sum + file.size, 0) || 1;
  let token = null;
  button.disabled = true;
  status.className = "status-msg show";
  status.textContent = `Uploading ${files.length} files (${formatBytes(totalBytes)})...`;
  setTaskProgress("import", 0, "Preparing upload...");
  try {
    const session = await requestJson("/api/import/start", {
      method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ name })
    });
    if (!session.ok) throw new Error(session.error || "Could not start the import");
    token = session.token;
    let sent = 0;
    for (const batch of batches) {
      const form = new FormData();
      form.append("token", token);
      batch.forEach(file => form.append("files", file, importRelativePath(file, root)));
      const result = await requestJson("/api/import/upload", { method: "POST", body: form });
      if (!result.ok) throw new Error(result.error || "Upload failed");
      sent += batch.reduce((sum, file) => sum + file.size, 0);
      setTaskProgress("import", (sent / totalBytes) * 100, `Uploaded ${formatBytes(sent)} of ${formatBytes(totalBytes)}`);
    }
    setTaskProgress("import", 100, "Building the server entry...");
    const data = await requestJson("/api/import/finish", {
      method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ token, name })
    });
    if (!data.ok) throw new Error(data.error || "Import failed");
    token = null;
    status.className = "status-msg show ok";
    status.textContent = `Imported "${data.meta.name}" with ${data.files} files`;
    showToast("Server folder imported", "success");
    input.value = "";
    const label = document.querySelector(".import-folder-label");
    if (label) label.childNodes[0].textContent = "Choose folder";
    setTimeout(() => { hideTaskProgress("import"); switchTab("servers"); }, 900);
  } catch (error) {
    if (token) {
      fetch("/api/import/cancel", {
        method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ token })
      }).catch(() => {});
    }
    hideTaskProgress("import");
    status.className = "status-msg show err";
    status.textContent = error.message;
    showToast(error.message, "error");
  } finally {
    button.disabled = false;
  }
}

async function openServer(id) {
  stopBackupPolling();
  currentServerId = id;
  consoleOffset = 0;
  fsPath = "";
  switchTab("detail");
  document.querySelectorAll(".dtab").forEach(d => d.classList.remove("active"));
  document.querySelectorAll(".dtab-panel").forEach(p => p.classList.remove("active"));
  document.querySelector('.dtab[data-dtab="console"]').classList.add("active");
  document.getElementById("dtab-console").classList.add("active");
  document.getElementById("console-output").textContent = ""; // Clear console on load

  const servers = await (await fetch("/api/servers")).json();
  const s = servers.find(x => x.id === id);
  if (!s) return;
  document.getElementById("detail-title").textContent = s.name;
  renderDetailLogo(s);
  document.getElementById("detail-info").innerHTML = `
    <div class="row"><span>Type</span><span>${s.type}</span></div>
    <div class="row"><span>Version</span><span>${s.version}</span></div>
    <div class="row"><span>RAM</span><span>${s.ram || 2048} MB</span></div>
    <div class="row"><span>Port</span><span>${s.port || 25565}</span></div>
    <div class="row"><span>Ping</span><span id="detail-ping">Checking...</span></div>
    <div class="row"><span>Created</span><span>${new Date(s.created || Date.now()).toLocaleDateString()}</span></div>`;
  updateStatusBadge(s.running);
  updateDetailPing(s.id);
  startDetailPing(s.id);
  startConsolePolling();
  loadPluginMods();
  loadPlayit();
}

async function updateDetailPing(id) {
  const target = document.getElementById("detail-ping");
  if (!target || detailPingInFlight) return;
  detailPingInFlight = true;
  try {
    const data = await (await fetch(`/api/server/${encodeURIComponent(id)}/ping`)).json();
    target.textContent = data.ping == null ? "Offline" : `${data.ping} ms`;
    target.classList.toggle("ping-live", data.ping != null);
  } catch { target.textContent = "Unavailable"; }
  finally { detailPingInFlight = false; }
}

function startDetailPing(id) {
  if (detailPingTimer) clearInterval(detailPingTimer);
  detailPingTimer = null;
  if (!getSettings().autoPing) return;
  detailPingTimer = setInterval(() => {
    if (currentServerId === id && document.getElementById("tab-detail")?.classList.contains("active")) updateDetailPing(id);
  }, Math.max(2, Number(getSettings().pingInterval)) * 1000);
}

function stopDetailPing() {
  if (detailPingTimer) clearInterval(detailPingTimer);
  detailPingTimer = null;
}

function renderDetailLogo(server) {
  const target = document.getElementById("detail-logo");
  if (!target) return;
  if (server.logo?.file) {
    target.outerHTML = `<img id="detail-logo" class="server-avatar server-avatar-image" src="/api/server/${encodeURIComponent(server.id)}/logo/${encodeURIComponent(server.logo.file)}" alt="${escapeHtml(server.name)} logo" />`;
  } else {
    target.className = `server-avatar ${escapeHtml(server.logo?.style || "avatar-lime")}`;
    target.textContent = escapeHtml(server.logo?.mark || server.name.slice(0, 2).toUpperCase());
  }
}

async function importServerLogo() {
  const input = document.getElementById("logo-upload");
  if (!input.files?.length || !currentServerId) return;
  const form = new FormData();
  form.append("logo", input.files[0]);
  const data = await (await fetch(`/api/server/${currentServerId}/logo`, { method: "POST", body: form })).json();
  if (!data.ok) { showToast(data.error || "Logo import failed", "error"); return; }
  showToast("Server logo imported", "success");
  const server = (await (await fetch("/api/servers")).json()).find(item => item.id === currentServerId);
  if (server) { renderDetailLogo(server); loadServers(); }
  input.value = "";
}

document.querySelectorAll(".dtab").forEach(btn => {
  btn.addEventListener("click", () => {
    document.querySelectorAll(".dtab").forEach(d => d.classList.remove("active"));
    document.querySelectorAll(".dtab-panel").forEach(p => p.classList.remove("active"));
    btn.classList.add("active");
    document.getElementById("dtab-" + btn.dataset.dtab).classList.add("active");
    if (btn.dataset.dtab === "players") loadPlayers();
    if (btn.dataset.dtab === "commands") loadCommandWiki();
    if (btn.dataset.dtab === "files") loadFs();
    if (btn.dataset.dtab === "props") loadProps();
    if (btn.dataset.dtab === "backups") loadBackups();
    if (btn.dataset.dtab === "anticheat") loadAntiCheat();
    if (btn.dataset.dtab === "plugins") loadPluginMods();
    if (btn.dataset.dtab === "plugins") loadPluginConfigs();
    if (btn.dataset.dtab === "plugins") loadAutoUpdate();
  });
});

function updateStatusBadge(running) {
  const badge = document.getElementById("detail-status");
  badge.textContent = running ? "Online" : "Offline";
  badge.className = "badge " + (running ? "online" : "offline");
  window.mcScene?.setServerState({ running });
}

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
  const text = document.getElementById("console-output")?.textContent || "";
  const blob = new Blob([text], { type: "text/plain;charset=utf-8" });
  const link = document.createElement("a");
  link.href = URL.createObjectURL(blob);
  link.download = `${currentServerId || "server"}-console.log`;
  link.click();
  URL.revokeObjectURL(link.href);
}

const minecraftCommands = [
  ["advancement", "Gameplay", "advancement grant <targets> everything", "Grant or revoke advancements"],
  ["attribute", "Gameplay", "attribute <target> <attribute> get", "Inspect entity attributes"],
  ["ban", "Players", "ban <player> [reason]", "Ban a player"], ["ban-ip", "Players", "ban-ip <address|name> [reason]", "Ban an IP address"],
  ["banlist", "Players", "banlist [ips|players]", "List bans"], ["bossbar", "World", "bossbar add <id> <name>", "Create and manage boss bars"],
  ["clear", "Players", "clear [targets] [item] [maxCount]", "Clear items from inventories"], ["clone", "World", "clone <begin> <end> <destination>", "Copy a region"],
  ["data", "World", "data get <target> [path]", "Inspect or modify NBT data"], ["datapack", "World", "datapack list", "Manage data packs"],
  ["defaultgamemode", "World", "defaultgamemode <mode>", "Set the default game mode"], ["deop", "Players", "deop <targets>", "Remove operator status"],
  ["difficulty", "World", "difficulty <difficulty>", "Change difficulty"], ["effect", "Players", "effect give <targets> <effect> [seconds] [amplifier]", "Apply or clear effects"],
  ["enchant", "Players", "enchant <targets> <enchantment> [level]", "Enchant an item"], ["execute", "Advanced", "execute as <targets> run <command>", "Run a command with context"],
  ["experience", "Players", "experience add <targets> <amount> [levels|points]", "Manage experience"], ["fill", "World", "fill <from> <to> <block>", "Fill a region with blocks"],
  ["forceload", "World", "forceload add <from> [to]", "Force chunks to stay loaded"], ["function", "Data", "function <name>", "Run a data pack function"],
  ["gamemode", "Players", "gamemode <mode> [target]", "Change game mode"], ["gamerule", "World", "gamerule <rule> [value]", "Change a game rule"],
  ["give", "Players", "give <targets> <item> [count]", "Give items to players"], ["help", "Utility", "help [command]", "Show command help"],
  ["item", "Players", "item replace entity <target> <slot> with <item>", "Manipulate inventory items"], ["kick", "Players", "kick <targets> [reason]", "Kick players"],
  ["kill", "Players", "kill [targets]", "Kill entities"], ["list", "Players", "list", "List online players"],
  ["locate", "World", "locate structure <structure>", "Locate a structure"], ["loot", "World", "loot give <target> loot <loot_table>", "Generate loot"],
  ["me", "Chat", "me <action>", "Send a third-person message"], ["msg", "Chat", "msg <target> <message>", "Send a private message"],
  ["op", "Players", "op <targets>", "Grant operator status"], ["particle", "World", "particle <name> <pos>", "Spawn particles"],
  ["pardon", "Players", "pardon <name>", "Remove a player ban"], ["pardon-ip", "Players", "pardon-ip <address>", "Remove an IP ban"],
  ["playsound", "Audio", "playsound <sound> <source> <targets>", "Play a sound"], ["recipe", "Players", "recipe give <targets> <recipe>", "Unlock recipes"],
  ["reload", "Server", "reload", "Reload data packs and plugins where supported"], ["replaceitem", "Players", "item replace entity <target> <slot> with <item>", "Replace an inventory slot"],
  ["save-all", "Server", "save-all", "Save world data"], ["say", "Chat", "say <message>", "Broadcast a server message"],
  ["schedule", "Data", "schedule function <function> <time>", "Schedule a function"], ["scoreboard", "Gameplay", "scoreboard objectives add <objective> dummy", "Manage scoreboards"],
  ["seed", "World", "seed", "Show the world seed"], ["setblock", "World", "setblock <pos> <block>", "Change one block"],
  ["setworldspawn", "World", "setworldspawn [pos]", "Set the world spawn"], ["spawnpoint", "Players", "spawnpoint [targets] [pos]", "Set a player spawn point"],
  ["spectate", "Players", "spectate [target] [player]", "Spectate an entity"], ["spreadplayers", "World", "spreadplayers <center> <spreadDistance> <maxRange> <targets>", "Spread entities"],
  ["stopsound", "Audio", "stopsound <targets> [source] [sound]", "Stop sounds"], ["summon", "World", "summon <entity> [pos]", "Summon an entity"],
  ["tag", "Gameplay", "tag <targets> add <name>", "Manage entity tags"], ["team", "Gameplay", "team add <team> [displayName]", "Manage teams"],
  ["teleport", "Players", "teleport <targets> <destination>", "Teleport players or entities"], ["time", "World", "time set <day|night|noon|midnight>", "Set world time"],
  ["title", "Chat", "title <targets> title <text>", "Display a title"], ["tp", "Players", "tp <targets> <destination>", "Teleport players"],
  ["trigger", "Gameplay", "trigger <objective> [add|set] <value>", "Trigger a scoreboard objective"], ["weather", "World", "weather <clear|rain|thunder> [duration]", "Change weather"],
  ["whitelist", "Players", "whitelist <on|off|list|add|remove>", "Manage the whitelist"], ["worldborder", "World", "worldborder set <distance> [time]", "Manage the world border"],
  ["xp", "Players", "xp add <targets> <amount> [levels|points]", "Manage experience"]
];

function loadCommandWiki() {
  const category = document.getElementById("command-category");
  const search = document.getElementById("command-search");
  if (!category.options.length || category.options.length === 1) {
    [...new Set(minecraftCommands.map(command => command[1]))].sort().forEach(name => category.add(new Option(name, name)));
  }
  const render = () => {
    const query = search.value.toLowerCase();
    const selected = category.value;
    const filtered = minecraftCommands.filter(([name, group, usage, description]) => (selected === "all" || group === selected) && [name, group, usage, description].some(value => value.toLowerCase().includes(query)));
    document.getElementById("command-list").innerHTML = filtered.map(([name, group, usage, description]) => `<article class="command-entry"><div><span class="badge type">${group}</span><h3>/${name}</h3><p>${description}</p><code>${usage}</code></div><button class="btn small" onclick="useWikiCommand('${escapeHtml(usage)}')">Use in console</button></article>`).join("") || '<div class="empty">No commands match this search.</div>';
  };
  search.oninput = render; category.onchange = render; render();
}

function useWikiCommand(command) {
  const input = document.getElementById("cmd-input");
  input.value = command;
  switchDetailTab("console");
  input.focus();
}

function switchDetailTab(name) {
  const button = document.querySelector(`.dtab[data-dtab="${name}"]`);
  if (button) button.click();
}

async function loadAntiCheat() {
  const data = await (await fetch(`/api/server/${currentServerId}/anticheat`)).json();
  const config = data.config || {};
  ["enabled", "movement", "combat", "alerts"].forEach(key => { document.getElementById(`ac-${key}`).checked = Boolean(config[key]); });
  document.getElementById("ac-threshold").value = config.threshold || 5;
  document.getElementById("ac-command").value = config.command || "notify";
  const status = document.getElementById("anticheat-plugin-status");
  status.textContent = data.plugins?.length ? `${data.plugins.length} plugin detected` : "Plugin required";
  status.className = `badge ${data.plugins?.length ? "online" : "offline"}`;
}

async function saveAntiCheat() {
  const payload = { enabled: document.getElementById("ac-enabled").checked, movement: document.getElementById("ac-movement").checked, combat: document.getElementById("ac-combat").checked, alerts: document.getElementById("ac-alerts").checked, threshold: document.getElementById("ac-threshold").value, command: document.getElementById("ac-command").value };
  const data = await (await fetch(`/api/server/${currentServerId}/anticheat`, { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(payload) })).json();
  showToast(data.ok ? "Anti-cheat profile saved" : (data.error || "Save failed"), data.ok ? "success" : "error");
}

function getVulcanCommand() {
  const template = document.getElementById("vulcan-command").value;
  const player = document.getElementById("vulcan-player").value.trim();
  return template.replaceAll("{player}", player);
}

function updateVulcanPreview() {
  const command = getVulcanCommand();
  document.getElementById("vulcan-preview").textContent = `/${command}`;
}

async function sendVulcanCommand() {
  const template = document.getElementById("vulcan-command").value;
  const command = getVulcanCommand();
  if (template.includes("{player}") && !document.getElementById("vulcan-player").value.trim()) {
    showToast("Enter a player name first", "warning");
    return;
  }
  const data = await (await fetch(`/api/server/${currentServerId}/command`, { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ command }) })).json();
  showToast(data.ok ? `Sent /${command}` : (data.error || data.message || "Command failed"), data.ok ? "success" : "error");
}

function togglePlayit() {
  document.querySelector(".playit-card")?.scrollIntoView({ behavior: "smooth", block: "center" });
}

async function loadPlayit() {
  if (!currentServerId) return;
  const data = await (await fetch(`/api/server/${currentServerId}/playit`)).json();
  const status = document.getElementById("playit-status");
  if (!status) return;
  document.getElementById("playit-executable").value = data.executable || "playit";
  status.textContent = data.running ? "Running" : (data.configured ? "Ready" : "Not configured");
  status.className = `badge ${data.running ? "online" : "offline"}`;
  document.getElementById("playit-log").textContent = (data.logs || []).join("\n");
}

async function savePlayit() {
  const data = await (await fetch(`/api/server/${currentServerId}/playit`, {
    method: "POST", headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ executable: document.getElementById("playit-executable").value, secret: document.getElementById("playit-secret").value })
  })).json();
  showToast(data.message || data.error, data.ok ? "success" : "error");
  if (data.ok) { document.getElementById("playit-secret").value = ""; loadPlayit(); }
}

async function startPlayit() {
  const data = await (await fetch(`/api/server/${currentServerId}/playit/start`, { method: "POST" })).json();
  showToast(data.message || data.error, data.ok ? "success" : "error");
  loadPlayit();
}

async function stopPlayit() {
  const data = await (await fetch(`/api/server/${currentServerId}/playit/stop`, { method: "POST" })).json();
  showToast(data.message || data.error, data.ok ? "success" : "warning");
  loadPlayit();
}

async function startSelected() {
  if (!currentServerId) return;
  const data = await (await fetch(`/api/server/${currentServerId}/start`, {
    method: "POST", headers: { "Content-Type": "application/json" }, body: "{}"
  })).json();
  
  if (data.ok) { 
    showToast("Server started", 'success');
    updateStatusBadge(true); 
    startConsolePolling(); 
  } else {
    showToast(data.message || "Failed to start", 'error');
  }
}

async function stopSelected() {
  if (!currentServerId) return;
  const data = await (await fetch(`/api/server/${currentServerId}/stop`, { method: "POST" })).json();
  showToast(data.message || "Server stopping", data.ok ? 'success' : 'warning');
  updateStatusBadge(false);
}

async function deleteSelected() {
  if (!currentServerId || (getSettings().confirmActions && !confirm("Are you sure you want to delete this server forever? This cannot be undone."))) return;
  try {
    await fetch(`/api/server/${currentServerId}/delete`, { method: "POST" });
    showToast("Server deleted", "success");
    currentServerId = null;
    stopConsolePolling();
    switchTab("servers");
  } catch (e) {
    showToast("Error deleting server", "error");
  }
}

async function sendCmd() {
  const input = document.getElementById("cmd-input");
  const cmd = input.value.trim();
  if (!cmd || !currentServerId) return;
  await fetch(`/api/server/${currentServerId}/command`, {
    method: "POST", headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ command: cmd })
  });
  input.value = "";
}
document.getElementById("cmd-input").addEventListener("keydown", e => { if (e.key === "Enter") sendCmd(); });

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
  if (!currentServerId) return;
  try {
    const data = await (await fetch(`/api/server/${currentServerId}/console?since=${consoleOffset}`)).json();
    updateStatusBadge(data.running);
    if (data.lines?.length) {
      const out = document.getElementById("console-output");
      // Smart Auto-scroll check
      const isScrolledToBottom = out.scrollHeight - out.clientHeight <= out.scrollTop + 50;
      
      out.textContent += (out.textContent ? "\n" : "") + data.lines.join("\n");
      consoleOffset = data.total;
      
      if (isScrolledToBottom) {
        out.scrollTop = out.scrollHeight;
      }
    }
  } catch {}
}

async function loadPlayers() {
  if (!currentServerId) return;
  const [data, activeData] = await Promise.all([
    (await fetch(`/api/server/${currentServerId}/players`)).json(),
    (await fetch(`/api/server/${currentServerId}/active-players`)).json()
  ]);
  renderActivePlayers(activeData.players || data.active || []);
  const fmt = list => {
    if (!list?.length) return '<div class="empty" style="padding:0.4rem">Empty</div>';
    return list.map(p => {
      const name = p.name || p.uuid || JSON.stringify(p);
      return `<div class="item"><span>${escapeHtml(name)}</span></div>`;
    }).join("");
  };
  document.getElementById("ops-list").innerHTML = fmt(data.ops);
  document.getElementById("wl-list").innerHTML = fmt(data.whitelist);
  document.getElementById("ban-list").innerHTML = fmt(data.banned);
}

function renderActivePlayers(players) {
  const list = document.getElementById("active-player-list");
  const count = document.getElementById("active-player-count");
  if (!list || !count) return;
  count.textContent = `${players.length} online`;
  count.className = `badge ${players.length ? "online" : "offline"}`;
  window.mcScene?.setServerState({ players: players.length });
  list.innerHTML = players.length
    ? players.map(name => `<button class="active-player" onclick="selectActivePlayer('${escapeHtml(name)}')"><span class="status-dot online"></span><strong>${escapeHtml(name)}</strong><span>Use</span></button>`).join("")
    : '<div class="empty">No players online</div>';
}

function selectActivePlayer(name) {
  const input = document.getElementById("player-name");
  if (input) {
    input.value = name;
    input.focus();
  }
}

async function playerAction(action) {
  const player = document.getElementById("player-name").value.trim();
  if (!player && !["whitelist_on","whitelist_off"].includes(action)) {
    showToast("Please enter a player name first", "warning");
    return;
  }
  const data = await (await fetch(`/api/server/${currentServerId}/player-action`, {
    method: "POST", headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ action, player })
  })).json();
  
  if (data.ok) {
    showToast(`Executed: ${data.command}`, "success");
    document.getElementById("player-name").value = "";
    setTimeout(loadPlayers, 1500);
  } else {
    showToast(data.message || data.error, "error");
  }
}

async function runTrollAction() {
  const player = document.getElementById("player-name").value.trim();
  const action = document.getElementById("troll-action").value;
  const value = document.getElementById("troll-value").value.trim();
  if (!player) { showToast("Select or enter a player first", "warning"); return; }
  const data = await (await fetch(`/api/server/${currentServerId}/player-action`, { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ action: `troll_${action}`, player, value }) })).json();
  showToast(data.ok ? "Prank command sent" : (data.error || data.message || "Prank failed"), data.ok ? "success" : "error");
}

async function loadFs() {
  if (!currentServerId) return;
  document.getElementById("fs-path").textContent = "/" + (fsPath || "");
  const data = await (await fetch(`/api/server/${currentServerId}/fs/list?path=${encodeURIComponent(fsPath)}`)).json();
  if (!data.ok) {
    document.getElementById("fs-list").innerHTML = `<div class="empty">${data.error}</div>`;
    return;
  }
  document.getElementById("fs-list").innerHTML = data.items.map(item => `
    <div class="fs-item">
      <span onclick="${item.is_dir ? `fsEnter('${escapeHtml(item.name)}')` : `fsOpen('${escapeHtml(item.name)}')`}" class="name">
        ${item.is_dir ? "📁" : "📄"} ${escapeHtml(item.name)}
      </span>
      <span class="meta">${item.is_dir ? "" : (item.size/1024).toFixed(1)+" KB"}</span>
      <span class="actions">
        ${!item.is_dir ? `<button class="btn small" onclick="fsDownload('${escapeHtml(item.name)}')">↓</button>` : ""}
        <button class="btn danger small" onclick="fsDelete('${escapeHtml(item.name)}')">✕</button>
      </span>
    </div>`).join("") || '<div class="empty">Empty folder</div>';
}

function fsEnter(name) {
  fsPath = fsPath ? fsPath + "/" + name : name;
  fsCloseEditor();
  loadFs();
}
function fsUp() {
  if (!fsPath) return;
  const parts = fsPath.split("/");
  parts.pop();
  fsPath = parts.join("/");
  fsCloseEditor();
  loadFs();
}
async function fsOpen(name) {
  const path = fsPath ? fsPath + "/" + name : name;
  const data = await (await fetch(`/api/server/${currentServerId}/fs/read?path=${encodeURIComponent(path)}`)).json();
  if (!data.ok) { 
    showToast(data.error, "error"); 
    return; 
  }
  fsEditPath = path;
  document.getElementById("fs-edit-name").textContent = path;
  document.getElementById("fs-content").value = data.content;
  document.getElementById("fs-editor").style.display = "block";
}
async function fsSave() {
  if (!fsEditPath) return;
  const content = document.getElementById("fs-content").value;
  const data = await (await fetch(`/api/server/${currentServerId}/fs/write`, {
    method: "POST", headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ path: fsEditPath, content })
  })).json();
  if (data.ok) showToast("File saved", "success");
  else showToast(data.error, "error");
}
function fsCloseEditor() {
  document.getElementById("fs-editor").style.display = "none";
  fsEditPath = null;
}
async function fsDelete(name) {
  if (getSettings().confirmActions && !confirm("Are you sure you want to delete " + name + "?")) return;
  const path = fsPath ? fsPath + "/" + name : name;
  await fetch(`/api/server/${currentServerId}/fs/delete`, {
    method: "POST", headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ path })
  });
  showToast("Deleted " + name, "success");
  loadFs();
}
async function fsNewFolder() {
  const name = prompt("Folder name:");
  if (!name) return;
  const path = fsPath ? fsPath + "/" + name : name;
  await fetch(`/api/server/${currentServerId}/fs/mkdir`, {
    method: "POST", headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ path })
  });
  loadFs();
}
async function fsUpload() {
  const input = document.getElementById("fs-upload");
  if (!input.files?.length) return;
  const fd = new FormData();
  fd.append("file", input.files[0]);
  fd.append("path", fsPath);
  const data = await (await fetch(`/api/server/${currentServerId}/fs/upload`, { method: "POST", body: fd })).json();
  if (data.ok) {
    showToast("Uploaded " + data.name, "success");
  } else {
    showToast(data.error, "error");
  }
  input.value = "";
  loadFs();
}
function fsDownload(name) {
  const path = fsPath ? fsPath + "/" + name : name;
  window.open(`/api/server/${currentServerId}/fs/download?path=${encodeURIComponent(path)}`, "_blank");
}

async function loadProps() {
  if (!currentServerId) return;
  const data = await (await fetch(`/api/server/${currentServerId}/properties`)).json();
  const props = data.props || {};
  document.getElementById("props-raw").value = data.raw || Object.entries(props).map(([key, value]) => `${key}=${value}`).join("\n");
  const keys = Object.keys(props).sort();
  const important = ["server-port","max-players","gamemode","difficulty","motd","online-mode","view-distance","spawn-protection","pvp","allow-nether","enable-command-block","white-list"];
  const ordered = [...important.filter(k => k in props), ...keys.filter(k => !important.includes(k))];
  const choices = {
    gamemode: ["survival", "creative", "adventure", "spectator"],
    difficulty: ["peaceful", "easy", "normal", "hard"],
    "online-mode": ["true", "false"], pvp: ["true", "false"],
    "white-list": ["true", "false"], "enable-command-block": ["true", "false"],
    "allow-nether": ["true", "false"], "spawn-monsters": ["true", "false"]
  };
  document.getElementById("props-form").innerHTML = ordered.map(k => {
    const value = String(props[k] ?? "");
    const control = choices[k]
      ? `<select data-prop="${escapeHtml(k)}">${choices[k].map(option => `<option ${option === value ? "selected" : ""}>${option}</option>`).join("")}</select>`
      : `<input type="text" data-prop="${escapeHtml(k)}" value="${escapeHtml(value)}" />`;
    return `<div class="form-group"><label>${escapeHtml(k)}${["motd", "server-port", "max-players"].includes(k) ? " <span class=\"property-hint\">common</span>" : ""}</label>${control}</div>`;
  }).join("");
}

function togglePropsMode() {
  const raw = document.getElementById("props-mode").value === "raw";
  document.getElementById("props-form").style.display = raw ? "none" : "grid";
  document.getElementById("props-raw").style.display = raw ? "block" : "none";
  document.querySelector('#dtab-props button[onclick="saveProps()"]')?.style.setProperty("display", raw ? "none" : "inline-block");
  document.getElementById("save-raw-props").style.display = raw ? "inline-block" : "none";
}

async function saveRawProps() {
  const raw = document.getElementById("props-raw").value;
  const data = await (await fetch(`/api/server/${currentServerId}/properties/raw`, { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ raw }) })).json();
  showToast(data.ok ? "Properties code saved" : (data.error || "Failed to save"), data.ok ? "success" : "error");
}

async function saveProps() {
  const props = {};
  document.querySelectorAll("#props-form input[data-prop], #props-form select[data-prop]").forEach(inp => {
    props[inp.dataset.prop] = inp.value;
  });
  const data = await (await fetch(`/api/server/${currentServerId}/properties`, {
    method: "POST", headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ props })
  })).json();
  
  if (data.ok) showToast("Server properties saved", "success");
  else showToast("Failed to save properties", "error");
}

async function loadPluginMods() {
  if (!currentServerId) return;
  const [plugins, mods] = await Promise.all([
    (await fetch(`/api/server/${currentServerId}/files?folder=plugins`)).json(),
    (await fetch(`/api/server/${currentServerId}/files?folder=mods`)).json()
  ]);
  const fmt = list => list.length
    ? list.map(f => `<div class="item"><span>${escapeHtml(f.name)}</span><span>${(f.size/1024).toFixed(0)} KB</span></div>`).join("")
    : '<div class="empty" style="padding:0.4rem">None found</div>';
  document.getElementById("plugins-list").innerHTML = fmt(plugins);
  document.getElementById("mods-list").innerHTML = fmt(mods);
}

let lastUpdateItems = [];

async function loadAutoUpdate() {
  if (!currentServerId) return;
  try {
    const data = await requestJson(`/api/server/${currentServerId}/auto-update`);
    if (!data.ok) return;
    document.getElementById("auto-update-enabled").checked = data.settings.enabled;
    document.getElementById("auto-update-interval").value = data.settings.interval_hours;
    document.getElementById("auto-update-install").checked = data.settings.install;
    document.getElementById("update-check-last").textContent = data.last_check
      ? `Last checked: ${new Date(data.last_check).toLocaleString()}`
      : "No check run yet.";
  } catch { /* server may have just been deleted mid-refresh */ }
}

async function saveAutoUpdate() {
  if (!currentServerId) return;
  const payload = {
    enabled: document.getElementById("auto-update-enabled").checked,
    interval_hours: Number(document.getElementById("auto-update-interval").value) || 12,
    install: document.getElementById("auto-update-install").checked
  };
  const data = await requestJson(`/api/server/${currentServerId}/auto-update`, {
    method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(payload)
  });
  if (data.ok) {
    showToast(payload.enabled ? "Update checks scheduled" : "Automatic update checks disabled", "success");
    loadAutoUpdate();
  }
}

function renderUpdateResults() {
  const list = document.getElementById("update-results");
  const withUpdates = lastUpdateItems.filter(item => item.update_available);
  document.getElementById("btn-apply-updates").hidden = withUpdates.length === 0;
  if (!lastUpdateItems.length) {
    list.innerHTML = '<div class="empty">No plugin or mod files found.</div>';
    return;
  }
  list.innerHTML = lastUpdateItems.map((item, index) => `
    <div class="backup-item" style="--i:${index}">
      <div class="backup-meta">
        <strong>${escapeHtml(item.name || item.file)}</strong>
        <span>${item.matched
          ? (item.update_available ? `${escapeHtml(item.current_version || "?")} → ${escapeHtml(item.latest_version || "?")}` : `Up to date · ${escapeHtml(item.current_version || "")}`)
          : "Not found on Modrinth"}</span>
      </div>
      <div class="backup-item-actions">
        ${item.update_available ? `<button class="btn primary small" onclick="applyUpdate(${index})">Update</button>` : ""}
      </div>
    </div>`).join("");
}

async function checkForUpdates() {
  if (!currentServerId) return;
  const button = document.getElementById("btn-check-updates");
  const badge = document.getElementById("update-check-badge");
  button.disabled = true;
  badge.textContent = "Checking...";
  try {
    const data = await requestJson(`/api/server/${currentServerId}/updates/check`);
    if (!data.ok) throw new Error(data.error || "Could not check for updates");
    lastUpdateItems = data.items;
    renderUpdateResults();
    const found = lastUpdateItems.filter(item => item.update_available).length;
    badge.textContent = found ? `${found} update${found === 1 ? "" : "s"} found` : "Up to date";
    badge.className = "badge " + (found ? "type" : "online");
    document.getElementById("update-check-last").textContent = `Last checked: ${new Date(data.checked).toLocaleString()}`;
  } catch (error) {
    showToast(error.message, "error");
    badge.textContent = "Check failed";
  } finally {
    button.disabled = false;
  }
}

async function applyUpdate(index) {
  const item = lastUpdateItems[index];
  if (!item) return;
  try {
    const data = await requestJson(`/api/server/${currentServerId}/updates/apply`, {
      method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ items: [item] })
    });
    if (data.applied?.length) {
      showToast(`Updated ${item.name || item.file}`, "success");
      checkForUpdates();
    } else {
      showToast(data.failed?.[0]?.detail || "Update failed", "error");
    }
  } catch (error) {
    showToast(error.message, "error");
  }
}

async function applyAllUpdates() {
  const pending = lastUpdateItems.filter(item => item.update_available);
  if (!pending.length) return;
  try {
    const data = await requestJson(`/api/server/${currentServerId}/updates/apply`, {
      method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ items: pending })
    });
    showToast(`Updated ${data.applied.length} of ${pending.length}${data.failed.length ? " · " + data.failed.length + " failed (stop the server and retry)" : ""}`, data.failed.length ? "warning" : "success");
    checkForUpdates();
  } catch (error) {
    showToast(error.message, "error");
  }
}

async function loadPluginConfigs() {
  if (!currentServerId) return;
  const list = document.getElementById("plugin-config-list");
  if (!list) return;
  const configs = await (await fetch(`/api/server/${currentServerId}/plugin-configs`)).json();
  list.innerHTML = configs.length ? configs.map(config => `<button class="plugin-config-item" onclick="openPluginConfig('${escapeHtml(config.path)}')"><span>${escapeHtml(config.path)}</span><small>${(config.size / 1024).toFixed(1)} KB</small></button>`).join("") : '<div class="empty">No YAML or JSON configs found</div>';
}

async function openPluginConfig(path) {
  pluginConfigPath = path;
  const data = await (await fetch(`/api/server/${currentServerId}/fs/read?path=${encodeURIComponent(path)}`)).json();
  if (!data.ok) { showToast(data.error || "Could not read config", "error"); return; }
  document.getElementById("plugin-config-name").textContent = path;
  document.getElementById("plugin-config-content").value = data.content;
}

async function savePluginConfig() {
  if (!pluginConfigPath) { showToast("Choose a config file first", "warning"); return; }
  const data = await (await fetch(`/api/server/${currentServerId}/fs/write`, { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ path: pluginConfigPath, content: document.getElementById("plugin-config-content").value }) })).json();
  showToast(data.ok ? "Plugin config saved" : (data.error || "Save failed"), data.ok ? "success" : "error");
}

function downloadPluginConfig() {
  if (!pluginConfigPath) { showToast("Choose a config file first", "warning"); return; }
  window.open(`/api/server/${currentServerId}/fs/download?path=${encodeURIComponent(pluginConfigPath)}`, "_blank");
}

async function reloadComponents() {
  const data = await (await fetch(`/api/server/${currentServerId}/reload-components`, { method: "POST" })).json();
  showToast(data.message || data.error, data.ok ? "success" : "warning");
}

async function restartSelected() {
  if (!currentServerId || (getSettings().confirmActions && !confirm("Restart this server now?"))) return;
  const data = await (await fetch(`/api/server/${currentServerId}/restart`, { method: "POST" })).json();
  showToast(data.message || data.error, data.ok ? "success" : "error");
  if (data.ok) { updateStatusBadge(true); startConsolePolling(); }
}

function stopBackupPolling() {
  if (backupJobTimer) clearInterval(backupJobTimer);
  backupJobTimer = null;
}

function renderBackupJob(job) {
  const button = document.getElementById("btn-backup-create");
  if (!job || job.state !== "running") {
    if (button) button.disabled = false;
    if (!job || job.state === "done") hideTaskProgress("backup");
    else if (job.state === "error") setTaskProgress("backup", 100, job.message);
    return false;
  }
  if (button) button.disabled = true;
  setTaskProgress("backup", job.progress, job.message);
  return true;
}

function startBackupPolling() {
  stopBackupPolling();
  const serverId = currentServerId;
  backupJobTimer = setInterval(async () => {
    if (currentServerId !== serverId) return stopBackupPolling();
    try {
      const data = await requestJson(`/api/server/${serverId}/backups/job`);
      if (renderBackupJob(data.job)) return;
      stopBackupPolling();
      showToast(data.job?.message || "Task finished", data.job?.state === "error" ? "error" : "success");
      loadBackups();
      loadServers();
    } catch {
      stopBackupPolling();
    }
  }, 1200);
}

async function loadBackups() {
  if (!currentServerId) return;
  const list = document.getElementById("backup-list");
  try {
    const data = await requestJson(`/api/server/${currentServerId}/backups`);
    if (!data.ok) throw new Error(data.error || "Could not read backups");
    document.getElementById("backup-keep").value = data.keep;
    document.getElementById("backup-count").textContent = `${data.backups.length} SAVED`;
    if (renderBackupJob(data.job)) startBackupPolling();
    list.innerHTML = data.backups.length ? data.backups.map((backup, i) => `
      <div class="backup-item" style="--i:${i}">
        <div class="backup-meta">
          <strong>${new Date(backup.created).toLocaleString()}</strong>
          <span>${formatBytes(backup.size)} · ${backup.files} files${backup.world ? "" : " · no world"}${backup.automatic ? " · automatic" : ""}</span>
          ${backup.label ? `<small>${escapeHtml(backup.label)}</small>` : ""}
        </div>
        <div class="backup-item-actions">
          <button class="btn small" onclick="downloadBackup('${backup.name}')">↓ Download</button>
          <button class="btn warning small" onclick="restoreBackup(this, '${backup.name}')"${data.running ? " disabled title='Stop the server first'" : ""}>↺ Restore</button>
          <button class="btn danger small" onclick="deleteBackup(this, '${backup.name}')" title="Delete this backup">✕</button>
        </div>
      </div>`).join("") : '<div class="empty">No backups yet. Create one before updating plugins or the server version.</div>';
  } catch (error) {
    list.innerHTML = `<div class="empty">${escapeHtml(error.message)}</div>`;
  }
  loadAutoBackup();
}

async function loadAutoBackup() {
  if (!currentServerId) return;
  try {
    const data = await requestJson(`/api/server/${currentServerId}/auto-backup`);
    if (!data.ok) return;
    document.getElementById("auto-backup-enabled").checked = data.settings.enabled;
    document.getElementById("auto-backup-interval").value = data.settings.interval_hours;
    document.getElementById("auto-backup-world").checked = data.settings.world;
    const badge = document.getElementById("auto-backup-badge");
    badge.textContent = data.settings.enabled ? "On" : "Off";
    badge.className = "badge " + (data.settings.enabled ? "online" : "offline");
    document.getElementById("auto-backup-last").textContent = data.last_run
      ? `Last automatic backup: ${new Date(data.last_run).toLocaleString()}`
      : "Never run yet.";
  } catch { /* server may have just been deleted mid-refresh */ }
}

async function saveAutoBackup() {
  if (!currentServerId) return;
  const payload = {
    enabled: document.getElementById("auto-backup-enabled").checked,
    interval_hours: Number(document.getElementById("auto-backup-interval").value) || 6,
    world: document.getElementById("auto-backup-world").checked
  };
  const data = await requestJson(`/api/server/${currentServerId}/auto-backup`, {
    method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(payload)
  });
  if (data.ok) {
    showToast(payload.enabled ? "Automatic backups enabled" : "Automatic backups disabled", "success");
    loadAutoBackup();
  }
}

async function createBackup() {
  if (!currentServerId) return;
  const payload = {
    label: document.getElementById("backup-label").value.trim(),
    world: document.getElementById("backup-world").checked,
    keep: Number(document.getElementById("backup-keep").value) || 0
  };
  try {
    setTaskProgress("backup", 0, "Starting backup...");
    const data = await requestJson(`/api/server/${currentServerId}/backups/create`, {
      method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(payload)
    });
    if (!data.ok) throw new Error(data.message || "Could not start the backup");
    document.getElementById("backup-label").value = "";
    startBackupPolling();
  } catch (error) {
    hideTaskProgress("backup");
    showToast(error.message, "error");
  }
}

async function restoreBackup(button, name) {
  if (!currentServerId || button.disabled) return;
  if (getSettings().confirmActions && !confirm("Restoring replaces every file in this server folder with the contents of the backup. A copy of the current files is saved first. Continue?")) return;
  button.disabled = true;
  try {
    setTaskProgress("backup", 0, "Starting restore...");
    const data = await requestJson(`/api/server/${currentServerId}/backups/${encodeURIComponent(name)}/restore`, {
      method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ safety: true })
    });
    if (!data.ok) throw new Error(data.error || data.message || "Could not start the restore");
    startBackupPolling();
  } catch (error) {
    hideTaskProgress("backup");
    showToast(error.message, "error");
    button.disabled = false;
  }
}

async function deleteBackup(button, name) {
  if (!currentServerId || button.disabled) return;
  if (getSettings().confirmActions && !confirm("Delete this backup permanently?")) return;
  button.disabled = true;
  try {
    const data = await requestJson(`/api/server/${currentServerId}/backups/${encodeURIComponent(name)}/delete`, { method: "POST" });
    if (!data.ok) throw new Error(data.error || "Could not delete the backup");
    showToast("Backup deleted", "success");
    loadBackups();
  } catch (error) {
    showToast(error.message, "error");
    button.disabled = false;
  }
}

function downloadBackup(name) {
  window.open(`/api/server/${currentServerId}/backups/${encodeURIComponent(name)}/download`, "_blank");
}

async function loadServerSelect() {
  const sel = document.getElementById("search-server");
  try {
    const servers = await (await fetch("/api/servers")).json();
    sel.innerHTML = '<option value="">Target server...</option>' +
      servers.map(s => `<option value="${s.id}">${escapeHtml(s.name)} (${s.type} ${s.version})</option>`).join("");
  } catch (e) {
    console.error("Could not load servers for dropdown");
  }
}

async function searchModrinth() {
  const q = document.getElementById("search-q").value.trim();
  const type = document.getElementById("search-type").value;
  const results = document.getElementById("search-results");
  if (!q) {
    showToast("Please enter a search term", "warning");
    return;
  }
  
  results.innerHTML = '<div class="loading">Searching...</div>';
  try {
    const data = await (await fetch(`/api/modrinth/search?q=${encodeURIComponent(q)}&type=${type}`)).json();
    const hits = data.hits || [];
    if (!hits.length) {
      results.innerHTML = '<div class="empty">No results found</div>';
      return;
    }
    results.innerHTML = hits.map(h => `
      <div class="result-card">
        ${h.icon_url ? `<img src="${h.icon_url}" alt="" onerror="this.style.display='none'" />` : "<div style='width:48px;height:48px;background:#242836;border-radius:8px'></div>"}
        <div class="info">
          <h4>${escapeHtml(h.title)}</h4>
          <p>${escapeHtml(h.description || "")}</p>
          <div class="stats">↓ ${(h.downloads||0).toLocaleString()} · ${escapeHtml(h.author||"")}</div>
        </div>
        <button class="btn primary small" onclick="installProject('${h.project_id||h.slug}','${escapeHtml(h.title)}','${type}')">Install</button>
      </div>`).join("");
  } catch (e) {
    results.innerHTML = `<div class="empty">Search Error: ${e.message}</div>`;
  }
}

function renderProjectCards(hits, type) {
  return hits.length
    ? hits.map(h => `
      <article class="featured-item">
        ${h.icon_url ? `<img src="${h.icon_url}" alt="" onerror="this.style.display='none'" />` : '<div class="project-icon"></div>'}
        <div class="info"><h4>${escapeHtml(h.title)}</h4><p>${escapeHtml(h.description || "")}</p><div class="stats">↓ ${(h.downloads || 0).toLocaleString()} downloads</div></div>
        <button class="btn primary small" onclick="installProject('${h.project_id || h.slug}','${escapeHtml(h.title)}','${type}')">Install</button>
      </article>`).join("")
    : '<div class="empty">No projects found</div>';
}

async function loadFeatured() {
  const plugins = document.getElementById("featured-plugins");
  const mods = document.getElementById("featured-mods");
  if (!plugins || !mods) return;
  try {
    const [pluginData, modData] = await Promise.all([
      (await fetch("/api/modrinth/featured?type=plugin")).json(),
      (await fetch("/api/modrinth/featured?type=mod")).json()
    ]);
    plugins.innerHTML = renderProjectCards(pluginData.hits || [], "plugin");
    mods.innerHTML = renderProjectCards(modData.hits || [], "mod");
  } catch (e) {
    plugins.innerHTML = '<div class="empty">Featured plugins unavailable</div>';
    mods.innerHTML = '<div class="empty">Featured mods unavailable</div>';
  }
}
document.getElementById("search-q").addEventListener("keydown", e => { if (e.key === "Enter") searchModrinth(); });

async function installProject(projectId, title, ptype) {
  const serverId = document.getElementById("search-server").value;
  if (!serverId) { 
    showToast("Please select a target server from the dropdown", "warning"); 
    return; 
  }
  
  showToast(`Finding latest version for ${title}...`, "success");
  const versions = await (await fetch(`/api/modrinth/versions/${projectId}`)).json();
  if (!versions.length) { 
    showToast("No compatible versions found", "error"); 
    return; 
  }
  
  const ver = versions[0];
  const file = (ver.files || []).find(f => f.primary) || (ver.files || [])[0];
  if (!file) { 
    showToast("No downloadable file found", "error"); 
    return; 
  }
  
  const target = ptype === "mod" ? "mods" : "plugins";
  const data = await (await fetch(`/api/server/${serverId}/install`, {
    method: "POST", headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ url: file.url, filename: file.filename, target })
  })).json();
  
  if (data.ok) showToast(`Successfully installed to /${target}/`, "success");
  else showToast(data.error || "Failed to install", "error");
}

async function loadManagerUpdate() {
  try {
    const data = await requestJson("/api/manager/update");
    document.getElementById("update-auto-check").checked = data.auto_check;
    document.getElementById("update-auto-install").checked = data.auto_install;
    document.getElementById("update-installed").textContent = data.installed
      ? `${data.installed.slice(0, 7)} from ${data.repo}`
      : `Unknown version of ${data.repo}`;
    renderUpdateLatest(data.latest, data.installed);
  } catch { /* settings tab can open before the panel finishes starting */ }
}

function renderUpdateLatest(latest, installed) {
  const badge = document.getElementById("update-badge");
  const detail = document.getElementById("update-detail");
  const apply = document.getElementById("btn-update-apply");
  if (!latest) {
    badge.textContent = "Not checked";
    badge.className = "badge offline";
    detail.hidden = true;
    apply.hidden = true;
    return;
  }
  const behind = latest.update_available;
  badge.textContent = behind ? (latest.count ? `${latest.count} update${latest.count === 1 ? "" : "s"}` : "Update available") : "Up to date";
  badge.className = `badge ${behind ? "type" : "online"}`;
  apply.hidden = !behind;
  detail.hidden = false;
  detail.innerHTML = `<div class="row"><span>Latest</span><span>${escapeHtml(latest.short)} · ${new Date(latest.date).toLocaleString()}</span></div>
    <div class="row"><span>Message</span><span>${escapeHtml(latest.message)}</span></div>
    ${latest.behind?.length ? `<ul class="update-log">${latest.behind.map(m => `<li>${escapeHtml(m)}</li>`).join("")}</ul>` : ""}`;
}

async function checkManagerUpdate() {
  const button = document.getElementById("btn-update-check");
  button.disabled = true;
  button.textContent = "Checking...";
  try {
    const data = await requestJson("/api/manager/update/check", { method: "POST" });
    if (!data.ok) throw new Error(data.error || "Check failed");
    renderUpdateLatest(data.latest, data.installed);
    showToast(data.latest.update_available ? "Update available" : "Already up to date", "success");
  } catch (error) {
    showToast(error.message, "error");
  } finally {
    button.disabled = false;
    button.textContent = "Check now";
  }
}

async function saveUpdateSettings() {
  await requestJson("/api/manager/update/settings", {
    method: "POST", headers: { "Content-Type": "application/json" },
    body: JSON.stringify({
      auto_check: document.getElementById("update-auto-check").checked,
      auto_install: document.getElementById("update-auto-install").checked
    })
  });
}

async function applyManagerUpdate(force) {
  if (getSettings().confirmActions && !force &&
      !confirm("Replace the manager's files with the latest version from GitHub? A copy of the current files is saved first.")) return;
  const button = document.getElementById("btn-update-apply");
  button.disabled = true;
  button.textContent = "Installing...";
  try {
    const data = await requestJson("/api/manager/update/apply", {
      method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ force: Boolean(force) })
    });
    if (!data.ok) {
      if (data.error?.includes("server is running") && confirm(`${data.error}\n\nUpdate anyway?`)) return applyManagerUpdate(true);
      throw new Error(data.error || "Update failed");
    }
    showToast(`Updated to ${data.short}. Restart the manager to load it.`, "success");
    document.getElementById("update-installed").textContent = `${data.short} (restart pending)`;
    loadManagerUpdate();
  } catch (error) {
    showToast(error.message, "error");
  } finally {
    button.disabled = false;
    button.textContent = "Install update";
  }
}

async function rollbackManagerUpdate() {
  if (getSettings().confirmActions && !confirm("Restore the manager files from the last snapshot?")) return;
  try {
    const data = await requestJson("/api/manager/update/rollback", { method: "POST" });
    if (!data.ok) throw new Error(data.error || "Rollback failed");
    showToast(`Restored ${data.files.length} files. Restart the manager.`, "success");
    loadManagerUpdate();
  } catch (error) {
    showToast(error.message, "error");
  }
}

function tickLiveClock() {
  const el = document.getElementById("live-clock");
  if (el) el.textContent = new Date().toLocaleTimeString([], { hour12: false });
}

// Init
initSettings();
loadHostMemory();
watchDevelopmentFiles();
setInterval(watchDevelopmentFiles, 1500);
tickLiveClock();
setInterval(tickLiveClock, 1000);
loadServers();
loadVersions();