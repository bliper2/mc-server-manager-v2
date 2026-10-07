// The Anti-Cheat tab: GrimAC status and install, live alerts, Discord relay, settings, punishments, files and commands.

let acConfig = {};
let grimData = null;
let grimFilePath = "";
let grimAlertTimer = null;

const jsonPost = body => ({ method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body) });
const stripFormatting = text => String(text).replace(/\x1b\[[0-9;]*[A-Za-z]|§./g, "");

async function loadAntiCheat() {
  if (!currentServerId) return;
  const id = currentServerId;
  try {
    const [policy, grim] = await Promise.all([requestJson(`/api/server/${id}/anticheat`), requestJson(`/api/server/${id}/grim`)]);
    if (id !== currentServerId) return;
    acConfig = policy.config || {};
    grimData = grim;
    document.getElementById("ac-discord-relay").checked = Boolean(acConfig.discord_relay);
    document.getElementById("ac-threshold").value = acConfig.threshold || 5;
    renderGrimStatus(grim.status);
    const live = grim.status.installed;
    document.getElementById("grim-live").hidden = !live;
    if (live) {
      renderGrimSettings(grim.settings);
      renderGrimPunishments(grim.punishments);
      renderGrimFiles(grim.status);
      renderGrimCommands(grim.commands);
      loadGrimAlerts();
    }
  } catch (error) {
    document.getElementById("grim-status-body").innerHTML = `<div class="notice warn">${escapeHtml(error.message)}</div>`;
  }
}

function renderGrimStatus(status) {
  const badge = document.getElementById("grim-badge");
  const body = document.getElementById("grim-status-body");
  const notes = [];
  if (status.others.length) notes.push(`<div class="notice warn"><span><strong>Another anti-cheat is installed: ${escapeHtml(status.others.join(", "))}.</strong> Two anti-cheats on one server fight each other and cause false flags. Keep only one.</span></div>`);
  if (status.wrong_builds.length) notes.push(`<div class="notice warn"><span><strong>${escapeHtml(status.wrong_builds.join(", "))}</strong> is a build for another loader, so this server ignores it. Use the Bukkit/Paper build.</span></div>`);
  if (!status.supported) {
    badge.textContent = "Not supported";
    badge.className = "badge offline";
    body.innerHTML = `<p class="hint">GrimAC is a plugin, and ${escapeHtml(status.type || "this server type")} servers do not run plugins. Use Paper, Purpur, Spigot or Folia to get it.</p>${notes.join("")}`;
  } else if (!status.installed) {
    badge.textContent = "Not installed";
    badge.className = "badge offline";
    body.innerHTML = `${notes.join("")}<p class="hint">GrimAC is a free anti-cheat that predicts how a player can move and flags anything impossible: fly, speed, reach, no-fall, timer and more. It works with Paper, Purpur, Spigot and Folia.</p>
      <button class="btn primary" data-perm="files" id="btn-install-grim" onclick="installGrim()">Install GrimAC</button>`;
  } else {
    badge.textContent = status.enabled ? `GrimAC ${status.version || ""}`.trim() : "Disabled";
    badge.className = `badge ${status.enabled ? "online" : "offline"}`;
    const lines = [`<strong>${escapeHtml(status.file)}</strong>${status.version ? ` · version ${escapeHtml(status.version)}` : ""}`];
    if (!status.enabled) lines.push('It is switched off, so the server does not load it. <button class="btn small" data-perm="files" onclick="enableGrim()">Switch it on</button> (restart to apply).');
    else if (!status.folder) lines.push("Start the server once. Grim creates its configuration files on the first start, and the settings appear here after that.");
    else if (!status.running) lines.push("The server is stopped, so the alert list shows what the last run logged.");
    body.innerHTML = `${notes.join("")}<p class="hint">${lines.join("<br>")}</p>`;
  }
}

async function installGrim() {
  const button = document.getElementById("btn-install-grim");
  if (button) button.disabled = true;
  await installProject("grimac", "GrimAC", "plugin", currentServerId);
  loadAntiCheat();
}

async function enableGrim() {
  await togglePlugin("plugins", grimData.status.file, true);
  loadAntiCheat();
}

// ---- Discord relay (kept with the older profile fields so nothing already saved is lost)
async function saveAntiCheat() {
  const payload = { ...acConfig, discord_relay: document.getElementById("ac-discord-relay").checked, threshold: document.getElementById("ac-threshold").value };
  try {
    const data = await requestJson(`/api/server/${currentServerId}/anticheat`, jsonPost(payload));
    if (data.config) acConfig = data.config;
    showToast(data.ok ? "Anti-cheat alert settings saved" : (data.error || "Save failed"), data.ok ? "success" : "error");
  } catch (error) {
    showToast(error.message, "error");
  }
}

// ---- Alerts
async function loadGrimAlerts() {
  if (!currentServerId || document.getElementById("grim-live").hidden) return;
  const player = document.getElementById("grim-alert-filter").value.trim();
  try {
    const data = await requestJson(`/api/server/${currentServerId}/grim/alerts?limit=120${player ? `&player=${encodeURIComponent(player)}` : ""}`);
    document.getElementById("grim-alert-total").textContent = `${data.total} alert${data.total === 1 ? "" : "s"}`;
    const player_chip = item => `<button class="grim-chip" onclick="filterGrimAlerts(${jsArg(item.name)})">${escapeHtml(item.name)} <b>${item.alerts} · max ${item.worst}</b></button>`;
    const check_chip = item => `<span class="grim-chip static">${escapeHtml(item.name)} <b>${item.alerts}</b></span>`;
    document.getElementById("grim-top-players").innerHTML = data.players.map(player_chip).join("") || '<span class="hint">None yet</span>';
    document.getElementById("grim-top-checks").innerHTML = data.checks.map(check_chip).join("") || '<span class="hint">None yet</span>';
    document.getElementById("grim-alert-list").innerHTML = data.alerts.length
      ? data.alerts.map(alert => `<div class="grim-alert"><time>${escapeHtml(alert.time.slice(11))}</time><button class="grim-player" onclick="filterGrimAlerts(${jsArg(alert.player)})">${escapeHtml(alert.player)}</button><span class="grim-check">${escapeHtml(alert.check)}${alert.exp ? "*" : ""}</span><span class="grim-vl${alert.vl >= (acConfig.threshold || 5) ? " high" : ""}">x${alert.vl}</span><span class="grim-info">${escapeHtml(alert.info)}</span></div>`).join("")
      : `<div class="empty">${player ? "No alerts for this player" : "No alerts yet"}</div>`;
  } catch { /* the server may have been deleted while this was refreshing */ }
}

function filterGrimAlerts(player) {
  document.getElementById("grim-alert-filter").value = player || "";
  loadGrimAlerts();
}

function startGrimAlertTimer() {
  if (grimAlertTimer) return;
  grimAlertTimer = setInterval(() => {
    const active = document.getElementById("dtab-anticheat")?.classList.contains("active");
    if (active && !document.hidden && document.getElementById("grim-alert-auto")?.checked) loadGrimAlerts();
  }, 5000);
}

// ---- Settings
function renderGrimSettings(settings) {
  const groups = {};
  settings.forEach(setting => (groups[setting.group] ||= []).push(setting));
  document.getElementById("grim-settings").innerHTML = Object.entries(groups).map(([group, items]) => `<div class="grim-group"><h4>${escapeHtml(group)}</h4>${items.map(setting => {
    const help = setting.help ? `<small>${escapeHtml(setting.help)}</small>` : "";
    const id = `grim-set-${setting.id}`;
    if (setting.type === "bool") return `<label class="toggle-card"><input type="checkbox" id="${id}" data-setting="${setting.id}" data-original="${setting.value}" ${setting.value ? "checked" : ""} data-perm="files" /><span><strong>${escapeHtml(setting.label)}</strong>${help}</span></label>`;
    if (setting.type === "secret") return `<div class="form-group"><label for="${id}">${escapeHtml(setting.label)}</label><input type="password" id="${id}" data-setting="${setting.id}" data-original="" autocomplete="off" placeholder="${setting.set ? "Saved. Type a new one to replace it" : "https://discord.com/api/webhooks/..."}" data-perm="files" />${setting.set ? `<button class="btn danger small" data-perm="files" onclick="clearGrimSecret(${jsArg(setting.id)})">Remove the saved webhook</button>` : ""}${help}</div>`;
    const kind = setting.type === "int" ? `type="number" min="${setting.min ?? ""}" max="${setting.max ?? ""}"` : 'type="text"';
    return `<div class="form-group"><label for="${id}">${escapeHtml(setting.label)}</label><input ${kind} id="${id}" data-setting="${setting.id}" data-original="${escapeHtml(String(setting.value ?? ""))}" value="${escapeHtml(String(setting.value ?? ""))}" data-perm="files" />${help}</div>`;
  }).join("")}</div>`).join("") || '<div class="empty">No settings found. Start the server once so Grim creates its files.</div>';
}

function changedGrimSettings() {
  const values = {};
  document.querySelectorAll("#grim-settings [data-setting]").forEach(input => {
    const current = input.type === "checkbox" ? String(input.checked) : input.value;
    if (current === input.dataset.original) return;
    if (input.type === "password" && !current) return;
    values[input.dataset.setting] = input.type === "checkbox" ? input.checked : (input.type === "number" ? Number(current) : current);
  });
  return values;
}

async function saveGrimSettings() {
  const values = changedGrimSettings();
  if (!Object.keys(values).length) { showToast("Nothing changed", "warning"); return; }
  try {
    const data = await requestJson(`/api/server/${currentServerId}/grim/settings`, jsonPost({ values }));
    showToast(`Saved ${data.changed.length} setting${data.changed.length === 1 ? "" : "s"}`, "success");
    if (grimData?.status.running && document.getElementById("grim-reload-after").checked) {
      try { await requestJson(`/api/server/${currentServerId}/command`, jsonPost({ command: "grim reload" })); } catch { /* reload needs the console permission */ }
    }
    renderGrimSettings(data.settings);
  } catch (error) {
    showToast(error.message, "error");
  }
}

async function clearGrimSecret(id) {
  if (!(await uiConfirm("Remove the saved Discord webhook from Grim's discord.yml?", { title: "Remove webhook", confirmText: "Remove", danger: true, always: true }))) return;
  try {
    const data = await requestJson(`/api/server/${currentServerId}/grim/settings`, jsonPost({ values: { [id]: "" } }));
    showToast("Webhook removed", "success");
    renderGrimSettings(data.settings);
  } catch (error) {
    showToast(error.message, "error");
  }
}

// ---- Punishments
function renderGrimPunishments(groups) {
  document.getElementById("grim-punishments").innerHTML = groups.length ? groups.map(group => `<div class="grim-card">
      <strong>${escapeHtml(group.name)}</strong>
      <div class="grim-chips">${group.checks.map(check => `<span class="grim-chip static">${escapeHtml(check)}</span>`).join("") || '<span class="hint">No checks listed</span>'}</div>
      <small>Violations expire after ${group.remove_after ?? "?"} seconds</small>
      ${group.rules.map(rule => `<div class="grim-rule"><span>at ${rule.threshold}${rule.interval ? `, then every ${rule.interval}` : ""}</span><code>${escapeHtml(rule.action)}</code></div>`).join("") || '<small class="hint">No actions</small>'}
    </div>`).join("") : '<div class="empty">punishments.yml was not found or has no groups yet.</div>';
}

// ---- Files
function renderGrimFiles(status) {
  document.getElementById("grim-files").innerHTML = Object.entries(status.files).filter(([, present]) => present)
    .map(([name]) => `<button class="btn small" onclick="openGrimFile(${jsArg(name)})">${escapeHtml(name)}</button>`).join("") || '<span class="hint">No files yet. Start the server once.</span>';
}

async function openGrimFile(name) {
  const path = `plugins/GrimAC/${name}`;
  try {
    const data = await requestJson(`/api/server/${currentServerId}/fs/read?path=${encodeURIComponent(path)}`);
    if (!data.ok) throw new Error(data.error || "Could not read the file");
    grimFilePath = path;
    document.getElementById("grim-file-name").textContent = path;
    document.getElementById("grim-file-content").value = data.content;
    const editor = document.getElementById("grim-file-editor");
    editor.hidden = false;
    editor.scrollIntoView({ behavior: "smooth", block: "nearest" });
  } catch (error) {
    showToast(error.message, "error");
  }
}

async function saveGrimFile() {
  if (!grimFilePath) return;
  try {
    const data = await requestJson(`/api/server/${currentServerId}/fs/write`, jsonPost({ path: grimFilePath, content: document.getElementById("grim-file-content").value }));
    showToast(data.ok ? "Saved. Run /grim reload to apply it." : (data.error || "Save failed"), data.ok ? "success" : "error");
    if (data.ok) loadAntiCheat();
  } catch (error) {
    showToast(error.message, "error");
  }
}

// ---- Commands
function renderGrimCommands(commands) {
  const select = document.getElementById("grim-command");
  const keep = select.value;
  select.innerHTML = commands.map(item => `<option value="${escapeHtml(item.command)}" data-player="${item.player}" data-message="${Boolean(item.message)}" data-game="${item.game_only}">${escapeHtml(item.label)}${item.game_only ? " (in game)" : ""}</option>`).join("");
  if (keep) select.value = keep;
  updateGrimPreview();
}

function grimCommandText() {
  const select = document.getElementById("grim-command");
  const target = document.getElementById("grim-target").value.trim();
  return select.value.replaceAll("{player}", target).replaceAll("{message}", target);
}

function updateGrimPreview() {
  const option = document.getElementById("grim-command").selectedOptions[0];
  const needs = option && (option.dataset.player === "true" || option.dataset.message === "true");
  document.getElementById("grim-target").parentElement.hidden = !needs;
  document.getElementById("grim-target-label").textContent = option?.dataset.message === "true" ? "Message" : "Player";
  document.getElementById("grim-preview").textContent = `/${grimCommandText()}`;
}

async function sendGrimCommand() {
  const option = document.getElementById("grim-command").selectedOptions[0];
  if (!option) return;
  if (option.dataset.game === "true") { showToast("That command only works for a player in the game. Run it from the game chat.", "warning"); return; }
  if ((option.dataset.player === "true" || option.dataset.message === "true") && !document.getElementById("grim-target").value.trim()) { showToast("Fill in the player or message first", "warning"); return; }
  const command = grimCommandText();
  const output = document.getElementById("grim-output");
  try {
    const before = (await requestJson(`/api/server/${currentServerId}/console?since=0`)).total;
    const sent = await requestJson(`/api/server/${currentServerId}/command`, jsonPost({ command }));
    if (!sent.ok) throw new Error(sent.message || sent.error || "Command failed");
    output.hidden = false;
    output.textContent = "Waiting for the server...";
    await new Promise(resolve => setTimeout(resolve, 1500));
    const after = await requestJson(`/api/server/${currentServerId}/console?since=0`);
    const lines = after.lines.slice(Math.max(0, before - (after.total - after.lines.length))).map(stripFormatting);
    output.textContent = lines.join("\n").trim() || "The server printed nothing.";
  } catch (error) {
    showToast(error.message, "error");
  }
}

startGrimAlertTimer();
