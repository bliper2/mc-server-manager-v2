// Players tab: roster with multi-select, moderation actions and the prank deck.
// Every action runs on the selected players (or the one named in a list row).

const selectedPlayers = new Set();
let onlinePlayers = [];
let prankCatalog = null;
let prankCategory = "All";
const PANE_KEY = "mc-manager-players-pane";
const RECENT_KEY = "mc-manager-recent-pranks";

function playerTargets() {
  return [...selectedPlayers];
}

function playerInitial(name) {
  return escapeHtml((name.replace(/^[^A-Za-z0-9]+/, "")[0] || "?").toUpperCase());
}

function renderTargetBar() {
  const bar = document.getElementById("target-bar");
  if (!bar) return;
  const names = playerTargets();
  if (!names.length) {
    bar.innerHTML = '<span class="target-empty">Nobody selected. Tick players on the left, or add a name.</span>';
    return;
  }
  bar.innerHTML = `<span class="target-label">${names.length === 1 ? "Target" : `${names.length} targets`}</span>` + names.map(name =>
    `<button type="button" class="target-chip" data-remove="${escapeHtml(name)}" aria-label="Remove ${escapeHtml(name)}">${escapeHtml(name)}<svg viewBox="0 0 12 12" fill="none" stroke="currentColor" stroke-width="1.6" stroke-linecap="round" aria-hidden="true"><path d="M3 3l6 6M9 3l-6 6"/></svg></button>`).join("");
}

function renderActivePlayers(players) {
  onlinePlayers = players;
  const list = document.getElementById("active-player-list");
  const count = document.getElementById("active-player-count");
  if (!list || !count) return;
  count.textContent = `${players.length} online`;
  count.className = `badge ${players.length ? "online" : "offline"}`;
  window.mcScene?.setServerState({ players: players.length });
  list.innerHTML = players.length
    ? players.map(name => `
      <label class="roster-row${selectedPlayers.has(name) ? " selected" : ""}">
        <input type="checkbox" data-player="${escapeHtml(name)}" ${selectedPlayers.has(name) ? "checked" : ""} />
        <span class="roster-avatar" aria-hidden="true">${playerInitial(name)}</span>
        <strong>${escapeHtml(name)}</strong>
        <span class="status-dot online" aria-hidden="true"></span>
      </label>`).join("")
    : '<div class="empty">No players online</div>';
  renderTargetBar();
}

function togglePlayer(name, on) {
  if (on) selectedPlayers.add(name);
  else selectedPlayers.delete(name);
  document.querySelectorAll("#active-player-list .roster-row").forEach(row => {
    const box = row.querySelector("input");
    row.classList.toggle("selected", box.checked);
  });
  renderTargetBar();
}

function selectAllPlayers() {
  onlinePlayers.forEach(name => selectedPlayers.add(name));
  renderActivePlayers(onlinePlayers);
}

function clearPlayerSelection() {
  selectedPlayers.clear();
  renderActivePlayers(onlinePlayers);
}

function addTypedPlayer(event) {
  event.preventDefault();
  const input = document.getElementById("player-name");
  const name = input.value.trim();
  if (!name) return;
  if (!/^[A-Za-z0-9_.\-]{1,32}$/.test(name)) {
    showToast("Names use letters, numbers, underscore, dot and dash", "warning");
    return;
  }
  selectedPlayers.add(name);
  input.value = "";
  renderActivePlayers(onlinePlayers);
}

async function postJson(url, body) {
  const response = await fetch(url, { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body) });
  return response.json();
}

async function playerAction(action, names) {
  const targets = names || playerTargets();
  const needsPlayer = !["whitelist_on", "whitelist_off"].includes(action);
  if (needsPlayer && !targets.length) {
    showToast("Pick at least one player first", "warning");
    return;
  }
  const dangerous = { ban: "Ban", kill: "Kill" }[action];
  if (dangerous && !(await uiConfirm(`${dangerous} ${targets.join(", ")}?`, { title: dangerous, confirmText: dangerous, danger: true }))) return;
  const extra = {
    reason: document.getElementById("player-reason")?.value.trim() || undefined,
    item: document.getElementById("give-item")?.value.trim(),
    count: Number(document.getElementById("give-count")?.value) || 1,
    target: document.getElementById("tp-target")?.value.trim()
  };
  const list = needsPlayer ? targets : [""];
  let failed = 0;
  let last = null;
  for (const player of list) {
    try {
      last = await postJson(`/api/server/${currentServerId}/player-action`, { action, player, ...extra });
      if (!last.ok) failed += 1;
    } catch (error) {
      failed += 1;
      last = { error: error.message };
    }
  }
  if (failed) showToast(last?.error || last?.message || `${failed} of ${list.length} failed`, "error");
  else showToast(list.length > 1 ? `Done for ${list.length} players` : `Executed: ${last.command}`, "success");
  setTimeout(loadPlayers, 1500);
}

// ----- tool panes -----

function showPane(name) {
  document.querySelectorAll(".players-main .seg-btn").forEach(button => {
    const on = button.dataset.pane === name;
    button.classList.toggle("active", on);
    button.setAttribute("aria-selected", on ? "true" : "false");
    button.tabIndex = on ? 0 : -1;
  });
  document.querySelectorAll(".players-main .seg-pane").forEach(pane => pane.classList.toggle("active", pane.dataset.pane === name));
  try { localStorage.setItem(PANE_KEY, name); } catch {}
  if (name === "pranks") loadPranks();
}

// ----- pranks -----

async function loadPranks() {
  if (prankCatalog || !currentServerId) return;
  const grid = document.getElementById("prank-grid");
  try {
    const data = await requestJson(`/api/server/${currentServerId}/pranks`);
    prankCatalog = data.pranks;
    renderPrankCats();
    renderPranks();
    renderRecentPranks();
  } catch (error) {
    grid.innerHTML = `<div class="empty">${escapeHtml(error.message)}</div>`;
  }
}

function renderPrankCats() {
  const cats = ["All", ...new Set(prankCatalog.map(item => item.category))];
  document.getElementById("prank-cats").innerHTML = cats.map(cat =>
    `<button type="button" class="chip${cat === prankCategory ? " active" : ""}" data-cat="${escapeHtml(cat)}" aria-pressed="${cat === prankCategory}">${escapeHtml(cat)}</button>`).join("");
}

function prankFieldHtml(item, param) {
  const id = `prank-${item.id}-${param.key}`;
  const label = `<label for="${id}">${escapeHtml(param.label)}</label>`;
  if (param.type === "select") {
    return `${label}<select id="${id}" data-key="${param.key}">${param.options.map(([value, text]) =>
      `<option value="${escapeHtml(value)}"${value === param.default ? " selected" : ""}>${escapeHtml(text)}</option>`).join("")}</select>`;
  }
  if (param.type === "number") {
    return `${label}<input id="${id}" data-key="${param.key}" type="number" min="${param.min}" max="${param.max}" value="${param.default}" />`;
  }
  return `${label}<input id="${id}" data-key="${param.key}" type="text" maxlength="80" value="${escapeHtml(param.default)}" />`;
}

function renderPranks() {
  const query = document.getElementById("prank-search").value.trim().toLowerCase();
  const shown = prankCatalog.filter(item =>
    (prankCategory === "All" || item.category === prankCategory)
    && (!query || `${item.name} ${item.description} ${item.category}`.toLowerCase().includes(query)));
  document.getElementById("prank-grid").innerHTML = shown.length ? shown.map(item => `
    <article class="prank-card" data-prank="${item.id}">
      <header><h4>${escapeHtml(item.name)}</h4><span class="prank-cat">${escapeHtml(item.category)}</span></header>
      <p>${escapeHtml(item.description)}</p>
      ${item.params.length ? `<div class="prank-fields">${item.params.map(param => prankFieldHtml(item, param)).join("")}</div>` : ""}
      <footer><small>${item.seconds ? `about ${item.seconds}s` : "instant"}</small><button class="btn small ${item.category === "Cleanup" ? "" : "warning"}" type="button" data-run="${item.id}">${item.category === "Cleanup" ? "Run" : "Prank"}</button></footer>
    </article>`).join("") : '<div class="empty">No pranks match</div>';
}

function readPrankParams(card) {
  const params = {};
  card.querySelectorAll("[data-key]").forEach(field => { params[field.dataset.key] = field.value; });
  return params;
}

function rememberPrank(id, params) {
  try {
    const recent = JSON.parse(localStorage.getItem(RECENT_KEY) || "[]").filter(entry => entry.id !== id);
    recent.unshift({ id, params });
    localStorage.setItem(RECENT_KEY, JSON.stringify(recent.slice(0, 6)));
  } catch {}
  renderRecentPranks();
}

function renderRecentPranks() {
  const box = document.getElementById("prank-recent");
  if (!box || !prankCatalog) return;
  let recent = [];
  try { recent = JSON.parse(localStorage.getItem(RECENT_KEY) || "[]"); } catch {}
  recent = recent.filter(entry => prankCatalog.some(item => item.id === entry.id));
  box.hidden = !recent.length;
  box.innerHTML = recent.length ? '<span class="target-label">Recent</span>' + recent.map((entry, index) =>
    `<button type="button" class="chip" data-recent="${index}">${escapeHtml(prankCatalog.find(item => item.id === entry.id).name)}</button>`).join("") : "";
}

async function runPrank(id, params, button) {
  const players = playerTargets();
  if (!players.length) {
    showToast("Pick at least one player first", "warning");
    return;
  }
  if (button) button.disabled = true;
  try {
    const data = await postJson(`/api/server/${currentServerId}/prank`, { prank: id, players, params });
    if (!data.ok) throw new Error(data.error || "Prank failed");
    const item = prankCatalog.find(entry => entry.id === id);
    showToast(`${item.name} sent to ${players.length === 1 ? players[0] : `${players.length} players`}`, "success");
    rememberPrank(id, params);
  } catch (error) {
    showToast(error.message, "error");
  } finally {
    if (button) setTimeout(() => { button.disabled = false; }, 600);
  }
}

// ----- wiring -----

function initPlayersTab() {
  const tab = document.getElementById("dtab-players");
  if (!tab) return;
  tab.addEventListener("change", event => {
    const box = event.target.closest("input[data-player]");
    if (box) togglePlayer(box.dataset.player, box.checked);
  });
  tab.addEventListener("click", event => {
    const remove = event.target.closest("[data-remove]");
    if (remove) { selectedPlayers.delete(remove.dataset.remove); renderActivePlayers(onlinePlayers); return; }
    const pane = event.target.closest(".seg-btn");
    if (pane) { showPane(pane.dataset.pane); return; }
    const cat = event.target.closest("[data-cat]");
    if (cat) { prankCategory = cat.dataset.cat; renderPrankCats(); renderPranks(); return; }
    const run = event.target.closest("[data-run]");
    if (run) { runPrank(run.dataset.run, readPrankParams(run.closest(".prank-card")), run); return; }
    const recent = event.target.closest("[data-recent]");
    if (recent) {
      let entries = [];
      try { entries = JSON.parse(localStorage.getItem(RECENT_KEY) || "[]"); } catch {}
      const entry = entries[Number(recent.dataset.recent)];
      if (entry) runPrank(entry.id, entry.params, recent);
    }
  });
  tab.addEventListener("keydown", event => {
    const seg = event.target.closest(".seg-btn");
    if (!seg || !["ArrowLeft", "ArrowRight"].includes(event.key)) return;
    const buttons = [...tab.querySelectorAll(".seg-btn")];
    const next = buttons[(buttons.indexOf(seg) + (event.key === "ArrowRight" ? 1 : -1) + buttons.length) % buttons.length];
    next.focus();
    showPane(next.dataset.pane);
    event.preventDefault();
  });
  document.getElementById("prank-search").addEventListener("input", () => prankCatalog && renderPranks());
  let pane = "moderate";
  try { pane = localStorage.getItem(PANE_KEY) || pane; } catch {}
  if (!document.querySelector(`.players-main .seg-btn[data-pane="${pane}"]`)) pane = "moderate";
  document.querySelectorAll(".players-main .seg-btn").forEach(button => { button.tabIndex = button.dataset.pane === pane ? 0 : -1; });
  showPane(pane);
  renderTargetBar();
}

initPlayersTab();
