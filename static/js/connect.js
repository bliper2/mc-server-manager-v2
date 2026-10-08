// "Let friends join": the addresses this PC can be reached on, who can use each one, and what could block a friend.

let connectInfo = null;

const NETWORK_ADVICE = {
  lan: "Friends on the same Wi-Fi or router",
  vpn: "Only people on the same VPN network",
  virtual: "This PC only (virtual adapter)"
};

// The address worth copying: a home-network address when there is one, otherwise localhost.
function bestAddress() {
  const home = connectInfo?.addresses.find(item => item.kind === "lan" && item.private);
  return `${home ? home.address : "localhost"}:${connectInfo?.port || currentServerPort}`;
}

async function loadConnect() {
  if (!currentServerId) return;
  const id = currentServerId;
  try {
    const data = await requestJson(`/api/server/${id}/connect`);
    if (id !== currentServerId) return;
    connectInfo = data;
    renderConnect(data);
  } catch (error) {
    document.getElementById("connect-list").innerHTML = `<div class="empty">${escapeHtml(error.message)}</div>`;
  }
}

function connectRow(label, address, note, extra = "") {
  return `<div class="connect-row"><div><strong>${escapeHtml(address)}</strong><small>${escapeHtml(label)} · ${escapeHtml(note)}</small></div>
    <button class="btn small" onclick="copyText(${jsArg(address)})">Copy</button>${extra}</div>`;
}

function renderConnect(data) {
  const badge = document.getElementById("connect-badge");
  const notes = [];
  if (!data.running) { badge.textContent = "Server stopped"; badge.className = "badge offline"; notes.push("Start the server first, then wait for the Done line in the console."); }
  else if (!data.listening) { badge.textContent = "Starting"; badge.className = "badge offline"; notes.push(`Nothing is accepting connections on port ${data.port} yet. Wait for the Done line in the console.`); }
  else { badge.textContent = "Ready"; badge.className = "badge online"; }
  const rows = [connectRow("This PC", `localhost:${data.port}`, "only you, on this PC")];
  data.addresses.filter(item => item.kind !== "virtual").forEach(item => {
    rows.push(connectRow(item.label, `${item.address}:${data.port}`, NETWORK_ADVICE[item.kind]));
  });
  document.getElementById("connect-list").innerHTML = rows.join("");
  const lan = data.addresses.filter(item => item.kind === "lan");
  if (lan.length > 1) notes.push("This PC is on more than one network. Give friends the address from the network they are on.");
  if (data.firewall === false) {
    notes.push(`Windows Firewall has no rule for port ${data.port}, so friends may be blocked unless you allowed Java when Windows asked. Run this in an administrator PowerShell: <code>${escapeHtml(data.firewall_command)}</code> <button class="btn small" onclick="copyText(${jsArg(data.firewall_command)})">Copy</button>`);
  }
  data.warnings.forEach(text => notes.push(escapeHtml(text)));
  document.getElementById("connect-notes").innerHTML = notes.map(text => `<div class="notice warn connect-note"><span>${text}</span></div>`).join("");
}

async function showPublicAddress() {
  const button = document.getElementById("btn-public-ip");
  button.disabled = true;
  try {
    const data = await requestJson(`/api/server/${currentServerId}/connect/public`);
    if (!data.ok) throw new Error(data.error);
    const port = connectInfo?.port || currentServerPort;
    document.getElementById("connect-list").insertAdjacentHTML("beforeend",
      connectRow("Public address", `${data.address}:${port}`, "works only with a port forward on your router, and not behind carrier NAT"));
    button.hidden = true;
  } catch (error) {
    showToast(error.message, "error");
  } finally {
    button.disabled = false;
  }
}

// While the server page is open, notice the server finishing its start-up (or stopping) without a manual refresh.
setInterval(() => {
  if (currentServerId && !document.hidden && document.getElementById("tab-detail")?.classList.contains("active")) loadConnect();
}, 6000);
