// Logo library dialog: bundled artwork, images the owner imported, and Pinterest pins. The create form and the server page both open it.

const LOGO_FILTERS = [["all", "All"], ["mine", "My logos"], ["combat", "Combat"], ["royal", "Royal"], ["nature", "Nature"], ["build", "Build"], ["pixel", "Pixel art"]];
let logoLibrary = [];
let logoFilter = "all";
let logoPickHandler = null;

function setLogoStatus(message, ok = false) {
  const status = document.getElementById("logo-status");
  status.textContent = message;
  status.className = message ? `status-msg show ${ok ? "ok" : "err"}` : "status-msg";
}

async function openLogoPicker(onPick) {
  logoPickHandler = onPick;
  setLogoStatus("");
  document.getElementById("logo-search").value = "";
  document.getElementById("logo-dialog").showModal();
  try {
    await loadLogoLibrary();
  } catch (error) {
    setLogoStatus(error.message);
  }
}

function closeLogoPicker() {
  document.getElementById("logo-dialog").close();
}

async function loadLogoLibrary() {
  const data = await requestJson("/api/logos");
  logoLibrary = [...data.custom, ...data.bundled];
  renderLogoFilters();
  renderLogoGrid();
}

function renderLogoFilters() {
  const present = new Set(logoLibrary.map(logo => logo.category));
  document.getElementById("logo-filter").innerHTML = LOGO_FILTERS
    .filter(([key]) => key === "all" || key === "mine" || present.has(key))
    .map(([key, label]) => `<button type="button" class="filter-pill${key === logoFilter ? " active" : ""}" data-key="${key}">${label}</button>`)
    .join("");
}

function renderLogoGrid() {
  const grid = document.getElementById("logo-grid");
  const query = document.getElementById("logo-search").value.trim().toLowerCase();
  const shown = logoLibrary.filter(logo => (logoFilter === "all" || logo.category === logoFilter) && (!query || `${logo.name} ${logo.category}`.toLowerCase().includes(query)));
  if (!shown.length) {
    const empty = logoFilter === "mine" && !query ? "Nothing imported yet. Upload images or paste a Pinterest link below." : "No logos match.";
    grid.innerHTML = `<div class="logo-empty">${empty}</div>`;
    return;
  }
  grid.innerHTML = shown.map(logo => `<div class="logo-tile">
      <button type="button" class="logo-pick" role="option" title="${escapeHtml(logo.name)}" onclick="chooseLogo(${jsArg(logo.ref)})"><img src="${escapeHtml(logo.url)}" alt="${escapeHtml(logo.name)}" width="64" height="64" loading="lazy" /></button>
      ${logo.category === "mine" ? `<button type="button" class="logo-remove" data-perm="manage" title="Remove from the library" aria-label="Remove ${escapeHtml(logo.name)} from the library" onclick="removeLogo(${jsArg(logo.ref)})">✕</button>` : ""}
    </div>`).join("");
}

function chooseLogo(ref) {
  const logo = logoLibrary.find(item => item.ref === ref);
  const handler = logoPickHandler;
  closeLogoPicker();
  if (logo && handler) handler(logo);
}

async function removeLogo(ref) {
  const logo = logoLibrary.find(item => item.ref === ref);
  if (!logo || !await uiAsk({ title: "Remove this logo?", message: `"${logo.name}" is deleted from the logos folder. Servers already using it keep their copy.`, confirmText: "Remove", danger: true })) return;
  try {
    await requestJson(`/api/logos/custom/${encodeURIComponent(ref.split("/")[1])}`, { method: "DELETE" });
    await loadLogoLibrary();
  } catch (error) {
    setLogoStatus(error.message);
  }
}

async function uploadLogoFiles() {
  const input = document.getElementById("logo-files");
  if (!input.files.length) return;
  const form = new FormData();
  for (const file of input.files) form.append("files", file);
  setLogoStatus("Uploading...", true);
  try {
    const data = await requestJson("/api/logos/custom", { method: "POST", body: form });
    const skipped = data.skipped.length ? ` ${data.skipped.length} skipped (${data.skipped[0]}).` : "";
    setLogoStatus(`Added ${data.added.length} logo${data.added.length === 1 ? "" : "s"} to My logos.${skipped}`, true);
    logoFilter = "mine";
    await loadLogoLibrary();
  } catch (error) {
    setLogoStatus(error.message);
  }
  input.value = "";
}

async function importPinterestLogo() {
  const field = document.getElementById("logo-pin");
  const button = document.getElementById("logo-pin-button");
  const url = field.value.trim();
  if (!url) { setLogoStatus("Paste the link of a Pinterest pin first"); return; }
  button.disabled = true;
  setLogoStatus("Fetching the pin...", true);
  try {
    await requestJson("/api/logos/pinterest", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ url }) });
    field.value = "";
    setLogoStatus("Added to My logos. Click it to use it.", true);
    logoFilter = "mine";
    await loadLogoLibrary();
  } catch (error) {
    setLogoStatus(error.message);
  } finally {
    button.disabled = false;
  }
}

document.getElementById("logo-filter").addEventListener("click", event => {
  const pill = event.target.closest("[data-key]");
  if (!pill) return;
  logoFilter = pill.dataset.key;
  renderLogoFilters();
  renderLogoGrid();
});
document.getElementById("logo-search").addEventListener("input", renderLogoGrid);
document.getElementById("logo-pin").addEventListener("keydown", event => {
  if (event.key === "Enter") { event.preventDefault(); importPinterestLogo(); }
});
