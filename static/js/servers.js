// Server list, create form and folder import.

// The preview is a picture when a library logo is chosen, otherwise the generated initials.
function renderCreateLogo() {
  const preview = document.getElementById("create-logo-preview");
  if (!preview) return;
  let next;
  if (createLogo.library) {
    next = document.createElement("img");
    next.className = "server-avatar server-avatar-image";
    next.alt = "";
    next.src = createLogo.libraryUrl;
  } else {
    next = document.createElement("div");
    next.className = `server-avatar ${createLogo.style}`;
    next.textContent = createLogo.mark;
  }
  next.id = "create-logo-preview";
  preview.replaceWith(next);
}

function updateCreateLogo() {
  if (createLogo.library) return;  // a chosen picture does not depend on the name
  const name = document.getElementById("create-name")?.value.trim() || "MC";
  createLogo.mark = name.slice(0, 2).toUpperCase();
  renderCreateLogo();
}

function shuffleServerLogo() {
  const styles = ["avatar-lime", "avatar-blue", "avatar-amber", "avatar-red"];
  const marks = ["MC", "XP", "GG", "24", "OP", "SV"];
  createLogo.library = "";
  createLogo.libraryUrl = "";
  createLogo.style = styles[Math.floor(Math.random() * styles.length)];
  createLogo.mark = marks[Math.floor(Math.random() * marks.length)];
  renderCreateLogo();
}

function chooseCreateLogo() {
  openLogoPicker(logo => {
    createLogo.library = logo.ref;
    createLogo.libraryUrl = logo.url;
    renderCreateLogo();
  });
}

function renderServerSummary(servers) {
  const summary = document.getElementById("server-summary");
  if (!summary) return;
  const total = servers.length;
  const online = servers.filter(s => s.running).length;
  const totalRam = servers.reduce((sum, s) => sum + (Number(s.ram) || 2048), 0);
  const primaryPort = servers[0]?.port || 25565;
  const key = [total, online, totalRam, primaryPort].join("|");
  if (key === summaryKey) return;
  summaryKey = key;
  summary.innerHTML = `
    <div class="summary-pill">
      <span class="summary-label">Total Servers</span>
      <strong data-count-to="${total}">0</strong>
    </div>
    <div class="summary-pill online-pill">
      <span class="summary-label">Online Servers</span>
      <strong data-count-to="${online}">0</strong>
    </div>
    <div class="summary-pill">
      <span class="summary-label">Total Memory</span>
      <strong>${formatRam(totalRam)}</strong>
    </div>
    <div class="summary-pill">
      <span class="summary-label">Primary Port</span>
      <strong>${total ? primaryPort : "—"}</strong>
    </div>
  `;
  animateCounts(summary);
}

function animateCounts(root) {
  if (document.body.classList.contains("reduced-motion")) {
    root.querySelectorAll("[data-count-to]").forEach(el => { el.textContent = el.dataset.countTo; });
    return;
  }
  root.querySelectorAll("[data-count-to]").forEach(el => {
    const target = Number(el.dataset.countTo) || 0;
    const start = performance.now();
    const duration = 420;
    const step = now => {
      const p = Math.min(1, (now - start) / duration);
      const eased = 1 - Math.pow(1 - p, 3);
      el.textContent = Math.round(target * eased);
      if (p < 1) requestAnimationFrame(step);
    };
    requestAnimationFrame(step);
  });
}

let serverQuery = "";
let serverSort = "created";
try {
  const saved = JSON.parse(localStorage.getItem("mc-manager-list") || "{}");
  serverSort = ["created", "name", "status", "port"].includes(saved.sort) ? saved.sort : "created";
} catch {}

function sortServers(list) {
  const sorters = {
    created: (a, b) => String(b.created || "").localeCompare(String(a.created || "")),
    name: (a, b) => a.name.localeCompare(b.name, undefined, { sensitivity: "base" }),
    status: (a, b) => Number(b.running) - Number(a.running) || a.name.localeCompare(b.name),
    port: (a, b) => (Number(a.port) || 0) - (Number(b.port) || 0)
  };
  return [...list].sort(sorters[serverSort]);
}

function serverCardHtml(s, i) {
  const id = jsArg(s.id);
  const logo = s.logo?.file
    ? `<img class="server-avatar server-avatar-image" src="/api/server/${encodeURIComponent(s.id)}/logo/${encodeURIComponent(s.logo.file)}?v=${s.logo.rev || 0}" alt="" />`
    : `<div class="server-avatar ${escapeHtml(s.logo?.style || "avatar-lime")}" aria-hidden="true">${escapeHtml(s.logo?.mark || s.name.slice(0, 2).toUpperCase())}</div>`;
  const power = s.running
    ? '<svg viewBox="0 0 16 16" fill="currentColor" stroke="none"><rect x="3.5" y="3.5" width="9" height="9" rx="1.5"/></svg> Stop'
    : '<svg viewBox="0 0 16 16" fill="currentColor" stroke="none"><path d="M4 2.5v11l9-5.5-9-5.5z"/></svg> Start';
  return `
      <div class="server-card ${s.running ? "online" : "offline"}" style="--i:${i}" tabindex="0" role="button" aria-label="Open ${escapeHtml(s.name)}" onclick="openServer(${id})">
        <div class="card-top">
          <div class="server-name-line">${logo}<h3>${escapeHtml(s.name)}</h3></div>
          <div class="server-status">
            <span class="status-dot ${s.running ? "online" : "offline"}"></span>
            <span class="badge ${s.running ? "online" : "offline"}">${s.running ? "Online" : "Offline"}</span>
          </div>
        </div>
        <div class="meta">
          <span class="badge type">${escapeHtml(s.type)} ${escapeHtml(s.version)}</span>
          ${s.running && s.players ? `<span class="badge online" title="Players online">${s.players} online</span>` : ""}
          <span class="server-ping" data-ping-for="${escapeHtml(s.id)}">-- ms</span>
        </div>
        <div class="server-meta-grid">
          <div><span>RAM</span><strong>${formatRam(s.ram || 2048)}</strong></div>
          <div><span>Port</span><strong>${s.port || 25565}</strong></div>
          <div><span>Created</span><strong>${new Date(s.created || Date.now()).toLocaleDateString()}</strong></div>
        </div>
        <div class="card-live" data-live-for="${escapeHtml(s.id)}" data-ram-limit="${Number(s.ram) || 2048}" ${s.running ? "" : "hidden"}>
          <div class="live-row"><span>CPU</span><i class="bar"><b data-bar="cpu"></b></i><em data-val="cpu">-</em></div>
          <div class="live-row"><span>RAM</span><i class="bar"><b data-bar="ram"></b></i><em data-val="ram">-</em></div>
        </div>
        <div class="card-actions">
          <button class="mini-btn ${s.running ? "" : "success"}" onclick="event.stopPropagation(); togglePowerFromCard(this, ${id}, ${s.running})">${power}</button>
          <button class="mini-btn danger" onclick="event.stopPropagation(); deleteServerFromCard(this, ${id})">✕ Delete</button>
        </div>
      </div>`;
}

async function loadServers() {
  const el = document.getElementById("servers-list");
  if (!el) return;
  // Show placeholders only when there is nothing on screen yet; background refreshes swap content silently.
  if (!serversHtml) el.innerHTML = '<div class="skeleton-card"></div><div class="skeleton-card"></div><div class="skeleton-card"></div>';
  try {
    const res = await fetch("/api/servers");
    if (!res.ok) throw new Error("Failed to load servers");
    const servers = await res.json();
    renderServerSummary(servers);
    knownServers = servers;
    updateTitle(servers.filter(s => s.running).length);
    const query = serverQuery.trim().toLowerCase();
    const filtered = sortServers(servers.filter(s => {
      if (serverFilter === "online" && !s.running) return false;
      if (serverFilter === "offline" && s.running) return false;
      return !query || `${s.name} ${s.type} ${s.version} ${s.port}`.toLowerCase().includes(query);
    }));
    const html = filtered.length
      ? filtered.map(serverCardHtml).join("")
      : `<div class="empty-card dashed">
        <svg viewBox="0 0 16 16" width="28" height="28" fill="none" stroke="currentColor" stroke-width="1.3" stroke-linecap="round" stroke-linejoin="round"><rect x="2" y="3" width="12" height="4" rx="1"/><rect x="2" y="9" width="12" height="4" rx="1"/><circle cx="4.5" cy="5" r=".5" fill="currentColor" stroke="none"/><circle cx="4.5" cy="11" r=".5" fill="currentColor" stroke="none"/></svg>
        <p>${servers.length ? "No servers match your search or filter." : "No servers yet."}</p>
        <button class="btn primary small" onclick="switchTab('create')"><span>+</span> New Server</button>
      </div>`;
    if (html !== serversHtml) {
      el.classList.toggle("settled", serversSettled);
      el.innerHTML = html;
      serversHtml = html;
    }
    serversSettled = true;
    if (filtered.length) refreshServerPings(filtered);
  } catch (e) {
    serversHtml = "";
    el.innerHTML = `<div class="empty">Error: ${escapeHtml(e.message)}</div>`;
  }
}

// ----- live CPU and memory -----

const STAT_POINTS = 40;
const statHistory = {};

function sparkPoints(values, max) {
  if (values.length < 2) return "";
  const step = 120 / (STAT_POINTS - 1);
  const offset = (STAT_POINTS - values.length) * step;
  return values.map((value, i) => `${(offset + i * step).toFixed(1)},${(30 - Math.min(1, Math.max(0, value / (max || 1))) * 28).toFixed(1)}`).join(" ");
}

function applyStats(data) {
  Object.entries(data.servers).forEach(([sid, stat]) => {
    const history = statHistory[sid] || (statHistory[sid] = { cpu: [], ram: [] });
    history.cpu.push(stat.cpu ?? 0);
    history.ram.push(stat.ram_mb ?? 0);
    if (history.cpu.length > STAT_POINTS) { history.cpu.shift(); history.ram.shift(); }
  });
  Object.keys(statHistory).forEach(sid => { if (!data.servers[sid]) delete statHistory[sid]; });
  document.querySelectorAll(".card-live").forEach(card => {
    const stat = data.servers[card.dataset.liveFor];
    card.hidden = !stat;
    if (!stat) return;
    const limit = Number(card.dataset.ramLimit) || 2048;
    card.querySelector('[data-bar="cpu"]').style.width = `${Math.min(100, stat.cpu ?? 0)}%`;
    card.querySelector('[data-val="cpu"]').textContent = stat.cpu == null ? "-" : `${stat.cpu}%`;
    card.querySelector('[data-bar="ram"]').style.width = `${Math.min(100, ((stat.ram_mb ?? 0) / limit) * 100)}%`;
    card.querySelector('[data-val="ram"]').textContent = stat.ram_mb == null ? "-" : `${formatRam(stat.ram_mb)}`;
  });
  updateDetailStats(data.servers[currentServerId]);
}

function updateDetailStats(stat) {
  const panel = document.getElementById("live-stats");
  if (!panel) return;
  panel.hidden = !stat;
  if (!stat) return;
  document.getElementById("stat-cpu").textContent = stat.cpu == null ? "-" : `${stat.cpu}%`;
  document.getElementById("stat-ram").textContent = stat.ram_mb == null ? "-" : `${formatRam(stat.ram_mb)} / ${formatRam(currentServerRam)}`;
  document.getElementById("stat-uptime").textContent = formatUptime(stat.uptime);
  const history = statHistory[currentServerId] || { cpu: [], ram: [] };
  document.getElementById("spark-cpu").setAttribute("points", sparkPoints(history.cpu, 100));
  document.getElementById("spark-ram").setAttribute("points", sparkPoints(history.ram, currentServerRam));
}

async function pollStats() {
  if (document.hidden) return;
  const onServers = document.getElementById("tab-servers")?.classList.contains("active");
  const onDetail = document.getElementById("tab-detail")?.classList.contains("active") && currentServerId;
  if (!onServers && !onDetail) return;
  try { applyStats(await (await fetch("/api/stats")).json()); } catch {}
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
  if (!(await uiConfirm("Delete this server permanently? This cannot be undone.", { danger: true, confirmText: "Continue" }))) return;
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

const portHint = document.getElementById("port-hint");

const portHintText = portHint.textContent;

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
    whitelist: document.getElementById("create-whitelist").checked,
    accept_eula: document.getElementById("create-eula").checked
  };
  const status = document.getElementById("create-status");
  const btn = document.getElementById("btn-create");
  if (!name) {
    status.className = "status-msg show err";
    status.textContent = "Give the server a name first";
    document.getElementById("create-name").focus();
    return;
  }
  if (!options.accept_eula) {
    status.className = "status-msg show err";
    status.textContent = "Tick the box to accept the Minecraft EULA first";
    document.getElementById("create-eula").focus();
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
      body: JSON.stringify({ name, type, version, ram, logo: { mark: createLogo.mark, style: createLogo.style, library: createLogo.library }, ...options })
    });
    const data = await res.json();
    if (data.ok) {
      status.className = "status-msg show ok";
      status.textContent = `Created "${name}"`;
      document.getElementById("create-name").value = "";
      createLogo.library = "";
      createLogo.libraryUrl = "";
      updateCreateLogo();
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
