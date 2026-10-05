// Backups and automatic backups.

function stopBackupPolling() {
  if (backupJobTimer) clearInterval(backupJobTimer);
  backupJobTimer = null;
}

function renderBackupJob(job) {
  const button = document.getElementById("btn-backup-create");
  if (!job || job.state !== "running") {
    if (button) button.disabled = false;
    if (!job || job.state === "done") hideTaskProgress("backup");
    else if (job.state === "error") setTaskProgress("backup", 100, job.message);
    return false;
  }
  if (button) button.disabled = true;
  setTaskProgress("backup", job.progress, job.message);
  return true;
}

function startBackupPolling() {
  stopBackupPolling();
  const serverId = currentServerId;
  backupJobTimer = setInterval(async () => {
    if (currentServerId !== serverId) return stopBackupPolling();
    try {
      const data = await requestJson(`/api/server/${serverId}/backups/job`);
      if (renderBackupJob(data.job)) return;
      stopBackupPolling();
      showToast(data.job?.message || "Task finished", data.job?.state === "error" ? "error" : "success");
      loadBackups();
      loadServers();
    } catch {
      stopBackupPolling();
    }
  }, 1200);
}

async function loadBackups() {
  if (!currentServerId) return;
  const list = document.getElementById("backup-list");
  try {
    const data = await requestJson(`/api/server/${currentServerId}/backups`);
    if (!data.ok) throw new Error(data.error || "Could not read backups");
    document.getElementById("backup-keep").value = data.keep;
    document.getElementById("backup-count").textContent = `${data.backups.length} SAVED`;
    if (renderBackupJob(data.job)) startBackupPolling();
    list.innerHTML = data.backups.length ? data.backups.map((backup, i) => `
      <div class="backup-item" style="--i:${i}">
        <div class="backup-meta">
          <strong>${new Date(backup.created).toLocaleString()}</strong>
          <span>${formatBytes(backup.size)} · ${backup.files} files${backup.world ? "" : " · no world"}${backup.automatic ? " · automatic" : ""}</span>
          ${backup.label ? `<small>${escapeHtml(backup.label)}</small>` : ""}
        </div>
        <div class="backup-item-actions">
          <button class="btn small" onclick="downloadBackup(${jsArg(backup.name)})">↓ Download</button>
          <button class="btn warning small" onclick="restoreBackup(this, ${jsArg(backup.name)})"${data.running ? " disabled title='Stop the server first'" : ""}>↺ Restore</button>
          <button class="btn danger small" onclick="deleteBackup(this, ${jsArg(backup.name)})" title="Delete this backup">✕</button>
        </div>
      </div>`).join("") : '<div class="empty">No backups yet. Create one before updating plugins or the server version.</div>';
  } catch (error) {
    list.innerHTML = `<div class="empty">${escapeHtml(error.message)}</div>`;
  }
  loadAutoBackup();
}

async function loadAutoBackup() {
  if (!currentServerId) return;
  try {
    const data = await requestJson(`/api/server/${currentServerId}/auto-backup`);
    if (!data.ok) return;
    document.getElementById("auto-backup-enabled").checked = data.settings.enabled;
    document.getElementById("auto-backup-interval").value = data.settings.interval_hours;
    document.getElementById("auto-backup-world").checked = data.settings.world;
    const badge = document.getElementById("auto-backup-badge");
    badge.textContent = data.settings.enabled ? "On" : "Off";
    badge.className = "badge " + (data.settings.enabled ? "online" : "offline");
    document.getElementById("auto-backup-last").textContent = data.last_run
      ? `Last automatic backup: ${new Date(data.last_run).toLocaleString()}`
      : "Never run yet.";
  } catch { /* server may have just been deleted mid-refresh */ }
}

async function saveAutoBackup() {
  if (!currentServerId) return;
  const payload = {
    enabled: document.getElementById("auto-backup-enabled").checked,
    interval_hours: Number(document.getElementById("auto-backup-interval").value) || 6,
    world: document.getElementById("auto-backup-world").checked
  };
  const data = await requestJson(`/api/server/${currentServerId}/auto-backup`, {
    method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(payload)
  });
  if (data.ok) {
    showToast(payload.enabled ? "Automatic backups enabled" : "Automatic backups disabled", "success");
    loadAutoBackup();
  }
}

async function createBackup() {
  if (!currentServerId) return;
  const payload = {
    label: document.getElementById("backup-label").value.trim(),
    world: document.getElementById("backup-world").checked,
    keep: Number(document.getElementById("backup-keep").value) || 0
  };
  try {
    setTaskProgress("backup", 0, "Starting backup...");
    const data = await requestJson(`/api/server/${currentServerId}/backups/create`, {
      method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(payload)
    });
    if (!data.ok) throw new Error(data.message || "Could not start the backup");
    document.getElementById("backup-label").value = "";
    startBackupPolling();
  } catch (error) {
    hideTaskProgress("backup");
    showToast(error.message, "error");
  }
}

async function restoreBackup(button, name) {
  if (!currentServerId || button.disabled) return;
  if (!(await uiConfirm("Restoring replaces every file in this server folder with the contents of the backup. A copy of the current files is saved first. Continue?", { danger: true, confirmText: "Continue" }))) return;
  button.disabled = true;
  try {
    setTaskProgress("backup", 0, "Starting restore...");
    const data = await requestJson(`/api/server/${currentServerId}/backups/${encodeURIComponent(name)}/restore`, {
      method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ safety: true })
    });
    if (!data.ok) throw new Error(data.error || data.message || "Could not start the restore");
    startBackupPolling();
  } catch (error) {
    hideTaskProgress("backup");
    showToast(error.message, "error");
    button.disabled = false;
  }
}

async function deleteBackup(button, name) {
  if (!currentServerId || button.disabled) return;
  if (!(await uiConfirm("Delete this backup permanently?", { danger: true, confirmText: "Continue" }))) return;
  button.disabled = true;
  try {
    const data = await requestJson(`/api/server/${currentServerId}/backups/${encodeURIComponent(name)}/delete`, { method: "POST" });
    if (!data.ok) throw new Error(data.error || "Could not delete the backup");
    showToast("Backup deleted", "success");
    loadBackups();
  } catch (error) {
    showToast(error.message, "error");
    button.disabled = false;
  }
}

function downloadBackup(name) {
  window.open(`/api/server/${currentServerId}/backups/${encodeURIComponent(name)}/download`, "_blank");
}
