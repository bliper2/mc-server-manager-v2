// Keeps this page in step with the running manager: reloads itself when the manager restarts or (in dev mode) its
// files change, shows a reconnect notice if the manager is unreachable, and drives "restart the manager".

const BOOT_POLL_MS = 2000;
let bootState = null;
let bootFailures = 0;
let restartingSince = 0;
let bootTimer = null;

function setOverlay(visible, title, text) {
  const overlay = document.getElementById("boot-overlay");
  if (!overlay) return;
  overlay.hidden = !visible;
  if (title) document.getElementById("boot-title").textContent = title;
  if (text) document.getElementById("boot-text").textContent = text;
}

function reloadForNewVersion() {
  setOverlay(true, "Updating", "Loading the new version...");
  setTimeout(() => window.location.reload(), 250);
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
  let info;
  try {
    const response = await nativeFetch("/api/boot", { cache: "no-store" });
    info = await response.json();
  } catch {
    bootFailures += 1;
    if (bootFailures >= 2 && !restartingSince) setOverlay(true, "Reconnecting", "The manager is not answering. Waiting for it to come back...");
    if (restartingSince && Date.now() - restartingSince > 120000) {
      setOverlay(true, "Taking longer than expected", "Look at the terminal window running the manager for an error message.");
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
  if (restarted || filesChanged) return reloadForNewVersion();
  if (!restartingSince) setOverlay(false);
  applyBootInfo(info);
}

function startBootWatcher() {
  pollBoot();
  bootTimer = setInterval(pollBoot, BOOT_POLL_MS);
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
    restartingSince = Date.now();
    setOverlay(true, "Restarting the manager", data.resumes
      ? "Stopping servers safely. They start again by themselves, and this page reloads when the manager is back."
      : "This page reloads by itself when the manager is back.");
    return true;
  } catch (error) {
    showToast(`Could not restart: ${error.message}`, "error");
    return false;
  }
}
