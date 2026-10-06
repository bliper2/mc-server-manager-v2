// File manager, server.properties and plugin config editor.

let fsDirty = false;

function setFsDirty(value) {
  fsDirty = value;
  const mark = document.getElementById("fs-dirty");
  if (mark) mark.hidden = !value;
}

function renderBreadcrumbs() {
  const nav = document.getElementById("fs-path");
  let walked = "";
  nav.innerHTML = `<button type="button" class="crumb" onclick="fsGo('')">server</button>` + (fsPath ? fsPath.split("/") : []).map(part => {
    walked = walked ? `${walked}/${part}` : part;
    return `<span class="crumb-sep">/</span><button type="button" class="crumb" onclick="fsGo(${jsArg(walked)})">${escapeHtml(part)}</button>`;
  }).join("");
}

async function loadFs() {
  if (!currentServerId) return;
  renderBreadcrumbs();
  let data;
  try {
    data = await (await fetch(`/api/server/${currentServerId}/fs/list?path=${encodeURIComponent(fsPath)}`)).json();
  } catch (error) {
    document.getElementById("fs-list").innerHTML = `<div class="empty">${escapeHtml(error.message)}</div>`;
    return;
  }
  if (!data.ok) {
    document.getElementById("fs-list").innerHTML = `<div class="empty">${escapeHtml(data.error)}</div>`;
    return;
  }
  document.getElementById("fs-list").innerHTML = data.items.map(item => `
    <div class="fs-item">
      <span onclick="${item.is_dir ? `fsEnter(${jsArg(item.name)})` : `fsOpen(${jsArg(item.name)})`}" class="name" tabindex="0" role="button">
        ${item.is_dir ? "📁" : "📄"} ${escapeHtml(item.name)}
      </span>
      <span class="meta" title="${escapeHtml(new Date(item.modified).toLocaleString())}">${item.is_dir ? "" : `${formatBytes(item.size)} · `}${relativeTime(item.modified)}</span>
      <span class="actions">
        <button class="btn small" data-perm="files" onclick="fsRename(${jsArg(item.name)})" title="Rename" aria-label="Rename ${escapeHtml(item.name)}">✎</button>
        ${!item.is_dir ? `<button class="btn small" onclick="fsDownload(${jsArg(item.name)})" title="Download" aria-label="Download ${escapeHtml(item.name)}">↓</button>` : ""}
        <button class="btn danger small" data-perm="files" onclick="fsDelete(${jsArg(item.name)})" title="Delete" aria-label="Delete ${escapeHtml(item.name)}">✕</button>
      </span>
    </div>`).join("") || '<div class="empty">Empty folder</div>';
}

async function fsGo(path) {
  if (!(await fsCloseEditor())) return;
  fsPath = path;
  loadFs();
}

async function fsEnter(name) {
  await fsGo(fsPath ? `${fsPath}/${name}` : name);
}

async function fsUp() {
  if (!fsPath) return;
  const parts = fsPath.split("/");
  parts.pop();
  await fsGo(parts.join("/"));
}

async function fsOpen(name) {
  if (!(await fsCloseEditor())) return;
  const path = fsPath ? `${fsPath}/${name}` : name;
  let data;
  try {
    data = await (await fetch(`/api/server/${currentServerId}/fs/read?path=${encodeURIComponent(path)}`)).json();
  } catch (error) {
    showToast(error.message, "error");
    return;
  }
  if (!data.ok) {
    showToast(data.error, "error");
    return;
  }
  fsEditPath = path;
  document.getElementById("fs-edit-name").textContent = path;
  document.getElementById("fs-content").value = data.content;
  document.getElementById("fs-editor").style.display = "block";
  setFsDirty(false);
}

// Opens a file given its full path (used by the crash report list).
async function fsOpenPath(path) {
  const parts = path.split("/");
  const name = parts.pop();
  switchDetailTab("files");
  if (!(await fsCloseEditor())) return;
  fsPath = parts.join("/");
  await loadFs();
  fsOpen(name);
}

async function fsSave() {
  if (!fsEditPath) return;
  const content = document.getElementById("fs-content").value;
  try {
    const data = await (await fetch(`/api/server/${currentServerId}/fs/write`, {
      method: "POST", headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ path: fsEditPath, content })
    })).json();
    if (data.ok) { showToast("File saved", "success"); setFsDirty(false); }
    else showToast(data.error, "error");
  } catch (error) {
    showToast(error.message, "error");
  }
}

// Resolves true when the editor is closed (or was not open). Asks first when there are unsaved changes.
async function fsCloseEditor() {
  if (fsDirty && !(await uiAsk({ title: "Discard changes?", message: `${fsEditPath} has changes that were not saved.`, confirmText: "Discard", danger: true }))) return false;
  document.getElementById("fs-editor").style.display = "none";
  fsEditPath = null;
  setFsDirty(false);
  return true;
}

async function fsDelete(name) {
  if (!(await uiConfirm("Are you sure you want to delete " + name + "?", { danger: true, confirmText: "Continue" }))) return;
  const path = fsPath ? fsPath + "/" + name : name;
  await fetch(`/api/server/${currentServerId}/fs/delete`, {
    method: "POST", headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ path })
  });
  showToast("Deleted " + name, "success");
  loadFs();
}

async function fsNewFolder() {
  const name = await uiAsk({ title: "New folder", confirmText: "Create", input: { placeholder: "Folder name", required: true } });
  if (!name) return;
  const path = fsPath ? `${fsPath}/${name}` : name;
  await fetch(`/api/server/${currentServerId}/fs/mkdir`, {
    method: "POST", headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ path })
  });
  loadFs();
}

async function fsNewFile() {
  const name = await uiAsk({ title: "New file", confirmText: "Create", input: { placeholder: "motd.txt", required: true } });
  if (!name) return;
  const path = fsPath ? `${fsPath}/${name}` : name;
  try {
    const data = await (await fetch(`/api/server/${currentServerId}/fs/write`, {
      method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ path, content: "" })
    })).json();
    if (!data.ok) throw new Error(data.error);
    await loadFs();
    fsOpen(name);
  } catch (error) {
    showToast(error.message, "error");
  }
}

async function fsRename(name) {
  const next = await uiAsk({ title: "Rename", confirmText: "Rename", input: { value: name, required: true } });
  if (!next || next === name) return;
  const path = fsPath ? `${fsPath}/${name}` : name;
  try {
    const data = await (await fetch(`/api/server/${currentServerId}/fs/rename`, {
      method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ path, name: next })
    })).json();
    if (!data.ok) throw new Error(data.error);
    showToast(`Renamed to ${next}`, "success");
    if (fsEditPath === path) fsCloseEditor();
    loadFs();
  } catch (error) {
    showToast(error.message, "error");
  }
}

async function fsUpload() {
  const input = document.getElementById("fs-upload");
  const files = [...(input.files || [])];
  if (!files.length) return;
  let done = 0;
  for (const file of files) {
    const fd = new FormData();
    fd.append("file", file);
    fd.append("path", fsPath);
    try {
      const data = await (await fetch(`/api/server/${currentServerId}/fs/upload`, { method: "POST", body: fd })).json();
      if (data.ok) done += 1;
      else showToast(`${file.name}: ${data.error}`, "error");
    } catch (error) {
      showToast(`${file.name}: ${error.message}`, "error");
    }
  }
  if (done) showToast(done === 1 ? `Uploaded ${files[0].name}` : `Uploaded ${done} files`, "success");
  input.value = "";
  loadFs();
}

function fsDownload(name) {
  const path = fsPath ? fsPath + "/" + name : name;
  window.open(`/api/server/${currentServerId}/fs/download?path=${encodeURIComponent(path)}`, "_blank");
}

const PROP_INFO = {
  "server-port": ["The port players connect to. Each server needs its own.", "number"],
  "max-players": ["How many players can be online at once.", "number"],
  "view-distance": ["How far players see, in chunks. Lower is faster (default 10).", "number"],
  "simulation-distance": ["How far mobs and crops are simulated, in chunks.", "number"],
  "spawn-protection": ["Radius around spawn that only operators can change (0 turns it off).", "number"],
  "motd": ["The line shown under the server name in the multiplayer list.", "text"],
  "level-name": ["World folder name. Changing it starts a new world.", "text"],
  "level-seed": ["Seed used when a new world is generated.", "text"],
  "online-mode": ["Verify accounts with Mojang. Off lets anyone join with any name.", "bool"],
  "pvp": ["Players can damage each other.", "bool"],
  "hardcore": ["Players are banned on death.", "bool"],
  "allow-flight": ["Stop kicking players for flying (needed by some mods and plugins).", "bool"],
  "allow-nether": ["Enable the Nether.", "bool"],
  "enable-command-block": ["Allow command blocks to run server commands.", "bool"],
  "white-list": ["Only approved players can join.", "bool"],
  "enforce-whitelist": ["Kick players who are not whitelisted when the list changes.", "bool"],
  "spawn-monsters": ["Hostile mobs spawn.", "bool"],
  "spawn-animals": ["Animals spawn.", "bool"],
  "generate-structures": ["Villages, temples and other structures generate.", "bool"],
  "force-gamemode": ["Put players in the default game mode every time they join.", "bool"],
  "enable-rcon": ["Allow remote console connections (needed by the live map).", "bool"],
  "op-permission-level": ["Permission level given to operators (1 to 4).", "number"],
  "max-world-size": ["Largest world radius in blocks.", "number"],
  "network-compression-threshold": ["Packet size that gets compressed. -1 turns compression off.", "number"],
  "player-idle-timeout": ["Minutes before an idle player is kicked (0 = never).", "number"]
};
const PROP_CHOICES = {
  gamemode: ["survival", "creative", "adventure", "spectator"],
  difficulty: ["peaceful", "easy", "normal", "hard"]
};
const IMPORTANT_PROPS = ["server-port", "max-players", "gamemode", "difficulty", "motd", "online-mode", "view-distance", "spawn-protection", "pvp", "allow-nether", "enable-command-block", "white-list"];

function propControl(key, value) {
  const info = PROP_INFO[key];
  const kind = PROP_CHOICES[key] ? "choice" : info?.[1] || (/^(true|false)$/.test(value) ? "bool" : /^-?\d+$/.test(value) ? "number" : "text");
  if (kind === "choice") return `<select data-prop="${escapeHtml(key)}">${PROP_CHOICES[key].map(option => `<option ${option === value ? "selected" : ""}>${option}</option>`).join("")}</select>`;
  if (kind === "bool") return `<select data-prop="${escapeHtml(key)}"><option ${value === "true" ? "selected" : ""}>true</option><option ${value !== "true" ? "selected" : ""}>false</option></select>`;
  if (kind === "number") return `<input type="number" data-prop="${escapeHtml(key)}" value="${escapeHtml(value)}" />`;
  return `<input type="text" data-prop="${escapeHtml(key)}" value="${escapeHtml(value)}" />`;
}

async function loadProps() {
  if (!currentServerId) return;
  const data = await (await fetch(`/api/server/${currentServerId}/properties`)).json();
  const props = data.props || {};
  document.getElementById("props-raw").value = data.raw || Object.entries(props).map(([key, value]) => `${key}=${value}`).join("\n");
  const keys = Object.keys(props).sort();
  const ordered = [...IMPORTANT_PROPS.filter(k => k in props), ...keys.filter(k => !IMPORTANT_PROPS.includes(k))];
  document.getElementById("props-form").innerHTML = ordered.map(k => {
    const value = String(props[k] ?? "");
    const hint = PROP_INFO[k]?.[0] || "";
    return `<div class="form-group" data-key="${escapeHtml(k)}" data-search="${escapeHtml(`${k} ${hint}`.toLowerCase())}"><label>${escapeHtml(k)}${["motd", "server-port", "max-players"].includes(k) ? ' <span class="property-hint">common</span>' : ""}</label>${propControl(k, value)}${hint ? `<small class="field-hint">${escapeHtml(hint)}</small>` : ""}</div>`;
  }).join("");
  filterProps();
}

function filterProps() {
  const query = (document.getElementById("props-filter")?.value || "").trim().toLowerCase();
  document.querySelectorAll("#props-form .form-group").forEach(group => { group.hidden = Boolean(query) && !group.dataset.search.includes(query); });
}

// ----- launch settings (memory and Java flags) -----

function updateLaunchUi() {
  const mode = document.getElementById("launch-flags").value;
  document.getElementById("launch-custom-group").hidden = mode !== "custom";
  const badge = document.getElementById("launch-badge");
  badge.textContent = { default: "Default", optimized: "Optimized", custom: "Custom" }[mode];
  badge.className = `badge ${mode === "default" ? "offline" : "online"}`;
}

async function loadLaunch() {
  if (!currentServerId) return;
  try {
    const data = await requestJson(`/api/server/${currentServerId}/launch`);
    document.getElementById("launch-ram").value = data.ram;
    document.getElementById("launch-flags").value = data.flags;
    document.getElementById("launch-custom").value = data.custom_flags;
    updateLaunchUi();
  } catch (error) {
    showToast(error.message, "error");
  }
}

async function saveLaunch(button) {
  await withBusy(button, async () => {
    try {
      const data = await requestJson(`/api/server/${currentServerId}/launch`, {
        method: "POST", headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ ram: Number(document.getElementById("launch-ram").value), flags: document.getElementById("launch-flags").value, custom_flags: document.getElementById("launch-custom").value })
      });
      if (!data.ok) throw new Error(data.error);
      currentServerRam = data.ram;
      showToast(data.restart_required ? "Saved. Restart the server to use the new settings." : "Launch settings saved", "success");
      loadLaunch();
    } catch (error) {
      showToast(error.message, "error");
    }
  });
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

async function loadPluginConfigs() {
  if (!currentServerId) return;
  const list = document.getElementById("plugin-config-list");
  if (!list) return;
  const configs = await (await fetch(`/api/server/${currentServerId}/plugin-configs`)).json();
  list.innerHTML = configs.length ? configs.map(config => `<button class="plugin-config-item" onclick="openPluginConfig(${jsArg(config.path)})"><span>${escapeHtml(config.path)}</span><small>${(config.size / 1024).toFixed(1)} KB</small></button>`).join("") : '<div class="empty">No YAML or JSON configs found</div>';
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
