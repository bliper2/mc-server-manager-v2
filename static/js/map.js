// The world map: real terrain drawn from the world files, live players over RCON, last-seen positions, the world spawn,
// places you mark, a ruler and a context menu. Terrain, spawn and last-seen positions need no plugin and no RCON.

let mapServerId = null;
let mapInstance = null;
let mapPollTimer = null;
let worldTimer = null;
let mapFrame = null;
let playerMarkers = new Map();
let structureLayer = null;
let trailLayer = null;
let offlineLayer = null;
let spawnLayer = null;
let rulerLayer = null;
let terrainLayer = null;
let gridLayer = null;
let placingMarker = false;
let movingPlaceId = null;
let selectedPlayer = null;
let playerFilter = "";
let placeFilter = "";
let lastPlayers = [];
let lastMax = null;
let places = [];
let placesKey = "";
let activeDimension = "overworld";
let followingPlayer = false;
let worldInfo = { world: null, dimensions: {}, spawn: null, players: [] };
let terrainRegions = {};
let terrainVersion = 0;
let terrainForce = false;
let terrainPending = 0;
let didAutoFit = null;
let rulerPoints = [];
let rulerOn = false;

const MAP_POLL_MS = 1000;
const WORLD_POLL_MS = 30000;
const LERP = 0.18;
const TRAIL_LENGTH = 14;
const REGION = 512;
const PREFS_KEY = "mc-manager-map";
const REGION_CACHE_LIMIT = 220;

const PLAYER_COLORS = ["#b9f227", "#8ec8ff", "#f4bb52", "#ff766d", "#c084fc", "#34d399", "#f472b6", "#38bdf8"];
const PLACE_KINDS = {
  base: ["⌂", "#f4bb52"], farm: ["☘", "#34d399"], portal: ["◈", "#c084fc"], shop: ["♦", "#ffd166"], spawn: ["★", "#b9f227"], mine: ["⚒", "#9ca3af"],
  village: ["☗", "#fb923c"], stronghold: ["✦", "#f87171"], monument: ["◇", "#38bdf8"], warp: ["➤", "#60a5fa"], death: ["☠", "#e5e7eb"], other: ["■", "#a3a3a3"]
};
const LAYER_DEFS = [
  ["terrain", "Terrain", true], ["grid", "Grid lines", true], ["players", "Online players", true], ["trails", "Player trails", true], ["offline", "Last seen", true],
  ["spawn", "World spawn and beds", true], ["places", "Places", true], ["heads", "Player skins (loads from mc-heads.net)", false]
];
let layerPrefs = loadLayerPrefs();

function loadLayerPrefs() {
  const defaults = Object.fromEntries(LAYER_DEFS.map(([key, , on]) => [key, on]));
  try { return { ...defaults, ...JSON.parse(localStorage.getItem(PREFS_KEY) || "{}") }; } catch { return defaults; }
}

function saveLayerPrefs() {
  try { localStorage.setItem(PREFS_KEY, JSON.stringify(layerPrefs)); } catch { /* private mode: the choice just lasts until reload */ }
}

function toLatLng(x, z) {
  return [-z, x];
}

function playerColor(name) {
  let hash = 0;
  for (let i = 0; i < name.length; i++) hash = (hash * 31 + name.charCodeAt(i)) >>> 0;
  return PLAYER_COLORS[hash % PLAYER_COLORS.length];
}

function normalizeDimension(dim) {
  const name = (dim || "overworld").replace("minecraft:", "");
  return ["overworld", "the_nether", "the_end"].includes(name) ? name : "overworld";
}

function dimensionLabel(dim) {
  return { overworld: "Overworld", the_nether: "Nether", the_end: "End" }[dim] || dim;
}

function formatAgo(iso) {
  if (!iso) return "";
  const seconds = Math.max(0, (Date.now() - new Date(iso).getTime()) / 1000);
  if (seconds < 90) return "just now";
  const minutes = seconds / 60;
  if (minutes < 90) return `${Math.round(minutes)} min ago`;
  const hours = minutes / 60;
  if (hours < 36) return `${Math.round(hours)} h ago`;
  return `${Math.round(hours / 24)} days ago`;
}

function sleep(ms) {
  return new Promise(resolve => setTimeout(resolve, ms));
}

// ---------------------------------------------------------------- terrain
const regionImages = new Map();

function regionUrl(dim, rx, rz, lod, force) {
  return `/api/server/${encodeURIComponent(mapServerId)}/map/tile/${dim}/${rx}/${rz}.png?lod=${lod}${force ? "&force=1" : ""}`;
}

async function fetchRegionBitmap(url) {
  terrainPending++;
  setTerrainNote();
  try {
    for (let attempt = 0; attempt < 150; attempt++) {
      const response = await fetch(url);
      if (response.status === 200) return await createImageBitmap(await response.blob());
      if (response.status !== 202) return null;  // 204: nothing to draw there
      await sleep(1500);  // the server is still drawing this region
    }
  } catch {
    return null;
  } finally {
    terrainPending--;
    setTerrainNote();
  }
  return null;
}

function regionBitmap(dim, rx, rz, lod) {
  const key = `${mapServerId}|${dim}|${rx}|${rz}|${lod}|${terrainVersion}`;
  let entry = regionImages.get(key);
  if (!entry) {
    entry = fetchRegionBitmap(regionUrl(dim, rx, rz, lod, terrainForce));
    regionImages.set(key, entry);
    if (regionImages.size > REGION_CACHE_LIMIT) regionImages.delete(regionImages.keys().next().value);
  }
  return entry;
}

function makeTerrainLayer() {
  const Terrain = L.GridLayer.extend({
    createTile(coords, done) {
      const canvas = document.createElement("canvas");
      canvas.width = canvas.height = 512;
      const context = canvas.getContext("2d");
      context.imageSmoothingEnabled = false;
      const scale = Math.pow(2, coords.z);
      const span = REGION / scale;
      const x0 = coords.x * span;
      const z0 = coords.y * span;
      const dim = activeDimension;
      const known = terrainRegions[dim];
      const lod = coords.z <= -2 ? 4 : 1;
      const jobs = [];
      for (let rx = Math.floor(x0 / REGION); rx <= Math.floor((x0 + span - 1) / REGION); rx++) {
        for (let rz = Math.floor(z0 / REGION); rz <= Math.floor((z0 + span - 1) / REGION); rz++) {
          if (known && !known.has(`${rx},${rz}`)) continue;
          jobs.push(regionBitmap(dim, rx, rz, lod).then(bitmap => {
            if (bitmap) context.drawImage(bitmap, (rx * REGION - x0) * scale, (rz * REGION - z0) * scale, REGION * scale, REGION * scale);
          }));
        }
      }
      Promise.all(jobs).then(() => done(null, canvas));
      return canvas;
    }
  });
  return new Terrain({ tileSize: 512, minZoom: -3, maxZoom: 4, className: "terrain-tile", updateWhenIdle: true, keepBuffer: 2 });
}

function makeGridLayer() {
  const Grid = L.GridLayer.extend({
    createTile(coords) {
      const tile = document.createElement("canvas");
      tile.width = tile.height = 256;
      const context = tile.getContext("2d");
      const scale = Math.pow(2, coords.z);
      const span = 256 / scale;
      const step = [16, 32, 64, 128, 256, 512, 1024, 2048, 4096, 8192].find(blocks => blocks * scale >= 56) || 8192;
      const x0 = coords.x * span;
      const z0 = coords.y * span;
      context.font = "10px monospace";
      context.fillStyle = "rgba(255,255,255,.55)";
      const line = (block, vertical) => {
        const major = block % REGION === 0;
        context.strokeStyle = major ? "rgba(255,255,255,.34)" : "rgba(255,255,255,.12)";
        context.lineWidth = major ? 1.5 : 1;
        const at = Math.round(((vertical ? block - x0 : block - z0) * scale)) + 0.5;
        context.beginPath();
        if (vertical) { context.moveTo(at, 0); context.lineTo(at, 256); } else { context.moveTo(0, at); context.lineTo(256, at); }
        context.stroke();
      };
      for (let block = Math.ceil(x0 / step) * step; block < x0 + span; block += step) line(block, true);
      for (let block = Math.ceil(z0 / step) * step; block < z0 + span; block += step) line(block, false);
      if (step * scale >= 70) {
        for (let bx = Math.ceil(x0 / step) * step; bx < x0 + span; bx += step) {
          for (let bz = Math.ceil(z0 / step) * step; bz < z0 + span; bz += step) {
            context.fillText(`${bx}, ${bz}`, (bx - x0) * scale + 4, (bz - z0) * scale + 12);
          }
        }
      }
      return tile;
    }
  });
  return new Grid({ tileSize: 256, minZoom: -3, maxZoom: 4, className: "grid-tile", updateWhenIdle: true, opacity: 0.9 });
}

function setTerrainNote() {
  const note = document.getElementById("map-note");
  if (!note) return;
  const info = worldInfo.dimensions[activeDimension];
  let text = "";
  if (!worldInfo.world) text = "No world folder found for this server yet. Start the server once to generate it.";
  else if (info && !info.regions) text = `The ${dimensionLabel(activeDimension)} has not been explored yet.`;
  else if (terrainPending > 0 && layerPrefs.terrain) text = `Drawing terrain... ${terrainPending} region${terrainPending === 1 ? "" : "s"} left`;
  note.textContent = text;
  note.hidden = !text;
}

function refreshTerrain() {
  if (!mapServerId || !terrainLayer) return;
  regionImages.clear();
  terrainVersion++;
  terrainForce = true;
  terrainLayer.redraw();
  showToast("Redrawing the terrain from the world files", "success");
  setTimeout(() => {  // the server drew fresh tiles meanwhile: fetch them
    terrainForce = false;
    regionImages.clear();
    terrainVersion++;
    terrainLayer?.redraw();
  }, 6000);
}

async function clearTerrainCache() {
  if (!mapServerId) return;
  try {
    const data = await requestJson(`/api/server/${encodeURIComponent(mapServerId)}/map/cache`, { method: "DELETE" });
    showToast(`Cleared ${data.removed} cached tile${data.removed === 1 ? "" : "s"}`, "success");
    regionImages.clear();
    terrainVersion++;
    terrainLayer?.redraw();
  } catch (error) {
    showToast(error.message, "error");
  }
}

// ---------------------------------------------------------------- layers panel
function applyLayerPrefs() {
  if (!mapInstance) return;
  const toggle = (layer, on) => { if (layer && on !== mapInstance.hasLayer(layer)) (on ? mapInstance.addLayer(layer) : mapInstance.removeLayer(layer)); };
  toggle(terrainLayer, layerPrefs.terrain);
  toggle(gridLayer, layerPrefs.grid);
  toggle(structureLayer, layerPrefs.places);
  toggle(offlineLayer, layerPrefs.offline);
  toggle(spawnLayer, layerPrefs.spawn);
  toggle(trailLayer, layerPrefs.trails);
  mapInstance.getContainer().classList.toggle("no-terrain", !layerPrefs.terrain);
  renderPlayers(lastPlayers);
  setTerrainNote();
}

function renderLayerPanel() {
  const panel = document.getElementById("map-layers");
  panel.innerHTML = LAYER_DEFS.map(([key, label]) => `<label class="layer-option"><input type="checkbox" data-layer="${key}" ${layerPrefs[key] ? "checked" : ""} /><span>${escapeHtml(label)}</span></label>`).join("")
    + '<div class="layer-actions"><button class="btn small ghost" data-act="clear" data-perm="files">Clear terrain cache</button></div>';
}

function toggleLayerPanel() {
  const panel = document.getElementById("map-layers");
  panel.hidden = !panel.hidden;
  document.getElementById("map-layers-btn").setAttribute("aria-expanded", String(!panel.hidden));
  if (!panel.hidden) renderLayerPanel();
}

// ---------------------------------------------------------------- the map itself
function initMap() {
  if (mapInstance) return mapInstance;
  mapInstance = L.map("map-canvas", { crs: L.CRS.Simple, minZoom: -3, maxZoom: 4, zoomControl: true, attributionControl: false, preferCanvas: true, zoomSnap: 0.5, zoomDelta: 1 });
  mapInstance.setView(toLatLng(0, 0), 0);
  terrainLayer = makeTerrainLayer();
  gridLayer = makeGridLayer();
  structureLayer = L.layerGroup();
  trailLayer = L.layerGroup();
  offlineLayer = L.layerGroup();
  spawnLayer = L.layerGroup();
  rulerLayer = L.layerGroup().addTo(mapInstance);
  mapInstance.addLayer(terrainLayer);
  addScaleBar();
  applyLayerPrefs();

  mapInstance.on("click", event => {
    if (movingPlaceId) return movePlaceTo(event.latlng);
    if (placingMarker) return placeStructure(Math.round(event.latlng.lng), Math.round(-event.latlng.lat));
    if (rulerOn) return addRulerPoint(event.latlng);
    hideContextMenu();
  });
  mapInstance.on("contextmenu", event => showContextMenu(event));
  mapInstance.on("movestart zoomstart", hideContextMenu);
  mapInstance.on("mousemove", event => updateCoords(event.latlng));
  return mapInstance;
}

function addScaleBar() {
  const Scale = L.Control.extend({
    options: { position: "bottomleft" },
    onAdd(map) {
      this.el = L.DomUtil.create("div", "map-scale");
      this.bar = L.DomUtil.create("span", "map-scale-bar", this.el);
      this.text = L.DomUtil.create("span", "map-scale-text", this.el);
      map.on("zoomend", this.update, this);
      this.update();
      return this.el;
    },
    update() {
      const pxPerBlock = Math.pow(2, mapInstance.getZoom());
      const nice = [1, 2, 5, 10, 20, 50, 100, 200, 500, 1000, 2000, 5000, 10000, 20000].find(blocks => blocks * pxPerBlock >= 70) || 20000;
      this.bar.style.width = `${Math.round(nice * pxPerBlock)}px`;
      this.text.textContent = `${nice.toLocaleString()} blocks`;
    }
  });
  new Scale().addTo(mapInstance);
}

function updateCoords(latlng) {
  const readout = document.getElementById("map-coords");
  if (!readout) return;
  const x = Math.round(latlng.lng);
  const z = Math.round(-latlng.lat);
  const other = activeDimension === "the_nether" ? `Overworld ${x * 8}, ${z * 8}` : activeDimension === "overworld" ? `Nether ${Math.round(x / 8)}, ${Math.round(z / 8)}` : "";
  readout.textContent = `X ${x}  Z ${z}  ·  chunk ${x >> 4}, ${z >> 4}  ·  r.${Math.floor(x / REGION)}.${Math.floor(z / REGION)}${other ? `  ·  ${other}` : ""}`;
}

// ---------------------------------------------------------------- players
function playerIconHtml(player) {
  const max = 20;
  const ratio = Math.max(0, Math.min(1, (player.health ?? max) / max));
  const state = ratio > 0.6 ? "ok" : ratio > 0.3 ? "hurt" : "critical";
  const color = playerColor(player.name);
  const facing = typeof player.yaw === "number" ? (player.yaw + 180) % 360 : null;
  const face = layerPrefs.heads
    ? `<img class="player-pin-head" src="https://mc-heads.net/avatar/${encodeURIComponent(player.name)}/32" alt="" width="18" height="18" onerror="this.replaceWith(Object.assign(document.createElement('span'),{className:'player-pin-dot'}))" />`
    : '<span class="player-pin-dot"></span>';
  return `<div class="player-pin ${selectedPlayer === player.name ? "selected" : ""}" style="--pc:${color}">
        ${facing !== null ? `<span class="player-pin-facing" style="transform:rotate(${Math.round(facing)}deg)"></span>` : ""}
        ${face}
        <span class="player-pin-name">${escapeHtml(player.name)}</span>
        <span class="player-pin-health ${state}"><i style="width:${Math.round(ratio * 100)}%"></i></span>
      </div>`;
}

function playerIcon(html) {
  return L.divIcon({ className: "player-pin-wrap", html, iconSize: null });
}

function removePlayerEntry(entry) {
  if (entry.inView) {
    mapInstance.removeLayer(entry.marker);
    trailLayer.removeLayer(entry.trail);
  }
  entry.inView = false;
}

function renderPlayers(players) {
  if (!mapInstance) return;
  const seen = new Set();
  players.forEach(player => {
    seen.add(player.name);
    const dim = normalizeDimension(player.dimension);
    const target = toLatLng(player.x, player.z);
    let entry = playerMarkers.get(player.name);
    const inView = layerPrefs.players && dim === activeDimension;
    if (!entry) {
      const iconHtml = playerIconHtml(player);
      const marker = L.marker(target, { icon: playerIcon(iconHtml), keyboard: true, zIndexOffset: 1000 });
      marker.on("click", () => openPlayerDrawer(player.name));
      const trail = L.polyline([], { color: playerColor(player.name), weight: 2, opacity: 0.4, dashArray: "1 6" });
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
    if (!inView && entry.inView) removePlayerEntry(entry);
    entry.inView = inView;
    if (inView) {
      entry.points.push(target);
      if (entry.points.length > TRAIL_LENGTH) entry.points.shift();
      entry.trail.setLatLngs(entry.points);
    }
  });
  [...playerMarkers.keys()].forEach(name => {
    if (seen.has(name)) return;
    removePlayerEntry(playerMarkers.get(name));
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

function renderOnlineList(players) {
  const list = document.getElementById("map-player-list");
  const count = document.getElementById("map-player-count");
  if (!list) return;
  const filtered = players.filter(p => p.name.toLowerCase().includes(playerFilter));
  count.textContent = `${players.length}${lastMax ? ` / ${lastMax}` : ""} online`;
  count.className = `badge ${players.length ? "online" : "offline"}`;
  list.innerHTML = filtered.length
    ? filtered.map(p => {
        const dim = normalizeDimension(p.dimension);
        const ratio = Math.max(0, Math.min(1, (p.health ?? 20) / 20));
        return `<button class="active-player" onclick="openPlayerDrawer(${escapeHtml(JSON.stringify(String(p.name)))})">
        <span class="status-dot online" style="background:${playerColor(p.name)};box-shadow:0 0 8px ${playerColor(p.name)}"></span><strong>${escapeHtml(p.name)}</strong>
        <span>${Math.round(p.x)}, ${Math.round(p.z)}${dim !== "overworld" ? ` · ${dim.replace("the_", "")}` : ""}</span>
        <i class="mini-health" style="--w:${Math.round(ratio * 100)}%"></i></button>`;
      }).join("")
    : `<div class="empty">${players.length ? "No match" : "Nobody online"}</div>`;
}

function centerMap() {
  const inView = [...playerMarkers.values()].filter(e => e.inView);
  if (!inView.length || !mapInstance) return showToast("Nobody is online in this dimension", "warning");
  const bounds = L.latLngBounds(inView.map(e => e.target));
  mapInstance.flyToBounds(bounds.pad(0.35), { animate: true, duration: 0.5, maxZoom: 1 });
}

function fitExplored() {
  const info = worldInfo.dimensions[activeDimension];
  if (!info?.bounds || !mapInstance) return showToast(`The ${dimensionLabel(activeDimension)} has not been explored yet`, "warning");
  const [minX, minZ, maxX, maxZ] = info.bounds;
  mapInstance.flyToBounds([toLatLng(minX * REGION, (maxZ + 1) * REGION), toLatLng((maxX + 1) * REGION, minZ * REGION)], { animate: true, duration: 0.5 });
}

function flyToBlock(x, z, dim, zoom = 1) {
  if (dim && dim !== activeDimension) setDimension(dim);
  mapInstance?.flyTo(toLatLng(x, z), zoom, { animate: true, duration: 0.5 });
}

function setDimension(dim) {
  activeDimension = dim;
  document.querySelectorAll(".dim-tab").forEach(tab => tab.classList.toggle("active", tab.dataset.dim === dim));
  rebuildTerrainIndex();
  terrainLayer?.redraw();
  gridLayer?.redraw();
  renderPlayers(lastPlayers);
  renderPlaces(true);
  renderWorldMarkers();
  setTerrainNote();
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

// ---------------------------------------------------------------- spawn, beds and players last seen
function pinIcon(className, html) {
  return L.divIcon({ className: "world-pin-wrap", html: `<div class="${className}">${html}</div>`, iconSize: null });
}

function renderWorldMarkers() {
  if (!mapInstance) return;
  offlineLayer.clearLayers();
  spawnLayer.clearLayers();
  const online = new Set(lastPlayers.map(p => p.name));
  const spawn = worldInfo.spawn;
  if (spawn && spawn.dim === activeDimension) {
    L.marker(toLatLng(spawn.x, spawn.z), { icon: pinIcon("spawn-pin", "★ World spawn"), keyboard: false })
      .bindPopup(`<strong>World spawn</strong><br>${Math.round(spawn.x)}, ${Math.round(spawn.y)}, ${Math.round(spawn.z)}<br><button class="mini-btn" onclick="copyText('${Math.round(spawn.x)} ${Math.round(spawn.y)} ${Math.round(spawn.z)}')">Copy coordinates</button>`)
      .addTo(spawnLayer);
  }
  (worldInfo.players || []).forEach(player => {
    if (player.bed && normalizeDimension(player.bed.dim) === activeDimension) {
      L.marker(toLatLng(player.bed.x, player.bed.z), { icon: pinIcon("bed-pin", `⌂<small>${escapeHtml(player.name)}</small>`), keyboard: false })
        .bindPopup(`<strong>${escapeHtml(player.name)}'s spawn point</strong><br>${Math.round(player.bed.x)}, ${Math.round(player.bed.y)}, ${Math.round(player.bed.z)}`).addTo(spawnLayer);
    }
    if (online.has(player.name) || normalizeDimension(player.dim) !== activeDimension) return;
    L.marker(toLatLng(player.x, player.z), { icon: pinIcon("offline-pin", `<span>${escapeHtml(player.name.slice(0, 1).toUpperCase())}</span><small>${escapeHtml(player.name)}</small>`), keyboard: false })
      .bindPopup(`<strong>${escapeHtml(player.name)}</strong> · last seen ${escapeHtml(formatAgo(player.seen))}<br>${Math.round(player.x)}, ${Math.round(player.y)}, ${Math.round(player.z)} · ${escapeHtml(dimensionLabel(normalizeDimension(player.dim)))}`)
      .addTo(offlineLayer);
  });
  renderLastSeenList();
}

function renderLastSeenList() {
  const online = new Set(lastPlayers.map(p => p.name));
  const list = document.getElementById("map-lastseen-list");
  const away = (worldInfo.players || []).filter(p => !online.has(p.name));
  document.getElementById("map-lastseen-count").textContent = String(away.length);
  list.innerHTML = away.length
    ? away.map(p => `<button class="active-player" onclick="flyToBlock(${Math.round(p.x)}, ${Math.round(p.z)}, ${escapeHtml(JSON.stringify(normalizeDimension(p.dim)))})">
        <span class="status-dot offline"></span><strong>${escapeHtml(p.name)}</strong><span>${escapeHtml(formatAgo(p.seen))} · ${Math.round(p.x)}, ${Math.round(p.z)}</span></button>`).join("")
    : '<div class="empty">No player files yet</div>';
}

function rebuildTerrainIndex() {
  terrainRegions = {};
  Object.entries(worldInfo.dimensions || {}).forEach(([dim, info]) => {
    terrainRegions[dim] = new Set((info.list || []).map(([rx, rz]) => `${rx},${rz}`));
  });
}

async function loadWorld(first = false) {
  if (!mapServerId) return;
  try {
    const data = await requestJson(`/api/server/${encodeURIComponent(mapServerId)}/map/world`);
    const before = JSON.stringify(Object.fromEntries(Object.entries(worldInfo.dimensions || {}).map(([d, i]) => [d, i.regions])));
    worldInfo = data;
    rebuildTerrainIndex();
    renderWorldMarkers();
    const tabs = document.querySelectorAll(".dim-tab");
    tabs.forEach(tab => { const info = worldInfo.dimensions?.[tab.dataset.dim]; tab.title = info ? `${info.regions} region${info.regions === 1 ? "" : "s"} explored` : ""; });
    setTerrainNote();
    if (didAutoFit !== mapServerId) {
      didAutoFit = mapServerId;
      const info = worldInfo.dimensions?.overworld;
      if (!lastPlayers.length && info?.bounds) fitExplored();
      else if (!lastPlayers.length && worldInfo.spawn) mapInstance.setView(toLatLng(worldInfo.spawn.x, worldInfo.spawn.z), 0);
    }
    if (!first && before !== JSON.stringify(Object.fromEntries(Object.entries(worldInfo.dimensions || {}).map(([d, i]) => [d, i.regions])))) terrainLayer?.redraw();
  } catch (error) {
    logAction(`Could not read the world: ${error.message}`, "error");
  }
}

// ---------------------------------------------------------------- places (the markers you place)
function placeMarkerHtml(place) {
  const [glyph, color] = PLACE_KINDS[place.kind] || PLACE_KINDS.other;
  return `<div class="place-pin" style="--pc:${color}"><span class="place-glyph">${glyph}</span><span class="place-name">${escapeHtml(place.label)}</span></div>`;
}

function placePopup(place) {
  const id = escapeHtml(JSON.stringify(String(place.id)));
  return `<strong>${escapeHtml(place.label)}</strong><br><small>${escapeHtml(place.kind)}${place.owner ? ` · ${escapeHtml(place.owner)}` : ""} · ${Math.round(place.x)}, ${Math.round(place.z)}</small>
    <div class="popup-actions"><button class="mini-btn" onclick="copyText('${Math.round(place.x)} ~ ${Math.round(place.z)}')">Copy</button><button class="mini-btn" onclick="renamePlace(${id})">Rename</button><button class="mini-btn" onclick="beginMovePlace(${id})">Move</button><button class="mini-btn danger" onclick="removeStructure(${id})">Remove</button></div>`;
}

function renderPlaces(force = false) {
  const key = JSON.stringify(places) + activeDimension;
  if (!force && key === placesKey) return;
  placesKey = key;
  if (structureLayer) {
    structureLayer.clearLayers();
    places.filter(p => (p.dim || "overworld") === activeDimension).forEach(place => {
      L.marker(toLatLng(place.x, place.z), { icon: L.divIcon({ className: "place-pin-wrap", html: placeMarkerHtml(place), iconSize: null }), keyboard: false })
        .bindPopup(placePopup(place)).addTo(structureLayer);
    });
  }
  renderPlacesList();
}

function renderPlacesList() {
  const list = document.getElementById("map-places-list");
  document.getElementById("map-places-count").textContent = String(places.length);
  const shown = places.filter(p => `${p.label} ${p.owner || ""} ${p.kind}`.toLowerCase().includes(placeFilter));
  list.innerHTML = shown.length
    ? shown.map(p => {
        const [glyph, color] = PLACE_KINDS[p.kind] || PLACE_KINDS.other;
        const id = escapeHtml(JSON.stringify(String(p.id)));
        return `<div class="place-row"><button class="active-player" onclick="goToPlace(${id})"><span class="place-glyph" style="color:${color}">${glyph}</span><strong>${escapeHtml(p.label)}</strong>
          <span>${Math.round(p.x)}, ${Math.round(p.z)}${(p.dim || "overworld") !== "overworld" ? ` · ${(p.dim || "").replace("the_", "")}` : ""}</span></button>
          <button class="mini-btn" title="Rename" aria-label="Rename ${escapeHtml(p.label)}" onclick="renamePlace(${id})">✎</button><button class="mini-btn danger" title="Remove" aria-label="Remove ${escapeHtml(p.label)}" onclick="removeStructure(${id})">✕</button></div>`;
      }).join("")
    : `<div class="empty">${places.length ? "No match" : "Nothing marked yet. Right-click the map to add a place."}</div>`;
}

function goToPlace(id) {
  const place = places.find(p => p.id === id);
  if (place) flyToBlock(place.x, place.z, place.dim || "overworld", 2);
}

// The map's click mode decides the cursor and the hint shown over it: place a new marker, move one, or measure.
function setMapMode() {
  const mode = placingMarker ? "place" : movingPlaceId ? "move" : rulerOn ? "ruler" : "";
  const canvas = document.getElementById("map-canvas");
  if (mode) canvas.dataset.mode = mode; else delete canvas.dataset.mode;
}

function setPlacing(active) {
  placingMarker = active;
  setMapMode();
}

function beginPlacement() {
  if (!document.getElementById("structure-label").value.trim()) return showToast("Name the place first", "warning");
  setPlacing(true);
  showToast("Click the map to drop the marker. Esc cancels.", "success");
}

async function saveMarkers(payload, message) {
  try {
    const data = await requestJson(`/api/server/${encodeURIComponent(mapServerId)}/map/markers`, { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(payload) });
    places = data.markers || [];
    renderPlaces(true);
    if (message) logAction(message);
    return data;
  } catch (error) {
    showToast(error.message, "error");
    return null;
  }
}

async function placeStructure(x, z, label = null) {
  setPlacing(false);
  const name = label ?? document.getElementById("structure-label").value.trim();
  const data = await saveMarkers({
    label: name, owner: document.getElementById("structure-owner").value.trim(), kind: document.getElementById("structure-kind").value, dim: activeDimension, x, z
  }, `Marked "${name}" at ${x}, ${z}`);
  if (data && label === null) document.getElementById("structure-label").value = "";
}

async function removeStructure(id) {
  mapInstance?.closePopup();
  await saveMarkers({ remove: id }, "Place removed");
}

async function renamePlace(id) {
  const place = places.find(p => p.id === id);
  mapInstance?.closePopup();
  if (!place) return;
  const name = await uiAsk({ title: "Rename place", confirmText: "Rename", input: { value: place.label, placeholder: "Place name" } });
  if (name && name.trim()) await saveMarkers({ edit: id, label: name.trim() }, `Renamed to "${name.trim()}"`);
}

function beginMovePlace(id) {
  mapInstance?.closePopup();
  movingPlaceId = id;
  setPlacing(false);
  setMapMode();
  showToast("Click where this place should go. Esc cancels.", "success");
}

async function movePlaceTo(latlng) {
  const id = movingPlaceId;
  movingPlaceId = null;
  setMapMode();
  await saveMarkers({ edit: id, x: Math.round(latlng.lng), z: Math.round(-latlng.lat), dim: activeDimension }, "Place moved");
}

// ---------------------------------------------------------------- ruler and context menu
function toggleRuler(force) {
  rulerOn = force ?? !rulerOn;
  document.getElementById("map-ruler-btn")?.classList.toggle("active", rulerOn);
  setMapMode();
  if (!rulerOn) return;
  clearRuler();
  showToast("Click points on the map to measure. Esc or the Ruler button stops.", "success");
}

function clearRuler() {
  rulerPoints = [];
  rulerLayer?.clearLayers();
}

function addRulerPoint(latlng) {
  rulerPoints.push(latlng);
  rulerLayer.clearLayers();
  L.polyline(rulerPoints, { color: "#ffffff", weight: 2, dashArray: "6 4" }).addTo(rulerLayer);
  let total = 0;
  rulerPoints.forEach((point, index) => {
    L.circleMarker(point, { radius: 4, color: "#111", weight: 1, fillColor: "#ffd166", fillOpacity: 1 }).addTo(rulerLayer);
    if (index === 0) return;
    const previous = rulerPoints[index - 1];
    const segment = Math.hypot(point.lng - previous.lng, point.lat - previous.lat);
    total += segment;
    L.tooltip({ permanent: true, direction: "top", className: "ruler-tip", offset: [0, -4] })
      .setContent(`${Math.round(segment)} blocks${index > 1 ? ` · total ${Math.round(total)}` : ""}`).setLatLng(L.latLng((point.lat + previous.lat) / 2, (point.lng + previous.lng) / 2)).addTo(rulerLayer);
  });
  if (rulerPoints.length > 1) {
    const nether = activeDimension === "overworld" ? ` (${Math.round(total / 8)} in the Nether)` : activeDimension === "the_nether" ? ` (${Math.round(total * 8)} in the Overworld)` : "";
    logAction(`Measured ${Math.round(total)} blocks${nether}`);
  }
}

function hideContextMenu() {
  const menu = document.getElementById("map-context");
  if (menu) menu.hidden = true;
}

function showContextMenu(event) {
  if (placingMarker || movingPlaceId) return;
  const x = Math.round(event.latlng.lng);
  const z = Math.round(-event.latlng.lat);
  const menu = document.getElementById("map-context");
  const items = [
    [`Copy coordinates  ${x}, ${z}`, () => copyText(`${x} ${z}`)],
    ["Copy /tp command", () => copyText(`/tp @s ${x} ~ ${z}`)],
    ["Add a place here...", async () => {
      const name = await uiAsk({ title: "New place", confirmText: "Add", input: { value: "", placeholder: "Name, for example Main base" } });
      if (name && name.trim()) placeStructure(x, z, name.trim());
    }],
    ["Measure from here", () => { toggleRuler(true); addRulerPoint(event.latlng); }],
    ["Center the map here", () => mapInstance.panTo(event.latlng)]
  ];
  if (activeDimension === "overworld") items.push([`Nether portal spot  ${Math.round(x / 8)}, ${Math.round(z / 8)}`, () => copyText(`${Math.round(x / 8)} ${Math.round(z / 8)}`)]);
  if (activeDimension === "the_nether") items.push([`Overworld spot  ${x * 8}, ${z * 8}`, () => copyText(`${x * 8} ${z * 8}`)]);
  menu.innerHTML = "";
  items.forEach(([label, action]) => {
    const button = document.createElement("button");
    button.type = "button";
    button.setAttribute("role", "menuitem");
    button.textContent = label;
    button.addEventListener("click", () => { hideContextMenu(); action(); });
    menu.appendChild(button);
  });
  const stage = menu.parentElement.getBoundingClientRect();
  const canvas = document.getElementById("map-canvas").getBoundingClientRect();
  menu.style.left = `${Math.min(event.containerPoint.x + (canvas.left - stage.left), stage.width - 250)}px`;
  menu.style.top = `${Math.min(event.containerPoint.y + (canvas.top - stage.top), stage.height - 40 - items.length * 34)}px`;
  menu.hidden = false;
}

document.addEventListener("keydown", event => {
  if (event.key !== "Escape") return;
  if (placingMarker) setPlacing(false);
  if (movingPlaceId) { movingPlaceId = null; setMapMode(); }
  if (rulerOn) { toggleRuler(false); clearRuler(); }
  hideContextMenu();
});

// ---------------------------------------------------------------- polling and the server picker
async function pollMap() {
  if (!mapServerId || document.hidden) return;
  try {
    const data = await requestJson(`/api/server/${encodeURIComponent(mapServerId)}/map`);
    const status = document.getElementById("map-status");
    if (data.error) {
      status.textContent = data.running ? data.error : "Offline";
      status.className = "badge offline";
    } else {
      status.textContent = data.running ? "Live" : "Offline";
      status.className = `badge ${data.running ? "online" : "offline"}`;
    }
    lastPlayers = data.players || [];
    lastMax = data.max || null;
    renderPlayers(lastPlayers);
    places = data.markers || [];
    renderPlaces();
    renderOnlineList(lastPlayers);
    if (lastPlayers.length) renderWorldMarkers();
    if (selectedPlayer) refreshDrawerStats();
  } catch (error) {
    logAction(error.message, "error");
  }
}

function startMapPolling() {
  stopMapPolling();
  pollMap();
  mapPollTimer = setInterval(pollMap, MAP_POLL_MS);
  worldTimer = setInterval(loadWorld, WORLD_POLL_MS);
  if (mapFrame === null) animate();
}

function stopMapPolling() {
  if (mapPollTimer) clearInterval(mapPollTimer);
  if (worldTimer) clearInterval(worldTimer);
  mapPollTimer = worldTimer = null;
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
  panel.hidden = data.settings.enabled && Boolean(data.settings.has_password);
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

// ---------------------------------------------------------------- the player drawer
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
    mapInstance?.panTo(toLatLng(player.x, player.z));
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
  const spawn = worldInfo.spawn;
  const away = spawn && spawn.dim === normalizeDimension(player.dimension) ? `${Math.round(Math.hypot(player.x - spawn.x, player.z - spawn.z))} blocks` : "";
  stats.innerHTML = `
    <div class="row"><span>Position</span><span>${Math.round(player.x)}, ${Math.round(player.y)}, ${Math.round(player.z)}</span></div>
    <div class="row"><span>Health</span><span>${player.health ?? "?"} / 20</span></div>
    <div class="row"><span>Dimension</span><span>${escapeHtml(dimensionLabel(normalizeDimension(player.dimension)))}</span></div>
    ${away ? `<div class="row"><span>From world spawn</span><span>${away}</span></div>` : ""}
    <div class="row"><span>Chunk</span><span>${Math.floor(player.x) >> 4}, ${Math.floor(player.z) >> 4}</span></div>`;
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

// ---------------------------------------------------------------- wiring
window.mcMap = {
  async onShow() {
    await loadMapServers();
    initMap();
    setTimeout(() => mapInstance.invalidateSize(), 60);
    renderLayerPanel();
    await loadWorld(true);
    startMapPolling();
  },
  onHide: stopMapPolling
};

document.getElementById("map-server").addEventListener("change", async event => {
  mapServerId = event.target.value || null;
  playerMarkers.forEach(entry => removePlayerEntry(entry));
  playerMarkers.clear();
  regionImages.clear();
  terrainVersion++;
  placesKey = "";
  places = [];
  lastPlayers = [];
  worldInfo = { world: null, dimensions: {}, spawn: null, players: [] };
  closePlayerDrawer();
  setDimension("overworld");
  await loadRcon();
  await loadWorld(true);
  pollMap();
});

document.getElementById("map-search").addEventListener("input", event => {
  playerFilter = event.target.value.trim().toLowerCase();
  renderOnlineList(lastPlayers);
});

document.getElementById("map-places-search").addEventListener("input", event => {
  placeFilter = event.target.value.trim().toLowerCase();
  renderPlacesList();
});

document.getElementById("map-dim-tabs").addEventListener("click", event => {
  const tab = event.target.closest(".dim-tab");
  if (tab) setDimension(tab.dataset.dim);
});

document.getElementById("map-layers").addEventListener("click", event => {
  const box = event.target.closest("[data-layer]");
  if (box) {
    layerPrefs[box.dataset.layer] = box.checked;
    saveLayerPrefs();
    applyLayerPrefs();
    if (box.dataset.layer === "heads") { playerMarkers.forEach(entry => { entry.iconHtml = ""; }); renderPlayers(lastPlayers); }
  }
  if (event.target.closest('[data-act="clear"]')) clearTerrainCache();
});

document.getElementById("map-log-toggle").addEventListener("click", () => {
  const feed = document.getElementById("map-log");
  feed.hidden = !feed.hidden;
  document.getElementById("map-log-toggle").textContent = feed.hidden ? "Show" : "Hide";
});
