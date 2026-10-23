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

let lastStats = null;
let pingedKey = "";
let livePlayerTotal = null;

function serverPlayerTotal(servers) {
  if (livePlayerTotal != null) return livePlayerTotal;
  return servers.reduce((sum, server) => sum + (Number(server.players) || 0), 0);
}

function countNodes(root) {
  if (root.matches?.("[data-count-to]")) return [root];
  return [...root.querySelectorAll("[data-count-to]")];
}

function findingLine(servers) {
  if (!servers.length) return "No worlds yet.";
  const running = servers.filter(server => server.running);
  const players = serverPlayerTotal(servers);
  const playerText = players === 1 ? "1 player" : `${players} players`;
  let longest = "";
  if (lastStats?.servers) {
    let best = null;
    running.forEach(server => {
      const up = lastStats.servers[server.id]?.uptime;
      if (up != null && (best == null || up > best.up)) best = { name: server.name, up };
    });
    if (best) longest = ` · ${best.name} has been up ${formatUptime(best.up)}`;
  }
  return `${running.length ? `${running.length} running` : "None running"} · ${playerText}${longest}`;
}

function renderServerSummary(servers) {
  const finding = document.getElementById("server-finding");
  if (finding) finding.textContent = findingLine(servers);
  const summary = document.getElementById("server-summary");
  if (!summary) return;
  if (!summary.querySelector(".host-strip")) {
    summary.innerHTML = `
      <div class="host-strip" id="fleet-load">
        <div class="host-meter"><span>CPU</span><i class="bar"><b data-host="cpu"></b></i><em data-host-val="cpu">…</em></div>
        <div class="host-meter"><span>Memory</span><i class="bar"><b data-host="ram"></b></i><em data-host-val="ram">…</em></div>
        <div class="host-meter"><span>Disk</span><i class="bar"><b data-host="disk"></b></i><em data-host-val="disk">…</em></div>
      </div>`;
  }
  if (lastStats) paintHost(lastStats.host);
}

function animateCounts(root) {
  const nodes = countNodes(root);
  if (document.body.classList.contains("reduced-motion")) {
    nodes.forEach(el => { el.textContent = el.dataset.countTo; });
    return;
  }
  nodes.forEach(el => {
    const target = Number(el.dataset.countTo) || 0;
    const current = Number(el.textContent);
    const from = Number.isFinite(current) ? current : 0;
    if (from === target) {
      el.textContent = String(target);
      return;
    }
    const start = performance.now();
    const duration = 420;
    const step = now => {
      if (el.dataset.countTo !== String(target)) return;
      const p = Math.min(1, (now - start) / duration);
      const eased = 1 - Math.pow(1 - p, 3);
      el.textContent = String(Math.round(from + (target - from) * eased));
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

function namesLine(names) {
  const list = (names || []).map(name => String(name)).filter(Boolean);
  if (!list.length) return "No one online";
  const shown = list.slice(0, 3).join(", ");
  const extra = list.length - 3;
  return extra > 0 ? `${shown} +${extra}` : shown;
}

function formatRamPair(used, limitMb) {
  const limit = Number(limitMb) || 2048;
  const figures = value => (value / 1024).toFixed(1);
  if (limit < 1024) return used == null ? `… of ${Math.round(limit)} MB` : `${Math.round(used)} of ${Math.round(limit)} MB`;
  return used == null ? `… of ${figures(limit)} GB` : `${figures(used)} of ${figures(limit)} GB`;
}

function setServerFilter(filter) {
  if (!["all", "online", "offline"].includes(filter)) return;
  serverFilter = filter;
  document.querySelectorAll("#server-filter .filter-pill").forEach(pill => pill.classList.toggle("active", pill.dataset.filter === filter));
}

function serverCardHtml(s) {
  const id = jsArg(s.id);
  const banner = s.banner?.file
    ? `<img src="/api/server/${encodeURIComponent(s.id)}/banner/${encodeURIComponent(s.banner.file)}?v=${s.banner.rev || 0}" alt="" />`
    : "";
  const who = s.running ? namesLine(s.player_names) : "Stopped";
  const action = s.running
    ? `<button class="btn small" type="button" onclick="event.stopPropagation(); openServer(${id})">Console</button>`
    : `<button class="btn small" type="button" onclick="event.stopPropagation(); togglePowerFromCard(this, ${id}, false)">Start</button>`;
  return `
      <article class="world-row ${s.running ? "is-running" : ""}" tabindex="0" aria-label="Open ${escapeHtml(s.name)}" onclick="openServer(${id})">
        <div class="world-banner">${banner}</div>
        <div class="world-name"><strong>${escapeHtml(s.name)}</strong><span>${escapeHtml(s.type)} ${escapeHtml(s.version)}</span></div>
        <div class="world-port"><span>${s.port || 25565}</span><span class="world-ping${s.running ? "" : " is-idle"}" data-ping-for="${escapeHtml(s.id)}">${s.running ? "…" : ""}</span></div>
        <div class="world-who" data-slot="who">${escapeHtml(who)}</div>
        <div class="world-ram${s.running ? "" : " is-idle"}" ${s.running ? `data-live-for="${escapeHtml(s.id)}" data-ram-limit="${Number(s.ram) || 2048}"` : ""}><span data-val="ram">${s.running ? formatRamPair(null, s.ram || 2048) : formatRam(s.ram || 2048)}</span><i class="bar" hidden><b data-bar="ram"></b></i></div>
        ${action}
      </article>`;
}

function updateFilterCounts(queried) {
  const online = queried.filter(s => s.running).length;
  const set = (id, value) => {
    const node = document.getElementById(id);
    if (node) node.textContent = String(value);
  };
  set("count-all", queried.length);
  set("count-online", online);
  set("count-offline", queried.length - online);
}

function paintServerList(servers) {
  const el = document.getElementById("servers-list");
  if (!el) return;
  renderServerSummary(servers);
  updateTitle(servers.filter(s => s.running).length);
  const query = serverQuery.trim().toLowerCase();
  const queried = servers.filter(s => !query || `${s.name} ${s.type} ${s.version} ${s.port}`.toLowerCase().includes(query));
  updateFilterCounts(queried);
  const filtered = sortServers(queried.filter(s => {
    if (serverFilter === "online" && !s.running) return false;
    if (serverFilter === "offline" && s.running) return false;
    return true;
  }));
  const html = filtered.length
    ? filtered.map(serverCardHtml).join("")
    : `<div class="empty-card dashed">
        <svg viewBox="0 0 16 16" width="28" height="28" fill="none" stroke="currentColor" stroke-width="1.3" stroke-linecap="round" stroke-linejoin="round"><rect x="2" y="3" width="12" height="4" rx="1"/><rect x="2" y="9" width="12" height="4" rx="1"/><circle cx="4.5" cy="5" r=".5" fill="currentColor" stroke="none"/><circle cx="4.5" cy="11" r=".5" fill="currentColor" stroke="none"/></svg>
        <p>${servers.length ? "No servers match your search or filter." : "No servers yet."}</p>
        ${servers.length ? "" : `<button class="btn primary small" data-perm="manage" onclick="switchTab('create')"><span>+</span> New Server</button>`}
      </div>`;
  if (html !== serversHtml) {
    el.classList.toggle("settled", serversSettled);
    el.innerHTML = html;
    serversHtml = html;
    pingedKey = "";
    if (lastStats) paintLiveCards(lastStats);
  }
  serversSettled = true;
  const running = filtered.filter(s => s.running);
  const key = running.map(s => s.id).sort().join(",");
  if (key !== pingedKey) {
    pingedKey = key;
    if (running.length) refreshServerPings(running);
  }
}

// Search, sort and the status filter paint from the list already on screen.
function repaintServers() {
  if (!serversHtml && !knownServers.length) return;
  paintServerList(knownServers);
}

async function loadServers() {
  const el = document.getElementById("servers-list");
  if (!el) return;
  // Show placeholders only when there is nothing on screen yet; background refreshes swap content silently.
  if (!serversHtml) el.innerHTML = '<div class="loading">Loading…</div>';
  try {
    const res = await fetch("/api/servers");
    if (!res.ok) throw new Error("Failed to load servers");
    knownServers = await res.json();
    livePlayerTotal = null;
    paintServerList(knownServers);
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

function paintMeter(bar, label, percent, text) {
  if (!bar || !label) return;
  const value = percent == null ? 0 : Math.max(0, Math.min(100, percent));
  bar.style.width = percent == null ? "0" : `${value}%`;
  bar.parentElement.classList.toggle("hot", percent != null && percent >= 90);
  label.textContent = text;
}

function paintHost(host) {
  const root = document.getElementById("fleet-load");
  if (!root || !host) return;
  root.classList.toggle("is-live", host.cpu != null || host.ram_percent != null || host.disk_percent != null);
  paintMeter(root.querySelector('[data-host="cpu"]'), root.querySelector('[data-host-val="cpu"]'), host.cpu, host.cpu == null ? "…" : `${Math.round(host.cpu)}%`);
  paintMeter(root.querySelector('[data-host="ram"]'), root.querySelector('[data-host-val="ram"]'), host.ram_percent, host.ram_percent == null ? "…" : `${Math.round(host.ram_percent)}%`);
  paintMeter(root.querySelector('[data-host="disk"]'), root.querySelector('[data-host-val="disk"]'), host.disk_percent, host.disk_percent == null ? "…" : `${Math.round(host.disk_percent)}%`);
}

function paintLiveCards(data) {
  if (!data?.servers) return;
  document.querySelectorAll("[data-live-for]").forEach(card => {
    const stat = data.servers[card.dataset.liveFor];
    if (!stat) return;
    const limit = Number(card.dataset.ramLimit) || 2048;
    const ramPct = limit ? ((stat.ram_mb ?? 0) / limit) * 100 : 0;
    const label = card.querySelector("[data-val='ram']");
    paintMeter(card.querySelector('[data-bar="ram"]'), label, stat.ram_mb == null ? null : ramPct, formatRamPair(stat.ram_mb, limit));
    card.classList.toggle("is-hot", stat.ram_mb != null && ramPct >= 90);
    const server = knownServers.find(item => item.id === card.dataset.liveFor);
    if (server && stat.players != null) server.players = stat.players;
    if (server && stat.player_names) server.player_names = stat.player_names;
    const who = card.parentElement?.querySelector("[data-slot='who']");
    if (who && stat.player_names) who.textContent = namesLine(stat.player_names);
  });
  if (knownServers.length) {
    livePlayerTotal = knownServers.reduce((sum, server) => sum + (server.running ? Number(server.players) || 0 : 0), 0);
    const finding = document.getElementById("server-finding");
    if (finding) finding.textContent = findingLine(knownServers);
  }
  updateDetailStats(data.servers[currentServerId]);
  paintHost(data.host);
}

function applyStats(data) {
  lastStats = data;
  Object.entries(data.servers).forEach(([sid, stat]) => {
    const history = statHistory[sid] || (statHistory[sid] = { cpu: [], ram: [] });
    history.cpu.push(stat.cpu ?? 0);
    history.ram.push(stat.ram_mb ?? 0);
    if (history.cpu.length > STAT_POINTS) { history.cpu.shift(); history.ram.shift(); }
  });
  Object.keys(statHistory).forEach(sid => { if (!data.servers[sid]) delete statHistory[sid]; });
  paintLiveCards(data);
}

function updateDetailStats(stat) {
  const panel = document.getElementById("live-stats");
  if (!panel) return;
  panel.hidden = !stat;
  if (!stat) return;
  const cpu = document.getElementById("stat-cpu");
  const ram = document.getElementById("stat-ram");
  const ramPct = currentServerRam ? ((stat.ram_mb ?? 0) / currentServerRam) * 100 : 0;
  cpu.textContent = stat.cpu == null ? "-" : `${stat.cpu}%`;
  cpu.classList.toggle("warn-text", (stat.cpu ?? 0) >= 90);
  ram.textContent = stat.ram_mb == null ? "-" : `${formatRam(stat.ram_mb)} / ${formatRam(currentServerRam)}`;
  ram.classList.toggle("warn-text", stat.ram_mb != null && ramPct >= 90);
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
        node.classList.remove("ping-good", "ping-mid", "ping-bad", "is-idle");
        if (data.ping == null) {
          node.textContent = "no reply";
          node.classList.add("ping-bad");
          return;
        }
        node.textContent = `${data.ping} ms`;
        node.classList.add(data.ping < 80 ? "ping-good" : data.ping < 180 ? "ping-mid" : "ping-bad");
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
    button.textContent = "Delete";
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
      ? `${formatRam(data.total)} installed, ${formatRam(data.available)} free right now. Leave 1 to 2 GB for the rest of the system.`
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
  document.getElementById("review-port").textContent = document.getElementById("create-port").value || "…";
  document.getElementById("review-ram").textContent = formatRam(currentCreateRam());
  document.getElementById("review-slots").textContent = document.getElementById("create-max-players").value;
  paintVersionJava(version);
}

let versionJavaToken = 0;

async function paintVersionJava(version) {
  const hint = document.getElementById("version-java");
  if (!hint) return;
  const token = ++versionJavaToken;
  if (!version) { hint.textContent = ""; hint.classList.remove("hint-warn"); return; }
  try {
    const data = await requestJson(`/api/java?version=${encodeURIComponent(version)}`);
    if (token !== versionJavaToken) return;
    if (data.required == null) { hint.textContent = ""; hint.classList.remove("hint-warn"); return; }
    hint.classList.toggle("hint-warn", !data.ok);
    if (data.ok) hint.textContent = `Java ${data.major} on this PC can run it. This version needs Java ${data.required}.`;
    else if (data.found) hint.textContent = `This version needs Java ${data.required}. This PC has Java ${data.major}.`;
    else hint.textContent = `This version needs Java ${data.required}. No Java was found on this PC.`;
  } catch {
    if (token === versionJavaToken) hint.textContent = "";
  }
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
