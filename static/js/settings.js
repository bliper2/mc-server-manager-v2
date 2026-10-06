// Manager updates, account and security settings.

// ----- manager updates -----

async function loadManagerUpdate() {
  try {
    const data = await requestJson("/api/manager/update");
    document.getElementById("update-auto-check").checked = data.auto_check;
    document.getElementById("update-auto-install").checked = data.auto_install;
    document.getElementById("update-channel").value = data.channel;
    document.getElementById("update-auto-install").disabled = data.channel !== "releases";
    document.getElementById("update-installed").textContent = data.installed
      ? `${data.installed.slice(0, 7)} from ${data.repo}`
      : `Unknown version of ${data.repo}`;
    renderUpdateLatest(data.latest, data.installed);
  } catch { /* settings tab can open before the panel finishes starting */ }
}

function renderUpdateLatest(latest, installed) {
  const badge = document.getElementById("update-badge");
  const detail = document.getElementById("update-detail");
  const apply = document.getElementById("btn-update-apply");
  if (!latest) {
    badge.textContent = "Not checked";
    badge.className = "badge offline";
    detail.hidden = true;
    apply.hidden = true;
    return;
  }
  const behind = latest.update_available;
  badge.textContent = behind ? (latest.release ? `${latest.release.tag} available` : (latest.count ? `${latest.count} update${latest.count === 1 ? "" : "s"}` : "Update available")) : "Up to date";
  badge.className = `badge ${behind ? "type" : "online"}`;
  apply.hidden = !behind || !IS_OWNER;
  detail.hidden = false;
  const files = (latest.files || []).map(file => `<li><span class="file-status ${escapeHtml(file.status)}">${escapeHtml(file.status[0].toUpperCase())}</span><code>${escapeHtml(file.name)}</code><small>+${file.added} -${file.removed}</small></li>`).join("");
  const notes = latest.release?.notes ? `<div class="release-notes">${escapeHtml(latest.release.notes)}</div>` : "";
  detail.innerHTML = `<div class="row"><span>${latest.release ? "Release" : "Latest"}</span><span>${escapeHtml(latest.release ? `${latest.release.name} (${latest.short})` : latest.short)} · ${new Date(latest.date).toLocaleString()}</span></div>
    <div class="row"><span>Message</span><span>${escapeHtml(latest.message)}</span></div>
    ${notes}
    ${latest.behind?.length ? `<ul class="update-log">${latest.behind.map(m => `<li>${escapeHtml(m)}</li>`).join("")}</ul>` : ""}
    ${files ? `<details class="update-files"><summary>${latest.files_total || latest.files.length} file(s) will change</summary><ul>${files}</ul></details>` : ""}`;
}

async function checkManagerUpdate() {
  const button = document.getElementById("btn-update-check");
  await withBusy(button, async () => {
    try {
      const data = await requestJson("/api/manager/update/check", { method: "POST" });
      if (!data.ok) throw new Error(data.error || "Check failed");
      renderUpdateLatest(data.latest, data.installed);
      showToast(data.latest.update_available ? "Update available" : "Already up to date", "success");
    } catch (error) {
      showToast(error.message, "error");
    }
  });
}

async function saveUpdateSettings() {
  if (!IS_OWNER) return;
  const channel = document.getElementById("update-channel").value;
  document.getElementById("update-auto-install").disabled = channel !== "releases";
  try {
    await requestJson("/api/manager/update/settings", {
      method: "POST", headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        auto_check: document.getElementById("update-auto-check").checked,
        auto_install: document.getElementById("update-auto-install").checked && channel === "releases",
        channel
      })
    });
    loadManagerUpdate();
  } catch (error) {
    showToast(error.message, "error");
  }
}

async function applyManagerUpdate() {
  const button = document.getElementById("btn-update-apply");
  if (!(await uiConfirm("Install this update? The files being replaced are saved first, then the manager restarts and this page reloads by itself.", { title: "Install update", confirmText: "Install and restart" }))) return;
  const post = force => fetch("/api/manager/update/apply", {
    method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ force })
  }).then(response => response.json());
  await withBusy(button, async () => {
    try {
      let data = await post(false);
      if (!data.ok && data.error?.includes("server is running")) {
        const proceed = await uiAsk({
          title: "A server is running",
          message: "Installing now is safe: the manager stops servers properly during its restart and starts them again afterwards.",
          confirmText: "Install anyway", danger: true
        });
        if (!proceed) return;
        data = await post(true);
      }
      if (!data.ok) throw new Error(data.error || "Update failed");
      showToast(`Installed ${data.ref}`, "success");
      document.getElementById("update-installed").textContent = `${data.short} (restart pending)`;
      if (data.can_restart) await restartManager();
      else showToast("Close the manager's terminal window and start it again to use the update.", "warning");
      loadManagerUpdate();
    } catch (error) {
      showToast(error.message, "error");
    }
  });
}

async function rollbackManagerUpdate() {
  if (!(await uiConfirm("Restore the manager files from the last snapshot? The manager restarts afterwards.", { title: "Roll back", confirmText: "Roll back", danger: true }))) return;
  try {
    const data = await requestJson("/api/manager/update/rollback", { method: "POST" });
    if (!data.ok) throw new Error(data.error || "Rollback failed");
    showToast(`Restored ${data.files.length} files`, "success");
    if (data.can_restart) await restartManager();
    else showToast("Close the manager's terminal window and start it again.", "warning");
    loadManagerUpdate();
  } catch (error) {
    showToast(error.message, "error");
  }
}

// ----- account -----

function initAccountUi() {
  const user = document.body.dataset.user || "";
  const role = document.body.dataset.role || "staff";
  const avatar = document.getElementById("user-avatar");
  if (avatar) avatar.textContent = (user[0] || "?").toUpperCase();
  const roleLabel = document.getElementById("user-role");
  if (roleLabel) roleLabel.textContent = role === "owner" ? "Owner" : "Staff";
  const badge = document.getElementById("account-role-badge");
  if (badge) badge.textContent = role === "owner" ? "OWNER" : "STAFF";
}

async function signOut() {
  try { await fetch("/api/auth/logout", { method: "POST" }); } catch {}
  window.location.reload();
}

async function changeOwnPassword(event) {
  event.preventDefault();
  const form = event.target;
  const submit = form.querySelector("button[type=submit]");
  await withBusy(submit, async () => {
    try {
      const data = await requestJson("/api/auth/password", {
        method: "POST", headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ current: document.getElementById("pw-current").value, new: document.getElementById("pw-new").value })
      });
      if (!data.ok) throw new Error(data.error || "Could not change the password");
      form.reset();
      showToast("Password changed", "success");
    } catch (error) {
      showToast(error.message, "error");
    }
  });
}

// ----- two-factor -----

async function loadTwoFactor() {
  try {
    const data = await requestJson("/api/auth/status");
    const enabled = Boolean(data.user?.totp_enabled);
    document.getElementById("twofa-badge").textContent = enabled ? "On" : "Off";
    document.getElementById("twofa-badge").className = `badge ${enabled ? "online" : "offline"}`;
    document.getElementById("twofa-off").hidden = enabled;
    document.getElementById("twofa-on").hidden = !enabled;
    if (enabled) document.getElementById("twofa-recovery-left").textContent = `${data.user.recovery_left} recovery code(s) left. Turning two-factor off needs your password and a code.`;
    if (!enabled) document.getElementById("twofa-setup").hidden = true;
  } catch { /* shown again next time the tab opens */ }
}

async function beginTwoFactor() {
  try {
    const data = await requestJson("/api/auth/2fa/begin", { method: "POST" });
    if (!data.ok) throw new Error(data.error);
    document.getElementById("twofa-secret").textContent = data.secret.match(/.{1,4}/g).join(" ");
    document.getElementById("twofa-off").hidden = true;
    document.getElementById("twofa-setup").hidden = false;
    document.getElementById("twofa-code").focus();
  } catch (error) {
    showToast(error.message, "error");
  }
}

function cancelTwoFactor() {
  document.getElementById("twofa-setup").hidden = true;
  document.getElementById("twofa-off").hidden = false;
}

async function enableTwoFactor() {
  try {
    const data = await requestJson("/api/auth/2fa/enable", {
      method: "POST", headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ code: document.getElementById("twofa-code").value })
    });
    if (!data.ok) throw new Error(data.error);
    document.getElementById("twofa-code").value = "";
    document.getElementById("twofa-setup").hidden = true;
    document.getElementById("twofa-codes-list").textContent = data.recovery_codes.join("\n");
    document.getElementById("twofa-codes").hidden = false;
    showToast("Two-factor sign-in is on", "success");
    loadTwoFactor();
  } catch (error) {
    showToast(error.message, "error");
  }
}

async function disableTwoFactor() {
  try {
    const data = await requestJson("/api/auth/2fa/disable", {
      method: "POST", headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ password: document.getElementById("twofa-disable-password").value, code: document.getElementById("twofa-disable-code").value })
    });
    if (!data.ok) throw new Error(data.error);
    document.getElementById("twofa-disable-password").value = "";
    document.getElementById("twofa-disable-code").value = "";
    document.getElementById("twofa-codes").hidden = true;
    showToast("Two-factor sign-in is off", "success");
    loadTwoFactor();
  } catch (error) {
    showToast(error.message, "error");
  }
}

// ----- Java -----

let javaPollTimer = null;

async function loadJava() {
  try {
    const data = await requestJson("/api/java");
    const badge = document.getElementById("java-badge");
    const summary = document.getElementById("java-summary");
    const install = document.getElementById("btn-java-install");
    const log = document.getElementById("java-log");
    const running = data.install.state === "running";
    if (data.found) {
      badge.textContent = `Java ${data.major}`;
      badge.className = `badge ${data.major >= 21 ? "online" : "type"}`;
      summary.textContent = `Using ${data.path}`;
    } else {
      badge.textContent = "Not found";
      badge.className = "badge offline";
      summary.textContent = "No Java was found on this PC, so servers cannot start.";
    }
    install.hidden = !IS_OWNER || !data.can_install || (data.found && data.major >= data.target);
    install.textContent = running ? "Installing..." : `Install Java ${data.target}`;
    install.disabled = running;
    log.hidden = !data.install.log.length;
    log.textContent = data.install.log.join("\n");
    clearTimeout(javaPollTimer);
    if (running) javaPollTimer = setTimeout(loadJava, 2500);
  } catch { /* shown again next time the tab opens */ }
}

async function installJava() {
  if (!(await uiConfirm("Install Java 21 (Eclipse Temurin) on this PC with winget? Windows may show a permission prompt.", { title: "Install Java", confirmText: "Install", always: true }))) return;
  try {
    const data = await requestJson("/api/java/install", { method: "POST" });
    showToast(data.message, data.ok ? "success" : "error");
  } catch (error) {
    showToast(error.message, "error");
  }
  loadJava();
}

// ----- Discord notifications -----

async function loadNotifications() {
  if (!IS_OWNER) return;
  try {
    const data = await requestJson("/api/notifications");
    const badge = document.getElementById("notify-badge");
    badge.textContent = data.configured ? "Connected" : "Not set";
    badge.className = `badge ${data.configured ? "online" : "offline"}`;
    document.getElementById("notify-hint").textContent = data.configured ? `A webhook ending ${data.hint} is saved. Paste a new URL to replace it.` : "No webhook saved yet.";
    document.getElementById("notify-events").innerHTML = data.events.map(event =>
      `<label class="toggle-card"><input type="checkbox" data-event="${escapeHtml(event.id)}" ${event.enabled ? "checked" : ""} onchange="saveNotificationEvents()" /><span><strong>${escapeHtml(event.label)}</strong></span></label>`).join("");
  } catch { /* owner-only; ignore otherwise */ }
}

async function saveNotifications() {
  const field = document.getElementById("notify-webhook");
  if (!field.value.trim()) { showToast("Paste a webhook URL first", "warning"); return; }
  try {
    const data = await requestJson("/api/notifications", {
      method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ webhook: field.value.trim() })
    });
    if (!data.ok) throw new Error(data.error);
    field.value = "";
    showToast("Webhook saved", "success");
    loadNotifications();
  } catch (error) {
    showToast(error.message, "error");
  }
}

async function saveNotificationEvents() {
  const events = {};
  document.querySelectorAll("#notify-events input[data-event]").forEach(box => { events[box.dataset.event] = box.checked; });
  try {
    await requestJson("/api/notifications", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ events }) });
  } catch (error) {
    showToast(error.message, "error");
  }
}

async function testNotification() {
  try {
    const data = await requestJson("/api/notifications/test", { method: "POST" });
    showToast(data.ok ? "Test message sent. Check your Discord channel." : (data.error || "Could not send"), data.ok ? "success" : "error");
  } catch (error) {
    showToast(error.message, "error");
  }
}

async function removeWebhook() {
  if (!(await uiConfirm("Remove the saved webhook? Notifications stop until you add another.", { title: "Remove webhook", confirmText: "Remove", danger: true, always: true }))) return;
  try {
    await requestJson("/api/notifications", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ webhook: "" }) });
    showToast("Webhook removed", "success");
    loadNotifications();
  } catch (error) {
    showToast(error.message, "error");
  }
}

// ----- diagnostics, log, sign out -----

let lastDiagnostics = "";

function formatMb(mb) {
  return mb == null ? "unknown" : mb >= 1024 ? `${(mb / 1024).toFixed(1)} GB` : `${mb} MB`;
}

async function loadDiagnostics() {
  if (!IS_OWNER) return;
  const box = document.getElementById("diagnostics");
  try {
    const [info, disk] = await Promise.all([requestJson("/api/diagnostics"), requestJson("/api/disk")]);
    const rows = [
      ["Version", info.version], ["Python", info.python], ["System", info.platform], ["Data folder", info.data_dir],
      ["Port", info.port], ["Mode", info.dev_mode ? "development (debug on)" : "normal"],
      ["Can restart itself", info.can_restart ? "yes" : "no"], ["Uptime", formatUptime(info.uptime)],
      ["Servers", `${info.servers} (${info.running} running)`],
      ["Java", info.java.found ? `${info.java.major} at ${info.java.path}` : "not found"],
      ["Installed commit", info.update.installed || "unknown"], ["Update channel", info.update.channel],
      ["Discord", info.notifications ? "connected" : "not set"], ["Live stats (psutil)", info.psutil ? "available" : "missing"],
      ["Disk free", `${formatMb(info.free_mb)} of ${formatMb(info.total_mb)}${disk.low ? " (LOW)" : ""}`],
      ["Log size", formatBytes(info.log_bytes)]
    ];
    const servers = Object.entries(disk.servers).map(([id, mb]) => `${knownServers.find(s => s.id === id)?.name || id}: ${formatMb(mb)} + ${formatMb(disk.backups[id] || 0)} backups`);
    lastDiagnostics = rows.map(([k, v]) => `${k}: ${v}`).join("\n") + (servers.length ? `\nStorage:\n  ${servers.join("\n  ")}` : "");
    box.innerHTML = rows.map(([k, v]) => `<div class="diag-row"><span>${escapeHtml(k)}</span><strong>${escapeHtml(String(v))}</strong></div>`).join("")
      + (servers.length ? `<div class="diag-row"><span>Storage</span><strong>${servers.map(escapeHtml).join("<br />")}</strong></div>` : "");
  } catch (error) {
    box.innerHTML = `<div class="empty">${escapeHtml(error.message)}</div>`;
  }
}

function copyDiagnostics() {
  if (lastDiagnostics) copyText(lastDiagnostics);
}

async function loadManagerLog() {
  if (!IS_OWNER) return;
  const box = document.getElementById("manager-log");
  try {
    const data = await requestJson("/api/manager/log?lines=200");
    box.textContent = data.lines.length ? data.lines.join("\n") : "Nothing logged yet.";
    box.scrollTop = box.scrollHeight;
  } catch (error) {
    box.textContent = error.message;
  }
}

async function signOutEverywhere() {
  if (!(await uiConfirm("Sign out every other browser that is signed in as you? This one stays signed in.", { title: "Sign out everywhere", confirmText: "Sign out others", always: true }))) return;
  try {
    const data = await requestJson("/api/auth/signout-all", { method: "POST" });
    if (!data.ok) throw new Error(data.error);
    showToast("Other sessions ended", "success");
  } catch (error) {
    showToast(error.message, "error");
  }
}

// ----- CurseForge key -----

async function loadCurseForge() {
  if (!IS_OWNER) return;
  try {
    const data = await requestJson("/api/curseforge/key");
    document.getElementById("cf-badge").textContent = data.configured ? "Connected" : "Not set";
    document.getElementById("cf-badge").className = `badge ${data.configured ? "online" : "offline"}`;
    document.getElementById("cf-hint").textContent = data.configured ? `A key ending ${data.hint.replace("...", "")} is saved. Paste a new key to replace it.` : "No key saved yet.";
  } catch { /* owner-only */ }
}

async function saveCurseForgeKey(button) {
  const field = document.getElementById("cf-key");
  if (!field.value.trim()) { showToast("Paste your CurseForge API key first", "warning"); return; }
  await withBusy(button, async () => {
    try {
      const data = await requestJson("/api/curseforge/key", {
        method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ key: field.value.trim() })
      });
      if (!data.ok) throw new Error(data.error);
      field.value = "";
      showToast("CurseForge connected", "success");
      loadCurseForge();
    } catch (error) {
      showToast(error.message, "error");
    }
  });
}

async function removeCurseForgeKey() {
  if (!(await uiConfirm("Remove the saved CurseForge key? CurseForge modpacks stop appearing until you add another.", { title: "Remove key", confirmText: "Remove", danger: true, always: true }))) return;
  try {
    await requestJson("/api/curseforge/key", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ key: "" }) });
    showToast("Key removed", "success");
    loadCurseForge();
  } catch (error) {
    showToast(error.message, "error");
  }
}

function loadSettingsTab() {
  loadCurseForge();
  loadManagerUpdate();
  loadTwoFactor();
  loadJava();
  loadNotifications();
  loadDiagnostics();
  loadManagerLog();
}
