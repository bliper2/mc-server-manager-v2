// Ctrl+K palette: jump to a page or server, or run a common action.

let paletteItems = [];
let paletteIndex = 0;

function paletteCatalog() {
  const items = [
    { title: "Servers", hint: "Page", run: () => switchTab("servers") },
    { title: "Live map", hint: "Page", run: () => switchTab("map") },
    { title: "Plugins and mods", hint: "Page", run: () => switchTab("browser") },
    { title: "Settings", hint: "Page", run: () => switchTab("settings") },
    { title: "Keyboard shortcuts", hint: "Help", run: openShortcuts },
    { title: "Sign out", hint: "Account", run: signOut }
  ];
  if (can("manage")) items.splice(1, 0, { title: "Create a server", hint: "Page", run: () => switchTab("create") });
  if (IS_OWNER) {
    items.push({ title: "Staff panel", hint: "Page", run: () => switchTab("staff") });
    items.push({ title: "Check for manager updates", hint: "Action", run: () => { switchTab("settings"); checkManagerUpdate(); } });
    items.push({ title: "Restart the manager", hint: "Action", run: restartManager });
  }
  knownServers.forEach(server => items.push({
    title: server.name, hint: `${server.running ? "Running" : "Stopped"} · ${server.type} ${server.version}`, run: () => openServer(server.id)
  }));
  return items;
}

function renderPalette() {
  const list = document.getElementById("palette-list");
  list.innerHTML = paletteItems.length ? paletteItems.map((item, i) =>
    `<button type="button" role="option" class="palette-item${i === paletteIndex ? " active" : ""}" data-index="${i}"><strong>${escapeHtml(item.title)}</strong><span>${escapeHtml(item.hint)}</span></button>`).join("")
    : '<div class="empty">Nothing matches</div>';
  list.querySelector(".active")?.scrollIntoView({ block: "nearest" });
}

function filterPalette() {
  const query = document.getElementById("palette-input").value.trim().toLowerCase();
  const all = paletteCatalog();
  paletteItems = query
    ? all.filter(item => `${item.title} ${item.hint}`.toLowerCase().includes(query)).sort((a, b) => a.title.toLowerCase().indexOf(query) - b.title.toLowerCase().indexOf(query))
    : all;
  paletteIndex = 0;
  renderPalette();
}

function runPaletteItem(index) {
  const item = paletteItems[index];
  if (!item) return;
  document.getElementById("palette-dialog").close();
  item.run();
}

function openPalette() {
  const dialog = document.getElementById("palette-dialog");
  if (dialog.open) return;
  document.getElementById("palette-input").value = "";
  filterPalette();
  dialog.showModal();
  document.getElementById("palette-input").focus();
}

function initPalette() {
  const input = document.getElementById("palette-input");
  input.addEventListener("input", filterPalette);
  input.addEventListener("keydown", event => {
    if (event.key === "ArrowDown" || event.key === "ArrowUp") {
      event.preventDefault();
      if (!paletteItems.length) return;
      paletteIndex = (paletteIndex + (event.key === "ArrowDown" ? 1 : -1) + paletteItems.length) % paletteItems.length;
      renderPalette();
    } else if (event.key === "Enter") {
      event.preventDefault();
      runPaletteItem(paletteIndex);
    }
  });
  document.getElementById("palette-list").addEventListener("click", event => {
    const row = event.target.closest(".palette-item");
    if (row) runPaletteItem(Number(row.dataset.index));
  });
}
