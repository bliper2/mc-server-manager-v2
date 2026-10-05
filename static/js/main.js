// Start-up: event wiring that needs every other file to be loaded first.

document.querySelectorAll(".nav-btn").forEach(btn => {
  btn.addEventListener("click", () => switchTab(btn.dataset.tab));
});

document.getElementById("server-filter").addEventListener("click", event => {
  const btn = event.target.closest(".filter-pill");
  if (!btn) return;
  serverFilter = btn.dataset.filter;
  document.querySelectorAll("#server-filter .filter-pill").forEach(p => p.classList.toggle("active", p === btn));
  serversSettled = false;
  loadServers();
});

document.getElementById("servers-list").addEventListener("keydown", event => {
  if ((event.key === "Enter" || event.key === " ") && event.target.classList?.contains("server-card")) {
    event.preventDefault();
    event.target.click();
  }
});

document.getElementById("create-type").addEventListener("change", loadVersions);

document.getElementById("create-name").addEventListener("input", updateCreateLogo);

document.getElementById("create-max-players").addEventListener("input", event => {
  document.getElementById("max-players-display").textContent = event.target.value;
});

document.getElementById("create-port").addEventListener("input", event => {
  const port = Number(event.target.value);
  const blank = event.target.value === "";
  const valid = port >= 1024 && port <= 65535;
  event.target.setCustomValidity(valid ? "" : "Use a port between 1024 and 65535");
  event.target.classList.toggle("invalid", !valid && !blank);
  event.target.setAttribute("aria-invalid", valid || blank ? "false" : "true");
  portHint.classList.toggle("hint-warn", !valid && !blank);
  portHint.textContent = valid || blank ? portHintText : `${event.target.value} is outside 1024–65535`;
  updateCreateReview();
});

["create-name", "create-type", "create-version", "create-max-players"].forEach(id => {
  document.getElementById(id).addEventListener("input", updateCreateReview);
});

document.getElementById("import-folder").addEventListener("change", event => {
  const label = document.querySelector(".import-folder-label");
  const count = event.target.files?.length || 0;
  if (label) label.childNodes[0].textContent = count ? `${count} files selected` : "Choose folder";
});

document.getElementById("create-ram-exact").addEventListener("input", event => setCreateRam(event.target.value, "exact"));

document.getElementById("create-ram-exact").addEventListener("blur", () => setCreateRam(currentCreateRam(), "blur"));

document.getElementById("ram-presets").addEventListener("click", event => {
  const preset = event.target.closest(".ram-preset");
  if (preset) setCreateRam(preset.dataset.ram, "preset");
});

document.querySelectorAll(".dtab").forEach(btn => {
  btn.addEventListener("click", () => {
    document.querySelectorAll(".dtab").forEach(d => d.classList.remove("active"));
    document.querySelectorAll(".dtab-panel").forEach(p => p.classList.remove("active"));
    btn.classList.add("active");
    document.getElementById("dtab-" + btn.dataset.dtab).classList.add("active");
    if (btn.dataset.dtab === "players") loadPlayers();
    if (btn.dataset.dtab === "commands") loadCommandWiki();
    if (btn.dataset.dtab === "files") loadFs();
    if (btn.dataset.dtab === "props") loadProps();
    if (btn.dataset.dtab === "backups") loadBackups();
    if (btn.dataset.dtab === "anticheat") loadAntiCheat();
    if (btn.dataset.dtab === "plugins") { loadPluginMods(); loadPluginConfigs(); loadAutoUpdate(); }
    if (btn.dataset.dtab === "automation") loadAutomation();
    if (btn.dataset.dtab === "activity") loadActivity();
  });
});

document.getElementById("featured-plugins").addEventListener("scroll", e => {
  if (nearBottom(e.target)) loadMoreFeatured("plugin");
});

document.getElementById("featured-mods").addEventListener("scroll", e => {
  if (nearBottom(e.target)) loadMoreFeatured("mod");
});

window.addEventListener("scroll", () => {
  if (!document.getElementById("tab-browser")?.classList.contains("active")) return;
  if (window.innerHeight + window.scrollY >= document.body.offsetHeight - 300) loadMoreSearch();
});

document.getElementById("search-q").addEventListener("keydown", e => { if (e.key === "Enter") searchModrinth(); });

document.addEventListener("pointerdown", event => {
  if (!(event.target instanceof Element) || document.body.classList.contains("reduced-motion")) return;
  const el = event.target.closest(RIPPLE_SELECTOR);
  if (!el || el.disabled) return;
  const box = el.getBoundingClientRect();
  el.style.setProperty("--rx", `${event.clientX - box.left}px`);
  el.style.setProperty("--ry", `${event.clientY - box.top}px`);
  el.classList.remove("rippling");
  void el.offsetWidth;
  el.classList.add("rippling");
  setTimeout(() => el.classList.remove("rippling"), 650);
});

document.addEventListener("pointermove", event => {
  if (spotlightFrame || !(event.target instanceof Element)) return;
  const card = event.target.closest(SPOTLIGHT_SELECTOR);
  if (!card) return;
  const { clientX, clientY } = event;
  spotlightFrame = requestAnimationFrame(() => {
    spotlightFrame = null;
    const box = card.getBoundingClientRect();
    card.style.setProperty("--mx", `${clientX - box.left}px`);
    card.style.setProperty("--my", `${clientY - box.top}px`);
  });
}, { passive: true });

// Init
initAccountUi();
initSettings();
initConsoleControls();
loadHostMemory();
tickLiveClock();
setInterval(tickLiveClock, 1000);
loadServers();
loadVersions();
pollStats();
setInterval(pollStats, 3000);
startBootWatcher();
restoreView();
