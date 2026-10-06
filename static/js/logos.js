// Image library dialog for server logos and banners: bundled artwork, files from the logos/banners folder, uploads, Pinterest pins.
// The create form and the server page open it; GIFs work as logos and banners and stay animated.

const LOGO_FILTERS = [["all", "All"], ["mine", "Mine"], ["community", "Community"], ["animated", "Animated"], ["combat", "Combat"], ["royal", "Royal"], ["nature", "Nature"],
                      ["build", "Build"], ["pixel", "Pixel art"], ["scenic", "Scenic"], ["pattern", "Patterns"]];
const PICKER_KINDS = {
  logo: { api: "/api/logos", eyebrow: "Server logo", title: "Choose a logo", folder: "logos", mine: "My logos", search: "Search: sword, emerald, crown...",
          empty: "Nothing imported yet. Upload images or paste a Pinterest link below, or drop files into the logos folder and press Refresh." },
  banner: { api: "/api/banners", eyebrow: "Server banner", title: "Choose a banner", folder: "banners", mine: "My banners", search: "Search your banners",
            empty: "No banners yet. Upload images or GIFs or paste a Pinterest link below, or drop files into the banners folder and press Refresh." },
};
let logoLibrary = [];
let logoFilter = "all";
let logoPickHandler = null;
let pickerKind = "logo";

function setLogoStatus(message, ok = false) {
  const status = document.getElementById("logo-status");
  status.textContent = message;
  status.className = message ? `status-msg show ${ok ? "ok" : "err"}` : "status-msg";
}

async function openLogoPicker(onPick, kind = "logo") {
  logoPickHandler = onPick;
  pickerKind = kind;
  logoFilter = "all";
  const config = PICKER_KINDS[kind];
  document.getElementById("logo-dialog").classList.toggle("banners", kind === "banner");
  document.getElementById("logo-eyebrow").textContent = config.eyebrow;
  document.getElementById("logo-title").textContent = config.title;
  document.getElementById("logo-folder-name").textContent = config.folder;
  document.getElementById("logo-search").placeholder = config.search;
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
  const data = await requestJson(PICKER_KINDS[pickerKind].api);
  logoLibrary = [...data.custom, ...(data.community || []), ...(data.bundled || [])];
  renderLogoFilters();
  renderLogoGrid();
}

function renderLogoFilters() {
  const present = new Set(logoLibrary.map(logo => logo.category));
  document.getElementById("logo-filter").innerHTML = LOGO_FILTERS
    .filter(([key]) => key === "all" || key === "mine" || present.has(key))
    .map(([key, label]) => `<button type="button" class="filter-pill${key === logoFilter ? " active" : ""}" data-key="${key}">${key === "mine" ? PICKER_KINDS[pickerKind].mine : label}</button>`)
    .join("");
}

function renderLogoGrid() {
  const grid = document.getElementById("logo-grid");
  const query = document.getElementById("logo-search").value.trim().toLowerCase();
  const shown = logoLibrary.filter(logo => (logoFilter === "all" || logo.category === logoFilter) && (!query || `${logo.name} ${logo.category}`.toLowerCase().includes(query)));
  if (!shown.length) {
    const empty = logoFilter === "mine" && !query ? PICKER_KINDS[pickerKind].empty : "Nothing matches.";
    grid.innerHTML = `<div class="logo-empty">${empty}</div>`;
    return;
  }
  const size = pickerKind === "banner" ? 'width="240" height="80"' : 'width="64" height="64"';
  grid.innerHTML = shown.map(logo => `<div class="logo-tile">
      <button type="button" class="logo-pick" role="option" title="${escapeHtml(logo.name)}" onclick="chooseLogo(${jsArg(logo.ref)})"><img src="${escapeHtml(logo.url)}" alt="${escapeHtml(logo.name)}" ${size} loading="lazy" /></button>
      ${logo.category === "mine" ? `<button type="button" class="logo-remove" data-perm="manage" title="Delete this file" aria-label="Delete ${escapeHtml(logo.name)}" onclick="removeLogo(${jsArg(logo.ref)})">✕</button>` : ""}
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
  const config = PICKER_KINDS[pickerKind];
  if (!logo || !await uiAsk({ title: "Delete this file?", message: `"${logo.name}" is deleted from the ${config.folder} folder. Servers already using it keep their copy.`, confirmText: "Delete", danger: true })) return;
  try {
    await requestJson(`${config.api}/custom/${encodeURIComponent(ref.split("/")[1])}`, { method: "DELETE" });
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
    const data = await requestJson(`${PICKER_KINDS[pickerKind].api}/custom`, { method: "POST", body: form });
    const skipped = data.skipped.length ? ` ${data.skipped.length} skipped (${data.skipped[0]}).` : "";
    setLogoStatus(`Added ${data.added.length} file${data.added.length === 1 ? "" : "s"}.${skipped}`, true);
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
    await requestJson(`${PICKER_KINDS[pickerKind].api}/pinterest`, { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ url }) });
    field.value = "";
    setLogoStatus("Added. Click it to use it.", true);
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
