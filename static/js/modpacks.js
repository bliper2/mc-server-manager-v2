// Modpacks: pick a Modrinth pack, then create a Fabric server from it while a background job reports progress.

const MODPACK_JOB_KEY = "mc-manager-modpack-job";
let modpackProject = null;
let modpackSource = "modrinth";
let modpackJobTimer = null;

function suggestPort() {
  const used = new Set(knownServers.map(server => Number(server.port)));
  let port = 25565;
  while (used.has(port) && port < 65535) port += 1;
  return port;
}

function closeModpackDialog() {
  document.getElementById("modpack-dialog").close();
}

function versionLabel(file) {
  const loader = file.loader_name ? ` · ${file.loader_name}` : "";
  return `${file.name} · Minecraft ${file.minecraft || "?"}${loader} · ${file.release}${file.server_pack ? " · server pack" : ""}${file.supported === false ? " · not supported" : ""}`;
}

// Normalises both services into the same list of {id, label, supported, serverPack}.
async function loadPackVersions(source, projectId) {
  if (source === "curseforge") {
    const data = await (await fetch(`/api/curseforge/files/${encodeURIComponent(projectId)}`)).json();
    if (!data.ok) throw new Error(data.error);
    return data.files.map(file => ({ id: String(file.id), label: versionLabel(file), supported: file.supported, serverPack: file.server_pack }));
  }
  const versions = await (await fetch(`/api/modrinth/versions/${encodeURIComponent(projectId)}`)).json();
  return versions.filter(version => (version.files || []).some(file => file.filename?.endsWith(".mrpack"))).map(version => {
    const loaders = (version.loaders || []).filter(name => ["fabric", "forge", "neoforge", "quilt"].includes(name));
    const supported = loaders.some(name => name !== "quilt");
    return {
      id: version.id, supported, serverPack: false,
      label: `${version.version_number} · Minecraft ${(version.game_versions || []).slice(-1)[0] || "?"} · ${loaders.join("/") || "?"} · ${version.version_type || "release"}${supported ? "" : " · not supported"}`
    };
  });
}

function updateModpackNote() {
  const note = document.getElementById("mp-note");
  const chosen = document.getElementById("mp-version").selectedOptions[0];
  const messages = [];
  if (chosen?.dataset.supported === "false") messages.push("The manager cannot install this loader yet. Pick another version (Fabric, Forge and NeoForge work).");
  else if (modpackSource === "curseforge" && chosen && chosen.dataset.serverPack !== "true") messages.push("This version has no official server pack, so it is built from the client pack. Client-only mods may need removing if the server crashes on start.");
  note.hidden = !messages.length;
  note.textContent = messages.join(" ");
  document.getElementById("mp-create").disabled = !chosen || !chosen.value || chosen.dataset.supported === "false";
}

async function openModpackDialog(projectId, title, iconUrl, source = "modrinth") {
  if (!can("manage")) {
    showToast("Creating a server needs the 'create, import and delete servers' permission", "warning");
    return;
  }
  modpackProject = projectId;
  modpackSource = source;
  document.getElementById("mp-title").textContent = title;
  document.getElementById("mp-intro").textContent = source === "curseforge"
    ? "Creates a new server from this CurseForge pack. The pack's official server pack is used when it has one. Every download is checked against its checksum."
    : "Creates a new server with the pack's mods and settings. Client-only mods are skipped and every file is checked against its checksum.";
  const icon = document.getElementById("mp-icon");
  icon.hidden = !iconUrl;
  if (iconUrl) icon.src = iconUrl;
  document.getElementById("mp-name").value = title.slice(0, 40);
  document.getElementById("mp-port").value = suggestPort();
  document.getElementById("mp-eula").checked = false;
  document.getElementById("mp-status").className = "status-msg";
  document.getElementById("mp-note").hidden = true;
  hideTaskProgress("mp");
  const select = document.getElementById("mp-version");
  select.innerHTML = "<option>Loading versions...</option>";
  document.getElementById("mp-create").disabled = true;
  document.getElementById("modpack-dialog").showModal();
  try {
    const versions = await loadPackVersions(source, projectId);
    select.innerHTML = versions.length
      ? versions.map(version => `<option value="${escapeHtml(version.id)}" data-supported="${version.supported}" data-server-pack="${version.serverPack}">${escapeHtml(version.label)}</option>`).join("")
      : '<option value="">No installable version of this pack found</option>';
    const firstUsable = [...select.options].findIndex(option => option.dataset.supported !== "false");
    if (firstUsable > 0) select.selectedIndex = firstUsable;
    updateModpackNote();
  } catch (error) {
    select.innerHTML = `<option value="">${escapeHtml(error.message)}</option>`;
  }
}

function setModpackStatus(message, ok = false) {
  const status = document.getElementById("mp-status");
  status.textContent = message;
  status.className = `status-msg show ${ok ? "ok" : "err"}`;
}

async function startModpackInstall(button) {
  const versionId = document.getElementById("mp-version").value;
  if (!versionId) return setModpackStatus("Choose a pack version");
  if (!document.getElementById("mp-eula").checked) return setModpackStatus("Tick the box to accept the Minecraft EULA");
  await withBusy(button, async () => {
    try {
      const response = await fetch("/api/modpacks/install", {
        method: "POST", headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          source: modpackSource,
          ...(modpackSource === "curseforge" ? { project_id: modpackProject, file_id: versionId } : { version_id: versionId }),
          pack_title: document.getElementById("mp-title").textContent,
          name: document.getElementById("mp-name").value.trim(),
          port: Number(document.getElementById("mp-port").value),
          ram: Number(document.getElementById("mp-ram").value),
          accept_eula: true
        })
      });
      const data = await response.json();
      if (!data.ok) throw new Error(data.error || "Could not start the install");
      document.getElementById("mp-status").className = "status-msg";
      try { sessionStorage.setItem(MODPACK_JOB_KEY, data.job); } catch {}
      pollModpackJob(data.job, true);
    } catch (error) {
      setModpackStatus(error.message);
    }
  });
}

// Keeps polling even if the dialog is closed, and picks the job up again after a page reload.
function pollModpackJob(jobId, showDialogProgress) {
  clearInterval(modpackJobTimer);
  const tick = async () => {
    try {
      const job = await (await fetch(`/api/modpacks/job/${encodeURIComponent(jobId)}`)).json();
      if (!job.ok) throw new Error(job.error || "Install not found");
      const dialogOpen = document.getElementById("modpack-dialog").open;
      if (dialogOpen && showDialogProgress) setTaskProgress("mp", job.progress, job.message);
      if (job.state === "running") return;
      clearInterval(modpackJobTimer);
      try { sessionStorage.removeItem(MODPACK_JOB_KEY); } catch {}
      if (job.state === "done") {
        showToast(job.message, "success");
        if (dialogOpen) closeModpackDialog();
        serversHtml = "";
        serversSettled = false;
        switchTab("servers");
      } else {
        hideTaskProgress("mp");
        if (dialogOpen) setModpackStatus(job.message);
        showToast(job.message, "error");
      }
    } catch (error) {
      clearInterval(modpackJobTimer);
      try { sessionStorage.removeItem(MODPACK_JOB_KEY); } catch {}
    }
  };
  tick();
  modpackJobTimer = setInterval(tick, 1000);
}

function resumeModpackInstall() {
  let job = null;
  try { job = sessionStorage.getItem(MODPACK_JOB_KEY); } catch {}
  if (job) pollModpackJob(job, false);
}
