// The server detail page: header actions, tabs, players, Playit, anti-cheat and the command wiki.

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
  clearConsole();

  let s;
  try {
    const servers = await (await fetch("/api/servers")).json();
    s = servers.find(x => x.id === id);
  } catch (error) {
    showToast(`Could not load the server: ${error.message}`, "error");
  }
  if (!s) {
    if (currentServerId === id) currentServerId = null;
    stopConsolePolling();
    switchTab("servers");
    return;
  }
  currentServerRam = Number(s.ram) || 2048;
  currentServerPort = Number(s.port) || 25565;
  updateDetailStats(null);
  document.getElementById("detail-title").textContent = s.name;
  renderDetailLogo(s);
  document.getElementById("detail-info").innerHTML = `
    <div class="row"><span>Type</span><span>${escapeHtml(s.type)}</span></div>
    <div class="row"><span>Version</span><span>${escapeHtml(s.version)}</span></div>
    <div class="row"><span>RAM</span><span>${formatRam(s.ram || 2048)}</span></div>
    <div class="row"><span>Port</span><span>${s.port || 25565}</span></div>
    ${s.modpack ? `<div class="row"><span>Modpack</span><span title="${escapeHtml(s.modpack.name)} ${escapeHtml(s.modpack.version)}">${escapeHtml(s.modpack.name)} · ${Number(s.modpack.mods) || 0} mods${s.modpack.source === "curseforge" ? " · CurseForge" : ""}</span></div>` : ""}
    <div class="row"><span>Java</span><span id="detail-java">Checking...</span></div>
    <div class="row"><span>Ping</span><span id="detail-ping">Checking...</span></div>
    <div class="row"><span>Created</span><span>${new Date(s.created || Date.now()).toLocaleDateString()}</span></div>`;
  updateStatusBadge(s.running);
  updateDetailPing(s.id);
  loadServerJava(s.version);
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
  let next;
  if (server.logo?.file) {
    next = document.createElement("img");
    next.className = "server-avatar server-avatar-image";
    next.alt = `${server.name} logo`;
    next.src = `/api/server/${encodeURIComponent(server.id)}/logo/${encodeURIComponent(server.logo.file)}?v=${server.logo.rev || 0}`;
  } else {
    next = document.createElement("div");
    next.className = `server-avatar ${server.logo?.style || "avatar-lime"}`;
    next.textContent = server.logo?.mark || server.name.slice(0, 2).toUpperCase();
  }
  next.id = "detail-logo";
  target.replaceWith(next);
}

async function importServerLogo() {
  const input = document.getElementById("logo-upload");
  if (!input.files?.length || !currentServerId) return;
  const form = new FormData();
  form.append("logo", input.files[0]);
  const data = await (await fetch(`/api/server/${currentServerId}/logo`, { method: "POST", body: form })).json();
  if (!data.ok) { showToast(data.error || "Logo import failed", "error"); return; }
  showToast("Server logo imported", "success");
  await refreshServerLogo();
  input.value = "";
}

async function refreshServerLogo() {
  const server = (await (await fetch("/api/servers")).json()).find(item => item.id === currentServerId);
  if (server) { renderDetailLogo(server); loadServers(); }
}

function pickServerLogo() {
  if (!currentServerId) return;
  openLogoPicker(async logo => {
    try {
      await requestJson(`/api/server/${encodeURIComponent(currentServerId)}/logo`, { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ logo: logo.ref }) });
      showToast("Server logo changed", "success");
      await refreshServerLogo();
    } catch (error) {
      showToast(error.message, "error");
    }
  });
}

async function loadServerJava(version) {
  const cell = document.getElementById("detail-java");
  try {
    const data = await requestJson(`/api/java?version=${encodeURIComponent(version)}`);
    if (!cell) return;
    if (!data.found) cell.innerHTML = '<span class="warn-text">Not installed</span>';
    else if (!data.ok) cell.innerHTML = `<span class="warn-text">Java ${data.major}, needs ${data.required}+</span>`;
    else cell.textContent = `Java ${data.major}`;
    if (cell) cell.title = data.path || "";
  } catch { if (cell) cell.textContent = "Unknown"; }
}

function updateStatusBadge(running) {
  const badge = document.getElementById("detail-status");
  badge.textContent = running ? "Online" : "Offline";
  badge.className = "badge " + (running ? "online" : "offline");
  if (!running) updateDetailStats(null);
  window.mcScene?.setServerState({ running });
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
    document.getElementById("command-list").innerHTML = filtered.map(([name, group, usage, description]) => `<article class="command-entry"><div><span class="badge type">${group}</span><h3>/${name}</h3><p>${description}</p><code>${usage}</code></div><button class="btn small" onclick="useWikiCommand(${jsArg(usage)})">Use in console</button></article>`).join("") || '<div class="empty">No commands match this search.</div>';
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

async function startSelected(button) {
  if (!currentServerId) return;
  await withBusy(button, async () => {
    try {
      const data = await (await fetch(`/api/server/${currentServerId}/start`, {
        method: "POST", headers: { "Content-Type": "application/json" }, body: "{}"
      })).json();
      if (data.ok) {
        showToast("Server started", "success");
        updateStatusBadge(true);
        startConsolePolling();
      } else {
        showToast(data.message || data.error || "Failed to start", "error");
      }
    } catch (error) {
      showToast(error.message, "error");
    }
  });
}

async function stopSelected(button) {
  if (!currentServerId) return;
  await withBusy(button, async () => {
    try {
      const data = await (await fetch(`/api/server/${currentServerId}/stop`, { method: "POST" })).json();
      showToast(data.message || data.error || "Server stopping", data.ok ? "success" : "warning");
      if (data.ok) updateStatusBadge(false);
    } catch (error) {
      showToast(error.message, "error");
    }
  });
}

async function deleteSelected(button) {
  if (!currentServerId) return;
  if (!(await uiConfirm("Delete this server forever? Its world, plugins and settings are removed. Backups are removed too. This cannot be undone.", { title: "Delete server", confirmText: "Delete", danger: true }))) return;
  await withBusy(button, async () => {
    try {
      const data = await (await fetch(`/api/server/${currentServerId}/delete`, { method: "POST" })).json();
      if (!data.ok) throw new Error(data.error || "Could not delete this server");
      showToast("Server deleted", "success");
      currentServerId = null;
      stopConsolePolling();
      switchTab("servers");
    } catch (error) {
      showToast(error.message, "error");
    }
  });
}

async function loadPlayers() {
  if (!currentServerId) return;
  const [data, activeData] = await Promise.all([
    (await fetch(`/api/server/${currentServerId}/players`)).json(),
    (await fetch(`/api/server/${currentServerId}/active-players`)).json()
  ]);
  renderActivePlayers(activeData.players || data.active || []);
  const fmt = (list, action, label) => {
    if (!list?.length) return '<div class="empty" style="padding:0.4rem">Empty</div>';
    return list.map(p => {
      const name = p.name || p.uuid || JSON.stringify(p);
      return `<div class="item"><span>${escapeHtml(name)}</span>${p.name ? `<button class="mini-btn" data-perm="console" onclick="playerActionFor(${jsArg(action)}, ${jsArg(p.name)})">${label}</button>` : ""}</div>`;
    }).join("");
  };
  document.getElementById("ops-list").innerHTML = fmt(data.ops, "deop", "DeOP");
  document.getElementById("wl-list").innerHTML = fmt(data.whitelist, "whitelist_remove", "Remove");
  document.getElementById("ban-list").innerHTML = fmt(data.banned, "pardon", "Pardon");
  loadPlaytime();
}

function playerActionFor(action, name) {
  document.getElementById("player-name").value = name;
  playerAction(action);
}

async function loadPlaytime() {
  const list = document.getElementById("playtime-list");
  try {
    const data = await requestJson(`/api/server/${currentServerId}/playtime`);
    const top = data.players[0]?.seconds || 1;
    list.innerHTML = data.players.length ? data.players.map((player, i) => `
      <div class="playtime-row">
        <span class="rank">${i + 1}</span>
        <strong>${escapeHtml(player.name)}${player.online ? ' <i class="status-dot online"></i>' : ""}</strong>
        <i class="bar"><b style="width:${Math.max(3, player.seconds / top * 100)}%"></b></i>
        <span class="time">${formatUptime(player.seconds)}</span>
        <small>${player.sessions || 0} session${player.sessions === 1 ? "" : "s"}</small>
      </div>`).join("") : '<div class="empty">No sessions recorded yet</div>';
  } catch { /* shown again next time the tab opens */ }
}

async function renameServer() {
  const current = document.getElementById("detail-title").textContent;
  const name = await uiAsk({ title: "Rename server", confirmText: "Rename", input: { value: current, required: true } });
  if (!name || name === current) return;
  try {
    const data = await requestJson(`/api/server/${currentServerId}/rename`, {
      method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ name })
    });
    if (!data.ok) throw new Error(data.error);
    document.getElementById("detail-title").textContent = data.name;
    serversHtml = "";
    showToast("Renamed", "success");
  } catch (error) {
    showToast(error.message, "error");
  }
}

async function cloneServer(button) {
  const current = document.getElementById("detail-title").textContent;
  const name = await uiAsk({ title: "Duplicate server", message: "Copies the world, plugins and settings. The copy gets its own port, no Playit secret and RCON off. The original must be stopped.", confirmText: "Duplicate", input: { value: `${current} (copy)`, required: true } });
  if (!name) return;
  await withBusy(button, async () => {
    try {
      const data = await requestJson(`/api/server/${currentServerId}/clone`, {
        method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ name })
      });
      if (!data.ok) throw new Error(data.error);
      showToast(`Created "${data.name}" on port ${data.port}`, "success");
      currentServerId = null;
      stopConsolePolling();
      switchTab("servers");
    } catch (error) {
      showToast(error.message, "error");
    }
  });
}

function copyAddress() {
  copyText(`localhost:${currentServerPort}`);
}

function renderActivePlayers(players) {
  const list = document.getElementById("active-player-list");
  const count = document.getElementById("active-player-count");
  if (!list || !count) return;
  count.textContent = `${players.length} online`;
  count.className = `badge ${players.length ? "online" : "offline"}`;
  window.mcScene?.setServerState({ players: players.length });
  list.innerHTML = players.length
    ? players.map(name => `<button class="active-player" onclick="selectActivePlayer(${jsArg(name)})"><span class="status-dot online"></span><strong>${escapeHtml(name)}</strong><span>Use</span></button>`).join("")
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

async function reloadComponents() {
  const data = await (await fetch(`/api/server/${currentServerId}/reload-components`, { method: "POST" })).json();
  showToast(data.message || data.error, data.ok ? "success" : "warning");
}

async function restartSelected(button) {
  if (!currentServerId) return;
  if (!(await uiConfirm("Restart this server now? Players will be disconnected.", { title: "Restart server", confirmText: "Restart" }))) return;
  await withBusy(button, async () => {
    try {
      const data = await (await fetch(`/api/server/${currentServerId}/restart`, { method: "POST" })).json();
      showToast(data.message || data.error, data.ok ? "success" : "error");
      if (data.ok) { updateStatusBadge(true); startConsolePolling(); }
    } catch (error) {
      showToast(error.message, "error");
    }
  });
}
