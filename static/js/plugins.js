// Installed plugins and mods, Modrinth browser and plugin update checker.

const pluginView = { plugins: [], mods: [], query: "", filter: "all", sort: "name" };

function pluginState(file) {
  if (!file.enabled) return "disabled";
  return file.problem ? "problem" : "enabled";
}

function pluginRows(list, folder) {
  if (!list.length) return '<div class="empty" style="padding:0.4rem">None found</div>';
  return list.map(file => {
    const info = file.info || {};
    const title = info.name || file.name.replace(/\.disabled$/, "");
    const update = lastUpdateItems.findIndex(item => item.file === file.name && item.update_available);
    const meta = [info.version ? `v${escapeHtml(info.version)}` : "", info.authors?.length ? `by ${escapeHtml(info.authors.join(", "))}` : "", formatBytes(file.size)].filter(Boolean).join(" · ");
    return `<div class="item plugin-row${file.enabled ? "" : " is-off"}${file.problem ? " has-problem" : ""}">
      <span class="plugin-name" title="${escapeHtml(file.name)}"><strong>${escapeHtml(title)}</strong>${file.enabled ? "" : ' <em>disabled</em>'}
        ${update >= 0 ? `<button class="chip-update" data-perm="files" onclick="applyUpdate(${update})" title="Install the newest build">↑ ${escapeHtml(lastUpdateItems[update].latest_version || "update")}</button>` : ""}
        <small class="plugin-meta">${meta}</small>
        ${info.description ? `<small class="plugin-desc">${escapeHtml(info.description)}</small>` : ""}
        ${file.problem ? `<small class="plugin-problem">⚠ ${escapeHtml(file.problem)}</small>` : ""}</span>
      ${file.has_config ? `<button class="btn small" onclick="showPluginConfigs(${jsArg(info.name)})" title="Open this plugin's configuration files">Config</button>` : ""}
      ${/^https?:\/\//.test(info.website || "") ? `<a class="btn small" href="${escapeHtml(info.website)}" target="_blank" rel="noopener noreferrer" title="Website">↗</a>` : ""}
      <label class="setting-switch" data-perm="files" title="${file.enabled ? "Disable" : "Enable"} (applies after a restart)"><input type="checkbox" ${file.enabled ? "checked" : ""} onchange="togglePlugin(${jsArg(folder)}, ${jsArg(file.name)}, this.checked)" aria-label="Enabled" /><span></span></label>
      <button class="btn danger small" data-perm="files" onclick="deletePlugin(${jsArg(folder)}, ${jsArg(file.name)})" aria-label="Delete ${escapeHtml(file.name)}">✕</button>
    </div>`;
  }).join("");
}

// Above a list: how many files the server cannot load, with a one-click way to switch them off.
function pluginNotice(list, folder) {
  const broken = list.filter(file => file.problem && file.enabled);
  if (!broken.length) return "";
  return `<div class="notice warn plugin-problems"><span><strong>${broken.length} file${broken.length === 1 ? "" : "s"} here cannot be loaded by this server.</strong> Each one makes the server log an error at start-up. Disable ${broken.length === 1 ? "it" : "them"} (you can turn ${broken.length === 1 ? "it" : "them"} back on later) or delete ${broken.length === 1 ? "it" : "them"}.</span><button class="btn small" data-perm="files" onclick="disableBrokenPlugins(${jsArg(folder)})">Disable ${broken.length === 1 ? "it" : "them all"}</button></div>`;
}

const PLUGIN_STATE_ORDER = ["problem", "disabled", "enabled"];

function visiblePlugins(list) {
  const query = pluginView.query.toLowerCase();
  const shown = list.filter(file => {
    if (pluginView.filter !== "all" && pluginState(file) !== pluginView.filter) return false;
    if (!query) return true;
    const info = file.info || {};
    return [file.name, info.name, info.description, (info.authors || []).join(" ")].some(text => (text || "").toLowerCase().includes(query));
  });
  const name = file => ((file.info || {}).name || file.name).toLowerCase();
  const order = {
    name: (a, b) => name(a).localeCompare(name(b)),
    size: (a, b) => b.size - a.size,
    newest: (a, b) => b.modified.localeCompare(a.modified),
    status: (a, b) => PLUGIN_STATE_ORDER.indexOf(pluginState(a)) - PLUGIN_STATE_ORDER.indexOf(pluginState(b)) || name(a).localeCompare(name(b))
  };
  return shown.sort(order[pluginView.sort] || order.name);
}

function renderPluginLists() {
  for (const folder of ["plugins", "mods"]) {
    const all = pluginView[folder];
    const shown = visiblePlugins(all);
    document.getElementById(`${folder}-notice`).innerHTML = pluginNotice(all, folder);
    document.getElementById(`${folder}-list`).innerHTML = all.length && !shown.length ? '<div class="empty" style="padding:0.4rem">Nothing matches this search</div>' : pluginRows(shown, folder);
    const off = all.filter(file => !file.enabled).length, bad = all.filter(file => file.problem && file.enabled).length;
    document.getElementById(`${folder}-count`).textContent = all.length ? `${all.length} file${all.length === 1 ? "" : "s"}${off ? ` · ${off} disabled` : ""}${bad ? ` · ${bad} with problems` : ""}` : "";
  }
}

function setPluginView(key, value) {
  pluginView[key] = value;
  renderPluginLists();
}

async function loadPluginMods() {
  if (!currentServerId) return;
  const [plugins, mods] = await Promise.all([
    (await fetch(`/api/server/${currentServerId}/files?folder=plugins`)).json(),
    (await fetch(`/api/server/${currentServerId}/files?folder=mods`)).json()
  ]);
  pluginView.plugins = plugins;
  pluginView.mods = mods;
  renderPluginLists();
  wirePluginDrop();
}

async function uploadPluginFiles(files, folder) {
  const jars = [...files];
  if (!jars.length) return;
  const form = new FormData();
  form.append("folder", folder);
  jars.forEach(file => form.append("files", file));
  try {
    const data = await (await fetch(`/api/server/${currentServerId}/plugins/upload`, { method: "POST", body: form })).json();
    (data.skipped || []).forEach(item => showToast(`${item.name}: ${item.reason}`, "error"));
    if (data.added?.length) showToast(`Added ${data.added.join(", ")}. Restart the server to load ${data.added.length === 1 ? "it" : "them"}.`, "success");
    else if (!(data.skipped || []).length) showToast(data.error || "Nothing was added", "error");
  } catch (error) {
    showToast(`Upload failed: ${error.message}`, "error");
  }
  loadPluginMods();
}

// A .jar dropped anywhere on the panel goes to the folder this server loads: mods when only mods exist, plugins otherwise.
function wirePluginDrop() {
  const panel = document.getElementById("dtab-plugins");
  if (!panel || panel.dataset.dropReady) return;
  panel.dataset.dropReady = "1";
  const folderFor = () => (pluginView.mods.length && !pluginView.plugins.length ? "mods" : "plugins");
  ["dragenter", "dragover"].forEach(type => panel.addEventListener(type, event => {
    if (event.dataTransfer?.types?.includes("Files")) { event.preventDefault(); panel.classList.add("drop-target"); }
  }));
  panel.addEventListener("dragleave", event => { if (!panel.contains(event.relatedTarget)) panel.classList.remove("drop-target"); });
  panel.addEventListener("drop", event => {
    if (!event.dataTransfer?.files?.length) return;
    event.preventDefault();
    panel.classList.remove("drop-target");
    uploadPluginFiles(event.dataTransfer.files, folderFor());
  });
}

function showPluginConfigs(name) {
  loadPluginConfigs(name);
  document.querySelector(".plugin-config-panel")?.scrollIntoView({ behavior: "smooth", block: "start" });
}

async function disableBrokenPlugins(folder) {
  const files = await (await fetch(`/api/server/${currentServerId}/files?folder=${folder}`)).json();
  let done = 0;
  for (const file of files.filter(item => item.problem && item.enabled)) {
    try {
      await requestJson(`/api/server/${currentServerId}/plugins/toggle`, { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ folder, name: file.name, enabled: false }) });
      done++;
    } catch (error) {
      showToast(error.message, "error");
    }
  }
  if (done) showToast(`Disabled ${done} file${done === 1 ? "" : "s"}. Restart the server to apply.`, "success");
  loadPluginMods();
}

async function togglePlugin(folder, name, enabled) {
  try {
    const data = await requestJson(`/api/server/${currentServerId}/plugins/toggle`, {
      method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ folder, name, enabled })
    });
    if (!data.ok) throw new Error(data.error);
    showToast(`${enabled ? "Enabled" : "Disabled"} ${name.replace(/\.disabled$/, "")}. Restart the server to apply.`, "success");
  } catch (error) {
    showToast(error.message, "error");
  }
  loadPluginMods();
}

async function deletePlugin(folder, name) {
  if (!(await uiConfirm(`Delete ${name.replace(/\.disabled$/, "")}? Its configuration folder is kept.`, { title: "Delete plugin", confirmText: "Delete", danger: true, always: true }))) return;
  try {
    const data = await requestJson(`/api/server/${currentServerId}/plugins/delete`, {
      method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ folder, name })
    });
    if (!data.ok) throw new Error(data.error);
    showToast("Deleted", "success");
  } catch (error) {
    showToast(error.message, "error");
  }
  loadPluginMods();
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
  renderPluginLists();
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

async function loadServerSelect() {
  const sel = document.getElementById("search-server");
  try {
    const servers = await (await fetch("/api/servers")).json();
    sel.innerHTML = '<option value="">Target server...</option>' +
      servers.map(s => `<option value="${escapeHtml(s.id)}">${escapeHtml(s.name)} (${escapeHtml(s.type)} ${escapeHtml(s.version)})</option>`).join("");
  } catch (e) {
    console.error("Could not load servers for dropdown");
  }
}

const PAGE_SIZE = 30;

const searchState = { q: "", type: "plugin", offset: 0, loading: false, done: false };

const featuredState = {
  plugin: { offset: 0, loading: false, done: false },
  mod: { offset: 0, loading: false, done: false },
  modpack: { offset: 0, loading: false, done: false },
  cfpack: { offset: 0, loading: false, done: false }
};
const FEATURED_LISTS = { plugin: "featured-plugins", mod: "featured-mods", modpack: "featured-modpacks", cfpack: "featured-curseforge" };
let curseforgeConfigured = false;

// One place that knows which service answers which kind of search.
function listingUrl(type, query, offset) {
  if (type === "cfpack") return `/api/curseforge/search?q=${encodeURIComponent(query)}&offset=${offset}`;
  return query
    ? `/api/modrinth/search?q=${encodeURIComponent(query)}&type=${type}&offset=${offset}&limit=${PAGE_SIZE}`
    : `/api/modrinth/featured?type=${type}&offset=${offset}`;
}

// Shown above CurseForge lists when the key cannot search and only featured packs are available.
function showCurseForgeNote(data) {
  const note = document.getElementById("cf-note");
  note.hidden = !data.note;
  note.textContent = data.note || "";
}

async function openCurseForgeById() {
  const field = document.getElementById("cf-project-id");
  const id = field.value.trim();
  if (!/^\d{3,9}$/.test(id)) { showToast("Enter the numeric Project ID from the pack's CurseForge page", "warning"); return; }
  if (!curseforgeConfigured) { showToast("Add your CurseForge API key in Settings first", "warning"); return; }
  try {
    const data = await requestJson(`/api/curseforge/pack/${id}`);
    if (!data.ok) throw new Error(data.error);
    openModpackDialog(data.pack.project_id, data.pack.title, data.pack.icon_url, "curseforge");
  } catch (error) {
    showToast(error.message, "error");
  }
}

const CURSEFORGE_HINT = `<div class="empty">Add a free CurseForge API key in <a href="#" onclick="switchTab('settings'); return false;">Settings</a> to browse CurseForge modpacks.</div>`;

function projectCard(h, type, i, cls) {
  return `
      <article class="${cls}" style="--i:${i % 12}">
        ${h.icon_url ? `<img src="${escapeHtml(h.icon_url)}" alt="" loading="lazy" onerror="this.style.display='none'" />` : '<div class="project-icon"></div>'}
        <div class="info"><h4>${escapeHtml(h.title)}</h4><p>${escapeHtml(h.description || "")}</p><div class="stats">↓ ${(h.downloads || 0).toLocaleString()} downloads${h.author ? ` · ${escapeHtml(h.author)}` : ""}</div></div>
        ${type === "modpack" || type === "cfpack"
          ? `<button class="btn primary small" data-perm="manage" onclick="openModpackDialog(${jsArg(h.project_id || h.slug)}, ${jsArg(h.title)}, ${jsArg(h.icon_url || "")}, ${jsArg(type === "cfpack" ? "curseforge" : "modrinth")})">Create server</button>`
          : `<button class="btn primary small" onclick="installProject(${jsArg(h.project_id || h.slug)}, ${jsArg(h.title)}, ${jsArg(type)})">Install</button>`}
      </article>`;
}

async function searchModrinth() {
  const q = document.getElementById("search-q").value.trim();
  const type = document.getElementById("search-type").value;
  const results = document.getElementById("search-results");
  if (!q) {
    showToast("Please enter a search term", "warning");
    return;
  }
  if (type === "cfpack" && !curseforgeConfigured) {
    results.innerHTML = CURSEFORGE_HINT;
    return;
  }
  searchState.q = q;
  searchState.type = type;
  searchState.offset = 0;
  searchState.done = false;
  results.innerHTML = '<div class="loading">Searching...</div>';
  try {
    if (type === "cfpack" && /^\d{3,9}$/.test(q)) {  // a bare number is a CurseForge project ID
      const found = await requestJson(`/api/curseforge/pack/${q}`);
      if (!found.ok) throw new Error(found.error);
      searchState.done = true;
      results.innerHTML = projectCard(found.pack, "cfpack", 0, "result-card");
      return;
    }
    const data = await (await fetch(listingUrl(type, q, 0))).json();
    if (data.ok === false) throw new Error(data.error);
    if (type === "cfpack") showCurseForgeNote(data);
    const hits = data.hits || [];
    searchState.offset = hits.length;
    searchState.done = hits.length < PAGE_SIZE;
    results.innerHTML = hits.length
      ? hits.map((h, i) => projectCard(h, type, i, "result-card")).join("")
      : '<div class="empty">No results found</div>';
  } catch (e) {
    searchState.done = true;
    results.innerHTML = `<div class="empty">Search Error: ${escapeHtml(e.message)}</div>`;
  }
}

async function loadMoreSearch() {
  if (searchState.loading || searchState.done || !searchState.q) return;
  searchState.loading = true;
  try {
    const data = await (await fetch(listingUrl(searchState.type, searchState.q, searchState.offset))).json();
    const hits = data.hits || [];
    searchState.done = hits.length < (searchState.type === "cfpack" ? 30 : PAGE_SIZE);
    if (hits.length) {
      document.getElementById("search-results").insertAdjacentHTML("beforeend",
        hits.map((h, i) => projectCard(h, searchState.type, searchState.offset + i, "result-card")).join(""));
      searchState.offset += hits.length;
    }
  } catch (e) {
    searchState.done = true;
  } finally {
    searchState.loading = false;
  }
}

async function loadFeatured() {
  const types = Object.keys(FEATURED_LISTS);
  if (!types.every(type => document.getElementById(FEATURED_LISTS[type]))) return;
  types.forEach(type => { featuredState[type] = { offset: 0, loading: false, done: false }; });
  try { curseforgeConfigured = Boolean((await requestJson("/api/curseforge/status")).configured); } catch { curseforgeConfigured = false; }
  await Promise.all(types.map(async type => {
    const list = document.getElementById(FEATURED_LISTS[type]);
    if (type === "cfpack" && !curseforgeConfigured) {
      featuredState.cfpack.done = true;
      list.innerHTML = CURSEFORGE_HINT;
      return;
    }
    try {
      const data = await (await fetch(listingUrl(type, "", 0))).json();
      if (data.ok === false) throw new Error(data.error);
      if (type === "cfpack") showCurseForgeNote(data);
      const hits = data.hits || [];
      featuredState[type].offset = hits.length;
      featuredState[type].done = data.search_available === false || hits.length < (type === "cfpack" ? 30 : PAGE_SIZE);
      list.innerHTML = hits.length
        ? hits.map((h, i) => projectCard(h, type, i, "featured-item")).join("")
        : '<div class="empty">No projects found</div>';
    } catch (e) {
      featuredState[type].done = true;
      list.innerHTML = `<div class="empty">${escapeHtml(e.message || "Unavailable")}</div>`;
    }
  }));
}

async function loadMoreFeatured(type) {
  const state = featuredState[type];
  const list = document.getElementById(FEATURED_LISTS[type]);
  if (!state || !list || state.loading || state.done) return;
  state.loading = true;
  try {
    const data = await (await fetch(listingUrl(type, "", state.offset))).json();
    const hits = data.hits || [];
    state.done = hits.length < (type === "cfpack" ? 30 : PAGE_SIZE);
    if (hits.length) {
      list.insertAdjacentHTML("beforeend",
        hits.map((h, i) => projectCard(h, type, state.offset + i, "featured-item")).join(""));
      state.offset += hits.length;
    }
  } catch (e) {
    state.done = true;
  } finally {
    state.loading = false;
  }
}

// A modpack creates its own server, so the "target server" choice only applies to plugins and mods.
function syncSearchType() {
  const modpack = ["modpack", "cfpack"].includes(document.getElementById("search-type").value);
  document.getElementById("search-server").hidden = modpack;
}

function nearBottom(el, threshold = 220) {
  return el.scrollTop + el.clientHeight >= el.scrollHeight - threshold;
}

async function installProject(projectId, title, ptype, targetServerId = "") {
  const serverId = targetServerId || document.getElementById("search-server").value;
  if (!serverId) {
    showToast("Please select a target server from the dropdown", "warning");
    document.getElementById("search-server").focus();
    return;
  }
  try {
    showToast(`Finding latest version for ${title}...`, "success");
    const server = (await (await fetch("/api/servers")).json()).find(item => item.id === serverId);
    // Ask for builds that match this server's version and loader (Paper/Purpur plugins, Fabric/Forge/NeoForge mods).
    // Newer version names that Modrinth does not tag yet fall back to the newest build for the right loader.
    const base = `/api/modrinth/versions/${encodeURIComponent(projectId)}?server=${encodeURIComponent(serverId)}&type=${ptype === "mod" ? "mod" : "plugin"}`;
    let versions = await (await fetch(server?.version ? `${base}&version=${encodeURIComponent(server.version)}` : base)).json();
    let fellBack = false;
    if (!versions.length && server?.version && !/^1\./.test(server.version)) {
      versions = await (await fetch(base)).json();
      fellBack = versions.length > 0;
    }
    if (!versions.length) {
      showToast(`No ${title} build for this server's version and type`, "error");
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
    if (data.ok) showToast(fellBack ? `Installed ${title} to /${target}/ (newest build for this server type; none is tagged ${server.version})` : `Installed ${title} to /${target}/`, "success");
    else showToast(data.error || "Failed to install", "error");
  } catch (error) {
    showToast(`Install failed: ${error.message}`, "error");
  }
}
