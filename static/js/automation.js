// Per-server automation (crash recovery, scheduled restart, announcements) and the per-server activity log.

function setSwitchBadge(id, on) {
  const badge = document.getElementById(id);
  badge.textContent = on ? "On" : "Off";
  badge.className = `badge ${on ? "online" : "offline"}`;
}

async function loadAutomation() {
  if (!currentServerId) return;
  try {
    const data = await requestJson(`/api/server/${currentServerId}/automation`);
    const { auto_restart: restart, schedule_restart: schedule, announcements } = data.automation;
    document.getElementById("ar-enabled").checked = restart.enabled;
    document.getElementById("ar-tries").value = restart.max_tries;
    document.getElementById("ar-window").value = restart.window_minutes;
    document.getElementById("sr-enabled").checked = schedule.enabled;
    document.getElementById("sr-time").value = schedule.time;
    document.getElementById("sr-warn").value = schedule.warn_minutes;
    document.getElementById("an-enabled").checked = announcements.enabled;
    document.getElementById("an-interval").value = announcements.interval_minutes;
    document.getElementById("an-messages").value = announcements.messages.join("\n");
    setSwitchBadge("auto-restart-badge", restart.enabled);
    setSwitchBadge("sched-badge", schedule.enabled);
    setSwitchBadge("ann-badge", announcements.enabled);
  } catch (error) {
    showToast(error.message, "error");
  }
}

async function saveAutomation(button) {
  if (!currentServerId) return;
  const payload = {
    auto_restart: {
      enabled: document.getElementById("ar-enabled").checked,
      max_tries: Number(document.getElementById("ar-tries").value),
      window_minutes: Number(document.getElementById("ar-window").value)
    },
    schedule_restart: {
      enabled: document.getElementById("sr-enabled").checked,
      time: document.getElementById("sr-time").value,
      warn_minutes: Number(document.getElementById("sr-warn").value)
    },
    announcements: {
      enabled: document.getElementById("an-enabled").checked,
      interval_minutes: Number(document.getElementById("an-interval").value),
      messages: document.getElementById("an-messages").value.split("\n").map(line => line.trim()).filter(Boolean)
    }
  };
  await withBusy(button, async () => {
    try {
      const data = await requestJson(`/api/server/${currentServerId}/automation`, {
        method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(payload)
      });
      if (!data.ok) throw new Error(data.error);
      showToast("Automation saved", "success");
      loadAutomation();
    } catch (error) {
      showToast(error.message, "error");
    }
  });
}

async function loadActivity() {
  if (!currentServerId) return;
  loadCrashReports();
  const list = document.getElementById("server-activity");
  try {
    const data = await requestJson(`/api/server/${currentServerId}/activity`);
    list.innerHTML = data.entries.length ? data.entries.map(entry => {
      const action = entry.action.replace(/^POST \/api\/server\/[^/]+\//, "").replace(/\//g, " ");
      const extra = entry.exit_code !== undefined ? `exit code ${entry.exit_code}` : (entry.status ? `HTTP ${entry.status}` : "");
      return `<div class="audit-row"><time>${new Date(entry.at).toLocaleString()}</time><strong>${escapeHtml(entry.user)}</strong><span>${escapeHtml(action)}</span><small>${escapeHtml(extra)}</small></div>`;
    }).join("") : '<div class="empty">Nothing has been done on this server yet</div>';
  } catch (error) {
    list.innerHTML = `<div class="empty">${escapeHtml(error.message)}</div>`;
  }
}

async function loadCrashReports() {
  if (!currentServerId) return;
  const list = document.getElementById("crash-list");
  try {
    const data = await requestJson(`/api/server/${currentServerId}/crash-reports`);
    list.innerHTML = data.reports.length ? data.reports.map(report => `
      <div class="audit-row"><time>${new Date(report.modified).toLocaleString()}</time><strong>${escapeHtml(report.name)}</strong><span>${formatBytes(report.size)}</span><small><button class="mini-btn" onclick="fsOpenPath(${jsArg(report.path)})">Open</button></small></div>`).join("") : '<div class="empty">No crash reports. That is good.</div>';
  } catch (error) {
    list.innerHTML = `<div class="empty">${escapeHtml(error.message)}</div>`;
  }
}
