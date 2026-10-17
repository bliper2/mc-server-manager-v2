// Keeps this page in step with the running manager: reloads itself when the manager restarts or (in dev mode) its
// files change, shows a reconnect notice if the manager is unreachable, and drives "restart the manager".

const BOOT_POLL_MS = 2000;
const BOOT_POLL_FAST_MS = 700;
const BOOT_GIVE_UP_MS = 150000;
let bootState = null;
let bootFailures = 0;
let restartingSince = 0;
let bootTimer = null;
let bootPolling = false;
let bootReloading = false;
let bootStartedAt = Date.now();

const BOOT_STEPS = {
  restart: ["Stopping servers safely", "Restarting the manager", "Loading the new version"],
  reconnect: ["Connection lost", "Waiting for the manager", "Loading the new version"]
};

function setOverlay(visible, title, text, step) {
  const overlay = document.getElementById("boot-overlay");
  if (!overlay) return;
  overlay.hidden = !visible;
  if (!visible) return;
  if (title) document.getElementById("boot-title").textContent = title;
  if (text) document.getElementById("boot-text").textContent = text;
  const list = document.getElementById("boot-steps");
  if (list && step !== undefined) {
    const labels = BOOT_STEPS[restartingSince ? "restart" : "reconnect"];
    list.innerHTML = labels.map((label, index) =>
      `<li class="${index < step ? "done" : index === step ? "active" : ""}">${label}</li>`).join("");
    list.hidden = false;
    const bar = document.getElementById("boot-bar");
    if (bar) bar.style.width = `${Math.min(100, Math.round(((step + .5) / labels.length) * 100))}%`;
  }
}

function userIsEditing() {
  const el = document.activeElement;
  if (!el) return false;
  if (el.isContentEditable) return true;
  if (el.tagName === "TEXTAREA") return !!el.value;
  if (el.tagName === "INPUT" && !["button", "checkbox", "radio", "range", "submit"].includes(el.type)) return !!el.value;
  return false;
}

async function waitUntilServed() {
  // /api/boot answers a moment before the page itself is ready (the first request compiles templates), so confirm
  // the real page loads before reloading into it. Otherwise the reload can land on a half-started manager.
  for (let attempt = 0; attempt < 30; attempt += 1) {
    try {
      const response = await nativeFetch("/", { cache: "no-store" });
      if (response.ok) return true;
    } catch { /* still starting */ }
    await new Promise(resolve => setTimeout(resolve, 500));
  }
  return false;
}

async function reloadForNewVersion() {
  if (bootReloading) return;
  bootReloading = true;
  setOverlay(true, "Updating", "Loading the new version...", 2);
  await waitUntilServed();
  setTimeout(() => window.location.reload(), 150);
}

function showReloadBanner() {
  const banner = document.getElementById("reload-banner");
  if (banner) banner.hidden = false;
}

function applyBootInfo(info) {
  const dev = document.getElementById("dev-banner");
  if (dev) dev.hidden = info.dev === null || info.dev === undefined;
  const banner = document.getElementById("update-banner");
  if (banner) {
    banner.hidden = !info.pending;
    document.getElementById("update-banner-text").textContent = info.can_restart
      ? "An update was installed. Restart the manager to start using it."
      : "An update was installed. Close the manager's terminal window and start it again to use it.";
    banner.querySelector("button").hidden = !info.can_restart || !IS_OWNER;
  }
  const restart = document.getElementById("btn-restart-manager");
  if (restart) restart.disabled = !info.can_restart;
  if (restart && !info.can_restart) restart.title = "Start the manager with start.bat or app.py to enable this";
}

async function pollBoot() {
  if (bootPolling || bootReloading) return;
  bootPolling = true;
  try {
    await pollBootOnce();
  } finally {
    bootPolling = false;
  }
}

async function pollBootOnce() {
  let info;
  try {
    const controller = new AbortController();
    const timer = setTimeout(() => controller.abort(), 4000);
    const response = await nativeFetch("/api/boot", { cache: "no-store", signal: controller.signal });
    clearTimeout(timer);
    info = await response.json();
  } catch {
    bootFailures += 1;
    if (bootFailures >= 2 || restartingSince) {
      if (restartingSince && Date.now() - restartingSince > BOOT_GIVE_UP_MS) {
        setOverlay(true, "Taking longer than expected", "Look at the terminal window running the manager for an error message.");
      } else if (restartingSince) {
        setOverlay(true, "Restarting the manager", "Servers are stopped. Waiting for the manager to come back...", 1);
      } else {
        setOverlay(true, "Reconnecting", "The manager is not answering. Waiting for it to come back...", 1);
      }
      scheduleBootPoll(BOOT_POLL_FAST_MS);
    }
    return;
  }
  bootFailures = 0;
  if (bootState === null) {
    bootState = info;
    applyBootInfo(info);
    return;
  }
  const restarted = info.boot !== bootState.boot;
  const filesChanged = info.dev !== null && info.dev !== undefined && info.dev !== bootState.dev;
  if (restarted) return reloadForNewVersion();
  if (filesChanged) {
    // Files changed under a live manager (dev mode). Never throw away something the user is typing.
    if (userIsEditing()) {
      showReloadBanner();
      bootState.dev = info.dev;
      return;
    }
    return reloadForNewVersion();
  }
  if (!restartingSince) setOverlay(false);
  applyBootInfo(info);
  scheduleBootPoll(restartingSince ? BOOT_POLL_FAST_MS : BOOT_POLL_MS);
}

function scheduleBootPoll(delay) {
  clearTimeout(bootTimer);
  bootTimer = setTimeout(pollBoot, delay);
}

function startBootWatcher() {
  pollBoot().then(() => scheduleBootPoll(BOOT_POLL_MS));
  document.addEventListener("visibilitychange", () => { if (!document.hidden) pollBoot(); });
  window.addEventListener("online", () => pollBoot());
  // Keep ticking even if a poll threw something unexpected.
  setInterval(() => { if (!bootPolling && !bootTimer) pollBoot(); }, BOOT_POLL_MS * 3);
}

// Called by the Settings page after /api/manager/update/apply or /rollback said the manager is restarting by itself.
function watchManagerRestart(resumes = 0) {
  restartingSince = Date.now();
  bootFailures = 0;
  setOverlay(true, "Restarting the manager", resumes
    ? "Stopping servers safely. They start again by themselves, and this page reloads when the manager is back."
    : "This page reloads by itself when the manager is back.", resumes ? 0 : 1);
  scheduleBootPoll(BOOT_POLL_FAST_MS);
}

async function restartManager() {
  if (!IS_OWNER) return false;
  const send = body => fetch("/api/manager/restart", {
    method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body)
  }).then(response => response.json());
  try {
    let data = await send({});
    if (data.needs_confirm) {
      const agreed = await uiAsk({
        title: "Restart the manager",
        message: `${data.servers.join(", ")} will be stopped safely and started again by themselves afterwards. Players are disconnected for about a minute.`,
        confirmText: "Restart", danger: true
      });
      if (!agreed) return false;
      data = await send({ stop_servers: true });
    }
    if (!data.ok) {
      showToast(data.error || "Could not restart", "error");
      return false;
    }
    watchManagerRestart(data.resumes);
    return true;
  } catch (error) {
    showToast(`Could not restart: ${error.message}`, "error");
    return false;
  }
}
