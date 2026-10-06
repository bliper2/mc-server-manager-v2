// Modpacks: pick a Modrinth pack, then create a Fabric server from it while a background job reports progress.

const MODPACK_JOB_KEY = "mc-manager-modpack-job";
let modpackProject = null;
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

async function openModpackDialog(projectId, title, iconUrl) {
  if (!can("manage")) {
    showToast("Creating a server needs the 'create, import and delete servers' permission", "warning");
    return;
  }
  modpackProject = projectId;
  document.getElementById("mp-title").textContent = title;
  const icon = document.getElementById("mp-icon");
  icon.hidden = !iconUrl;
  if (iconUrl) icon.src = iconUrl;
  document.getElementById("mp-name").value = title.slice(0, 40);
  document.getElementById("mp-port").value = suggestPort();
  document.getElementById("mp-eula").checked = false;
  document.getElementById("mp-status").className = "status-msg";
  hideTaskProgress("mp");
  const select = document.getElementById("mp-version");
  const create = document.getElementById("mp-create");
  select.innerHTML = "<option>Loading versions...</option>";
  create.disabled = true;
  document.getElementById("modpack-dialog").showModal();
  try {
    const versions = await (await fetch(`/api/modrinth/versions/${encodeURIComponent(projectId)}?loader=fabric`)).json();
    const usable = versions.filter(version => (version.files || []).some(file => file.filename?.endsWith(".mrpack")));
    select.innerHTML = usable.length
      ? usable.map(version => `<option value="${escapeHtml(version.id)}">${escapeHtml(version.version_number)} · Minecraft ${escapeHtml((version.game_versions || []).slice(-1)[0] || "?")} · ${escapeHtml(version.version_type || "release")}</option>`).join("")
      : "<option value=\"\">No Fabric version of this pack found</option>";
    create.disabled = !usable.length;
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
          version_id: versionId,
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
