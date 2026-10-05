// File manager, server.properties and plugin config editor.

async function loadFs() {
  if (!currentServerId) return;
  document.getElementById("fs-path").textContent = "/" + (fsPath || "");
  const data = await (await fetch(`/api/server/${currentServerId}/fs/list?path=${encodeURIComponent(fsPath)}`)).json();
  if (!data.ok) {
    document.getElementById("fs-list").innerHTML = `<div class="empty">${escapeHtml(data.error)}</div>`;
    return;
  }
  document.getElementById("fs-list").innerHTML = data.items.map(item => `
    <div class="fs-item">
      <span onclick="${item.is_dir ? `fsEnter(${jsArg(item.name)})` : `fsOpen(${jsArg(item.name)})`}" class="name">
        ${item.is_dir ? "📁" : "📄"} ${escapeHtml(item.name)}
      </span>
      <span class="meta">${item.is_dir ? "" : (item.size/1024).toFixed(1)+" KB"}</span>
      <span class="actions">
        ${!item.is_dir ? `<button class="btn small" onclick="fsDownload(${jsArg(item.name)})">↓</button>` : ""}
        <button class="btn danger small" onclick="fsDelete(${jsArg(item.name)})">✕</button>
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
