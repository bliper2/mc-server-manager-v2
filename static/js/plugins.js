// Installed plugins and mods, Modrinth browser and plugin update checker.

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
  mod: { offset: 0, loading: false, done: false }
};

function projectCard(h, type, i, cls) {
  return `
      <article class="${cls}" style="--i:${i % 12}">
        ${h.icon_url ? `<img src="${escapeHtml(h.icon_url)}" alt="" loading="lazy" onerror="this.style.display='none'" />` : '<div class="project-icon"></div>'}
        <div class="info"><h4>${escapeHtml(h.title)}</h4><p>${escapeHtml(h.description || "")}</p><div class="stats">↓ ${(h.downloads || 0).toLocaleString()} downloads${h.author ? ` · ${escapeHtml(h.author)}` : ""}</div></div>
        <button class="btn primary small" onclick="installProject(${jsArg(h.project_id || h.slug)}, ${jsArg(h.title)}, ${jsArg(type)})">Install</button>
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
  searchState.q = q;
  searchState.type = type;
  searchState.offset = 0;
  searchState.done = false;
  results.innerHTML = '<div class="loading">Searching...</div>';
  try {
    const data = await (await fetch(`/api/modrinth/search?q=${encodeURIComponent(q)}&type=${type}&offset=0&limit=${PAGE_SIZE}`)).json();
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
    const data = await (await fetch(`/api/modrinth/search?q=${encodeURIComponent(searchState.q)}&type=${searchState.type}&offset=${searchState.offset}&limit=${PAGE_SIZE}`)).json();
    const hits = data.hits || [];
    searchState.done = hits.length < PAGE_SIZE;
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
  const plugins = document.getElementById("featured-plugins");
  const mods = document.getElementById("featured-mods");
  if (!plugins || !mods) return;
  featuredState.plugin = { offset: 0, loading: false, done: false };
  featuredState.mod = { offset: 0, loading: false, done: false };
  try {
    const [pluginData, modData] = await Promise.all([
      (await fetch("/api/modrinth/featured?type=plugin&offset=0")).json(),
      (await fetch("/api/modrinth/featured?type=mod&offset=0")).json()
    ]);
    const pluginHits = pluginData.hits || [];
    const modHits = modData.hits || [];
    featuredState.plugin.offset = pluginHits.length;
    featuredState.plugin.done = pluginHits.length < PAGE_SIZE;
    featuredState.mod.offset = modHits.length;
    featuredState.mod.done = modHits.length < PAGE_SIZE;
    plugins.innerHTML = pluginHits.length
      ? pluginHits.map((h, i) => projectCard(h, "plugin", i, "featured-item")).join("")
      : '<div class="empty">No projects found</div>';
    mods.innerHTML = modHits.length
      ? modHits.map((h, i) => projectCard(h, "mod", i, "featured-item")).join("")
      : '<div class="empty">No projects found</div>';
  } catch (e) {
    featuredState.plugin.done = true;
    featuredState.mod.done = true;
    plugins.innerHTML = '<div class="empty">Featured plugins unavailable</div>';
    mods.innerHTML = '<div class="empty">Featured mods unavailable</div>';
  }
}

async function loadMoreFeatured(type) {
  const state = featuredState[type];
  const list = document.getElementById(type === "plugin" ? "featured-plugins" : "featured-mods");
  if (!state || !list || state.loading || state.done) return;
  state.loading = true;
  try {
    const data = await (await fetch(`/api/modrinth/featured?type=${type}&offset=${state.offset}`)).json();
    const hits = data.hits || [];
    state.done = hits.length < PAGE_SIZE;
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

function nearBottom(el, threshold = 220) {
  return el.scrollTop + el.clientHeight >= el.scrollHeight - threshold;
}

async function installProject(projectId, title, ptype) {
  const serverId = document.getElementById("search-server").value;
  if (!serverId) {
    showToast("Please select a target server from the dropdown", "warning");
    document.getElementById("search-server").focus();
    return;
  }
  try {
    showToast(`Finding latest version for ${title}...`, "success");
    const server = (await (await fetch("/api/servers")).json()).find(item => item.id === serverId);
    const query = server?.version && /^1\./.test(server.version) ? `?version=${encodeURIComponent(server.version)}` : "";
    const versions = await (await fetch(`/api/modrinth/versions/${encodeURIComponent(projectId)}${query}`)).json();
    if (!versions.length) {
      showToast(`No ${title} build found for this server's version`, "error");
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
    if (data.ok) showToast(`Installed ${title} to /${target}/`, "success");
    else showToast(data.error || "Failed to install", "error");
  } catch (error) {
    showToast(`Install failed: ${error.message}`, "error");
  }
}
