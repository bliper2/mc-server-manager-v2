let mapServerId = null;
let mapInstance = null;
let mapPollTimer = null;
let mapFrame = null;
let playerMarkers = new Map();
let structureLayer = null;
let trailLayer = null;
let placingMarker = false;
let selectedPlayer = null;
let playerFilter = "";
let lastPlayers = [];
let activeDimension = "overworld";
let followingPlayer = false;
let structureKey = "";

const MAP_POLL_MS = 1000;
const LERP = 0.18;
const TRAIL_LENGTH = 14;

const PLAYER_COLORS = ["#b9f227", "#8ec8ff", "#f4bb52", "#ff766d", "#c084fc", "#34d399", "#f472b6", "#38bdf8"];

function toLatLng(x, z) {
  return [-z, x];
}

function playerColor(name) {
  let hash = 0;
  for (let i = 0; i < name.length; i++) hash = (hash * 31 + name.charCodeAt(i)) >>> 0;
  return PLAYER_COLORS[hash % PLAYER_COLORS.length];
}

function normalizeDimension(dim) {
  return (dim || "overworld").replace("minecraft:", "");
}

function initMap() {
  if (mapInstance) return mapInstance;
  mapInstance = L.map("map-canvas", {
    crs: L.CRS.Simple,
    minZoom: -4,
    maxZoom: 4,
    zoomControl: true,
    attributionControl: false,
    preferCanvas: true
  });
  mapInstance.setView(toLatLng(0, 0), -1);

  const Grid = L.GridLayer.extend({
    createTile(coords) {
      const tile = document.createElement("canvas");
      const size = this.getTileSize();
      tile.width = size.x;
      tile.height = size.y;
      const ctx = tile.getContext("2d");
      const style = getComputedStyle(document.body);
      ctx.strokeStyle = style.getPropertyValue("--line").trim() || "#2e3335";
      ctx.fillStyle = style.getPropertyValue("--muted").trim() || "#99a1a3";
      ctx.lineWidth = 1;
      ctx.strokeRect(0.5, 0.5, size.x - 1, size.y - 1);
      ctx.font = "10px monospace";
      const world = this._map.unproject(coords.scaleBy(size), coords.z);
      ctx.fillText(`${Math.round(world.lng)}, ${Math.round(-world.lat)}`, 6, 14);
      return tile;
    }
  });
  new Grid({ tileSize: 128 }).addTo(mapInstance);

  structureLayer = L.layerGroup().addTo(mapInstance);
  trailLayer = L.layerGroup().addTo(mapInstance);

  mapInstance.on("click", event => {
    if (!placingMarker) return;
    placeStructure(Math.round(event.latlng.lng), Math.round(-event.latlng.lat));
  });
  mapInstance.on("mousemove", event => {
    const readout = document.getElementById("map-coords");
    if (readout) readout.textContent = `X ${Math.round(event.latlng.lng)}  Z ${Math.round(-event.latlng.lat)}`;
  });
  return mapInstance;
}

function playerIconHtml(player) {
  const max = 20;
  const ratio = Math.max(0, Math.min(1, (player.health ?? max) / max));
  const state = ratio > 0.6 ? "ok" : ratio > 0.3 ? "hurt" : "critical";
  const color = playerColor(player.name);
  const facing = typeof player.yaw === "number" ? (player.yaw + 180) % 360 : null;
  return `<div class="player-pin ${selectedPlayer === player.name ? "selected" : ""}" style="--pc:${color}">
        ${facing !== null ? `<span class="player-pin-facing" style="transform:rotate(${Math.round(facing)}deg)"></span>` : ""}
        <span class="player-pin-dot"></span>
        <span class="player-pin-name">${escapeHtml(player.name)}</span>
        <span class="player-pin-health ${state}"><i style="width:${Math.round(ratio * 100)}%"></i></span>
      </div>`;
}

function playerIcon(html) {
  return L.divIcon({ className: "player-pin-wrap", html, iconSize: null });
}

function renderPlayers(players) {
  const seen = new Set();
  players.forEach(player => {
    seen.add(player.name);
    const dim = normalizeDimension(player.dimension);
    const target = toLatLng(player.x, player.z);
    let entry = playerMarkers.get(player.name);
    const inView = dim === activeDimension;
    if (!entry) {
      const iconHtml = playerIconHtml(player);
      const marker = L.marker(target, { icon: playerIcon(iconHtml), keyboard: true });
      marker.on("click", () => openPlayerDrawer(player.name));
      const trail = L.polyline([], { color: playerColor(player.name), weight: 2, opacity: 0.35, dashArray: "1 6" });
      entry = { marker, current: target.slice(), target, trail, points: [], dim, inView: false, iconHtml };
      playerMarkers.set(player.name, entry);
    } else {
      entry.target = target;
      const iconHtml = playerIconHtml(player);
      if (iconHtml !== entry.iconHtml) {
        entry.iconHtml = iconHtml;
        entry.marker.setIcon(playerIcon(iconHtml));
      }
    }
    if (entry.dim !== dim) { entry.points = []; entry.trail.setLatLngs([]); }
    entry.dim = dim;
    entry.data = player;
    if (inView && !entry.inView) { entry.marker.addTo(mapInstance); entry.trail.addTo(trailLayer); entry.current = target.slice(); entry.marker.setLatLng(entry.current); }
    if (!inView && entry.inView) { mapInstance.removeLayer(entry.marker); trailLayer.removeLayer(entry.trail); }
    entry.inView = inView;
    if (inView) {
      entry.points.push(target);
      if (entry.points.length > TRAIL_LENGTH) entry.points.shift();
      entry.trail.setLatLngs(entry.points);
    }
  });
  [...playerMarkers.keys()].forEach(name => {
    if (seen.has(name)) return;
    const entry = playerMarkers.get(name);
    if (entry.inView) { mapInstance.removeLayer(entry.marker); trailLayer.removeLayer(entry.trail); }
    playerMarkers.delete(name);
  });
}

function animate() {
  mapFrame = requestAnimationFrame(animate);
  let followTarget = null;
  playerMarkers.forEach(entry => {
    if (!entry.inView) return;
    const [lat, lng] = entry.current;
    const [tLat, tLng] = entry.target;
    if (Math.abs(tLat - lat) >= 0.01 || Math.abs(tLng - lng) >= 0.01) {
      entry.current = [lat + (tLat - lat) * LERP, lng + (tLng - lng) * LERP];
      entry.marker.setLatLng(entry.current);
    }
    if (followingPlayer && entry.data && entry.data.name === selectedPlayer) followTarget = entry.current;
  });
  if (followTarget && mapInstance) {
    const center = mapInstance.getCenter();
    if (Math.hypot(center.lat - followTarget[0], center.lng - followTarget[1]) > 0.5) {
      mapInstance.panTo(followTarget, { animate: true, duration: 0.4, easeLinearity: 0.5 });
    }
  }
}

const STRUCTURE_GLYPH = { base: "⌂", farm: "☘", portal: "◈", shop: "♦", spawn: "★" };

function renderStructures(markers) {
  const key = JSON.stringify(markers);
  if (key === structureKey) return;
  structureKey = key;
  structureLayer.clearLayers();
  markers.forEach(marker => {
    L.marker(toLatLng(marker.x, marker.z), {
      icon: L.divIcon({
        className: "structure-pin-wrap",
        html: `<div class="structure-pin" data-kind="${escapeHtml(marker.kind)}">
          <strong><span class="structure-glyph">${STRUCTURE_GLYPH[marker.kind] || "■"}</span>${escapeHtml(marker.label)}</strong>
          <small>${escapeHtml(marker.kind)}${marker.owner ? ` · ${escapeHtml(marker.owner)}` : ""}</small>
          <small class="structure-coords">${marker.x}, ${marker.z}</small>
          <button class="mini-btn danger" onclick="removeStructure(${escapeHtml(JSON.stringify(String(marker.id)))})">Remove</button></div>`,
        iconSize: null
      })
    }).addTo(structureLayer);
  });
}

function renderOnlineList(players) {
  const list = document.getElementById("map-player-list");
  const count = document.getElementById("map-player-count");
  if (!list) return;
  const filtered = players.filter(p => p.name.toLowerCase().includes(playerFilter));
  count.textContent = `${players.length} online`;
  count.className = `badge ${players.length ? "online" : "offline"}`;
  list.innerHTML = filtered.length
    ? filtered.map(p => {
        const dim = normalizeDimension(p.dimension);
        return `<button class="active-player" onclick="openPlayerDrawer(${escapeHtml(JSON.stringify(String(p.name)))})">
        <span class="status-dot online" style="background:${playerColor(p.name)};box-shadow:0 0 8px ${playerColor(p.name)}"></span><strong>${escapeHtml(p.name)}</strong>
        <span>${Math.round(p.x)}, ${Math.round(p.z)}${dim !== "overworld" ? ` · ${dim.replace("the_", "")}` : ""}</span></button>`;
      }).join("")
    : `<div class="empty">${players.length ? "No match" : "Nobody online"}</div>`;
}

function centerMap() {
  const inView = [...playerMarkers.values()].filter(e => e.inView);
  if (!inView.length || !mapInstance) return;
  const bounds = L.latLngBounds(inView.map(e => e.target));
  mapInstance.flyToBounds(bounds.pad(0.35), { animate: true, duration: 0.5, maxZoom: 1 });
}

function setDimension(dim) {
  activeDimension = dim;
  document.querySelectorAll(".dim-tab").forEach(tab => tab.classList.toggle("active", tab.dataset.dim === dim));
  renderPlayers(lastPlayers);
}

function toggleFollow() {
  followingPlayer = !followingPlayer;
  const btn = document.getElementById("drawer-follow");
  if (btn) btn.classList.toggle("active", followingPlayer);
  if (followingPlayer) {
    const player = lastPlayers.find(p => p.name === selectedPlayer);
    if (player) {
      const dim = normalizeDimension(player.dimension);
      if (dim !== activeDimension) setDimension(dim);
      mapInstance.setZoom(1);
    }
  }
}

function logAction(message, kind = "info") {
  const feed = document.getElementById("map-log");
  if (!feed) return;
  const stamp = new Date().toLocaleTimeString([], { hour12: false });
  const row = document.createElement("div");
  row.className = `log-row ${kind}`;
  row.textContent = `${stamp}  ${message}`;
  feed.prepend(row);
  while (feed.children.length > 80) feed.lastChild.remove();
}

async function pollMap() {
  if (!mapServerId) return;
  try {
    const data = await requestJson(`/api/server/${mapServerId}/map`);
    const status = document.getElementById("map-status");
    if (data.error) {
      status.textContent = data.error;
      status.className = "badge offline";
    } else {
      status.textContent = data.running ? "Live" : "Offline";
      status.className = `badge ${data.running ? "online" : "offline"}`;
    }
    lastPlayers = data.players || [];
    renderPlayers(lastPlayers);
    renderStructures(data.markers || []);
    renderOnlineList(lastPlayers);
    if (selectedPlayer) refreshDrawerStats();
  } catch (error) {
    logAction(error.message, "error");
  }
}

function startMapPolling() {
  stopMapPolling();
  pollMap();
  mapPollTimer = setInterval(pollMap, MAP_POLL_MS);
  if (mapFrame === null) animate();
}

function stopMapPolling() {
  if (mapPollTimer) clearInterval(mapPollTimer);
  mapPollTimer = null;
  if (mapFrame !== null) cancelAnimationFrame(mapFrame);
  mapFrame = null;
}

async function loadMapServers() {
  const select = document.getElementById("map-server");
  let servers = [];
  try {
    servers = await (await fetch("/api/servers")).json();
  } catch (error) {
    logAction(`Could not load servers: ${error.message}`, "error");
  }
  select.innerHTML = servers.length
    ? servers.map(s => `<option value="${escapeHtml(s.id)}">${escapeHtml(s.name)}</option>`).join("")
    : '<option value="">No servers yet</option>';
  if (!mapServerId || !servers.some(s => s.id === mapServerId)) {
    mapServerId = servers[0]?.id || null;
  }
  select.value = mapServerId || "";
  try { await loadRcon(); } catch (error) { logAction(error.message, "error"); }
}

async function loadRcon() {
  if (!mapServerId) return;
  const data = await requestJson(`/api/server/${mapServerId}/rcon`);
  if (!data.settings) return;
  const panel = document.getElementById("map-rcon-setup");
  document.getElementById("rcon-port").value = data.settings.port;
  document.getElementById("rcon-enabled").checked = data.settings.enabled;
  panel.hidden = data.settings.enabled && Boolean(data.settings.password);
}

async function saveRcon() {
  const payload = {
    enabled: document.getElementById("rcon-enabled").checked,
    port: Number(document.getElementById("rcon-port").value) || 25575,
    password: document.getElementById("rcon-password").value.trim()
  };
  const data = await requestJson(`/api/server/${mapServerId}/rcon`, {
    method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(payload)
  });
  if (!data.ok) return showToast(data.error || "Could not save RCON settings", "error");
  showToast(data.restart_required ? "RCON saved. Restart the server to apply." : "RCON settings saved", "success");
  logAction(`RCON ${payload.enabled ? "enabled" : "disabled"} on port ${payload.port}`);
  loadRcon();
}

function setPlacing(active) {
  placingMarker = active;
  document.getElementById("map-canvas")?.classList.toggle("placing", active);
}

function beginPlacement() {
  if (!document.getElementById("structure-label").value.trim()) {
    return showToast("Name the structure first", "warning");
  }
  setPlacing(true);
  showToast("Click the map to drop the marker. Esc cancels.", "success");
}

document.addEventListener("keydown", event => {
  if (event.key === "Escape" && placingMarker) setPlacing(false);
});

async function placeStructure(x, z) {
  setPlacing(false);
  const payload = {
    label: document.getElementById("structure-label").value.trim(),
    owner: document.getElementById("structure-owner").value.trim(),
    kind: document.getElementById("structure-kind").value,
    x, z
  };
  let data;
  try {
    data = await requestJson(`/api/server/${mapServerId}/map/markers`, {
      method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(payload)
    });
  } catch (error) {
    return showToast(error.message, "error");
  }
  if (!data.ok) return showToast(data.error || "Could not save marker", "error");
  document.getElementById("structure-label").value = "";
  renderStructures(data.markers);
  logAction(`Marked "${payload.label}" at ${x}, ${z}`);
}

async function removeStructure(id) {
  const data = await requestJson(`/api/server/${mapServerId}/map/markers`, {
    method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ remove: id })
  });
  if (data.ok) {
    renderStructures(data.markers);
    logAction("Marker removed");
  }
}

function openPlayerDrawer(name) {
  selectedPlayer = name;
  followingPlayer = false;
  document.getElementById("player-drawer").classList.add("open");
  document.getElementById("drawer-name").textContent = name;
  document.getElementById("drawer-follow")?.classList.remove("active");
  const player = lastPlayers.find(p => p.name === name);
  if (player) {
    const dim = normalizeDimension(player.dimension);
    if (dim !== activeDimension) setDimension(dim);
  }
  refreshDrawerStats();
  renderPlayers(lastPlayers);
}

function closePlayerDrawer() {
  selectedPlayer = null;
  followingPlayer = false;
  document.getElementById("player-drawer").classList.remove("open");
  renderPlayers(lastPlayers);
}

function refreshDrawerStats() {
  const player = lastPlayers.find(p => p.name === selectedPlayer);
  const stats = document.getElementById("drawer-stats");
  if (!player) {
    stats.innerHTML = '<div class="row"><span>Status</span><span>Not online</span></div>';
    return;
  }
  stats.innerHTML = `
    <div class="row"><span>Position</span><span>${Math.round(player.x)}, ${Math.round(player.y)}, ${Math.round(player.z)}</span></div>
    <div class="row"><span>Health</span><span>${player.health ?? "?"} / 20</span></div>
    <div class="row"><span>Dimension</span><span>${escapeHtml((player.dimension || "overworld").replace("minecraft:", ""))}</span></div>`;
}

async function moderate(action, extra = {}) {
  if (!selectedPlayer || !mapServerId) return;
  const body = { action, player: selectedPlayer, ...extra };
  try {
    const data = await requestJson(`/api/server/${mapServerId}/player-action`, {
      method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body)
    });
    logAction(`${action} ${selectedPlayer}${data.command ? ` -> ${data.command}` : ""}`, data.ok ? "ok" : "error");
    showToast(data.ok ? `${action} sent` : (data.error || data.message || "Command failed"), data.ok ? "success" : "error");
  } catch (error) {
    logAction(`${action} ${selectedPlayer} failed: ${error.message}`, "error");
  }
}

function moderateBan() {
  const reason = document.getElementById("ban-reason").value.trim();
  moderate("ban", { reason: reason || "Banned by admin" });
}

function moderateGive() {
  moderate("give", {
    item: document.getElementById("give-item").value.trim() || "minecraft:stone",
    count: Number(document.getElementById("give-count").value) || 1
  });
}

function moderateTeleportCoords() {
  moderate("tp_to_coords", {
    x: Number(document.getElementById("tp-x").value) || 0,
    y: Number(document.getElementById("tp-y").value) || 64,
    z: Number(document.getElementById("tp-z").value) || 0
  });
}

function moderateTeleportPlayer() {
  moderate("tp_to_player", { target: document.getElementById("tp-target").value.trim() });
}

window.mcMap = {
  async onShow() {
    await loadMapServers();
    initMap();
    setTimeout(() => mapInstance.invalidateSize(), 60);
    startMapPolling();
  },
  onHide: stopMapPolling
};

document.getElementById("map-server").addEventListener("change", async event => {
  mapServerId = event.target.value || null;
  playerMarkers.forEach(entry => { if (entry.inView) { mapInstance.removeLayer(entry.marker); trailLayer.removeLayer(entry.trail); } });
  playerMarkers.clear();
  structureKey = "";
  closePlayerDrawer();
  setDimension("overworld");
  await loadRcon();
  pollMap();
});

document.getElementById("map-search").addEventListener("input", event => {
  playerFilter = event.target.value.trim().toLowerCase();
  renderOnlineList(lastPlayers);
});

document.getElementById("map-dim-tabs").addEventListener("click", event => {
  const tab = event.target.closest(".dim-tab");
  if (tab) setDimension(tab.dataset.dim);
});

document.getElementById("map-log-toggle").addEventListener("click", () => {
  const feed = document.getElementById("map-log");
  feed.hidden = !feed.hidden;
  document.getElementById("map-log-toggle").textContent = feed.hidden ? "Show" : "Hide";
});
