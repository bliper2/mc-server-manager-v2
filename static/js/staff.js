// Owner-only staff panel.

let permissionCatalog = [];

function permissionBoxes(selected, attrs = "") {
  return permissionCatalog.map(perm =>
    `<label class="perm-item" title="${escapeHtml(perm.label)}"><input type="checkbox" value="${escapeHtml(perm.id)}" ${selected.includes(perm.id) ? "checked" : ""} ${attrs} /><span>${escapeHtml(perm.label)}</span></label>`).join("");
}

async function loadStaff() {
  if (!IS_OWNER) return;
  const list = document.getElementById("staff-list");
  try {
    const data = await requestJson("/api/staff");
    if (!data.ok) throw new Error(data.error || "Could not load staff");
    permissionCatalog = data.permissions;
    const newBox = document.getElementById("staff-new-permissions");
    if (!newBox.children.length) newBox.innerHTML = permissionBoxes(data.default_permissions);
    document.getElementById("staff-slots").textContent = `${data.staff_count} / ${data.max_staff} staff slots`;
    const full = data.staff_count >= data.max_staff;
    document.getElementById("staff-add-card").classList.toggle("is-full", full);
    document.querySelectorAll("#staff-form input, #btn-staff-add").forEach(node => { node.disabled = full; });
    list.innerHTML = data.accounts.map((account, i) => {
      const staff = account.role === "staff";
      return `
      <div class="staff-row" style="--i:${i}">
        <span class="user-avatar ${account.role}" aria-hidden="true">${escapeHtml(account.username[0].toUpperCase())}</span>
        <div class="staff-meta">
          <strong>${escapeHtml(account.username)}</strong>
          <small>Last sign-in ${relativeTime(account.last_login)} · added ${new Date(account.created).toLocaleDateString()}</small>
        </div>
        ${account.totp_enabled ? '<span class="badge online" title="Signs in with an authenticator code">2FA</span>' : '<span class="badge offline" title="Password only">No 2FA</span>'}
        <span class="badge ${account.role === "owner" ? "online" : "type"}">${account.role === "owner" ? "OWNER" : "STAFF"}</span>
        ${staff ? `<div class="staff-actions">
          <button class="btn small" onclick="resetStaffPassword(${jsArg(account.username)})">Reset password</button>
          ${account.totp_enabled ? `<button class="btn small" onclick="resetStaffTwoFactor(${jsArg(account.username)})">Reset 2FA</button>` : ""}
          <button class="btn danger small" onclick="removeStaff(this, ${jsArg(account.username)})">Remove</button>
        </div>
        <div class="staff-perms" data-user="${escapeHtml(account.username)}">${permissionBoxes(account.permissions, `onchange="saveStaffPermissions(${jsArg(account.username)})"`)}</div>` : '<div class="staff-perms muted">Full access to everything</div>'}
      </div>`;
    }).join("");
    const audit = document.getElementById("staff-audit");
    audit.innerHTML = data.audit.length ? data.audit.map(entry => {
      const extra = entry.target || entry.ip || (entry.server ? `server ${entry.server}` : "") || (entry.status ? `HTTP ${entry.status}` : "");
      return `<div class="audit-row"><time>${new Date(entry.at).toLocaleString()}</time><strong>${escapeHtml(entry.user)}</strong><span>${escapeHtml(entry.action)}</span><small>${escapeHtml(extra)}</small></div>`;
    }).join("") : '<div class="empty">No activity yet</div>';
  } catch (error) {
    list.innerHTML = `<div class="empty">${escapeHtml(error.message)}</div>`;
  }
}

async function createStaff(event) {
  event.preventDefault();
  await withBusy(document.getElementById("btn-staff-add"), async () => {
    try {
      const permissions = [...document.querySelectorAll("#staff-new-permissions input:checked")].map(box => box.value);
      const data = await requestJson("/api/staff", {
        method: "POST", headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ username: document.getElementById("staff-username").value.trim(), password: document.getElementById("staff-password").value, permissions })
      });
      if (!data.ok) throw new Error(data.error || "Could not create the account");
      document.getElementById("staff-username").value = "";
      document.getElementById("staff-password").value = "";
      showToast("Staff account created", "success");
    } catch (error) {
      showToast(error.message, "error");
    }
  });
  loadStaff();
}

async function saveStaffPermissions(username) {
  const row = [...document.querySelectorAll(".staff-perms[data-user]")].find(node => node.dataset.user === username);
  const permissions = [...row.querySelectorAll("input:checked")].map(box => box.value);
  try {
    const data = await requestJson(`/api/staff/${encodeURIComponent(username)}/permissions`, {
      method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ permissions })
    });
    if (!data.ok) throw new Error(data.error);
    showToast(`Updated what ${username} can do`, "success");
  } catch (error) {
    showToast(error.message, "error");
    loadStaff();
  }
}

async function resetStaffTwoFactor(username) {
  if (!(await uiConfirm(`Turn off two-factor sign-in for ${username}? They are signed out and can set it up again.`, { title: "Reset two-factor", confirmText: "Reset", danger: true, always: true }))) return;
  try {
    const data = await requestJson(`/api/staff/${encodeURIComponent(username)}/2fa/reset`, { method: "POST" });
    if (!data.ok) throw new Error(data.error);
    showToast(`Two-factor reset for ${username}`, "success");
  } catch (error) {
    showToast(error.message, "error");
  }
  loadStaff();
}

function askNewPassword(username) {
  const dialog = document.getElementById("password-dialog");
  const input = document.getElementById("password-dialog-input");
  document.getElementById("password-dialog-hint").textContent = `Choose a new password for ${username}. They will be signed out everywhere.`;
  input.value = "";
  return new Promise(resolve => {
    dialog.addEventListener("close", () => resolve(dialog.returnValue === "ok" ? input.value : null), { once: true });
    dialog.showModal();
    input.focus();
  });
}

async function resetStaffPassword(username) {
  const password = await askNewPassword(username);
  if (!password) return;
  try {
    const data = await requestJson(`/api/staff/${encodeURIComponent(username)}/password`, {
      method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ password })
    });
    if (!data.ok) throw new Error(data.error || "Could not reset the password");
    showToast(`Password reset for ${username}`, "success");
  } catch (error) {
    showToast(error.message, "error");
  }
  loadStaff();
}

async function removeStaff(button, username) {
  if (!(await uiConfirm(`Remove ${username}? They lose access immediately.`, { title: "Remove staff", confirmText: "Remove", danger: true, always: true }))) return;
  await withBusy(button, async () => {
    try {
      const data = await requestJson(`/api/staff/${encodeURIComponent(username)}/delete`, { method: "POST" });
      if (!data.ok) throw new Error(data.error || "Could not remove the account");
      showToast(`Removed ${username}`, "success");
    } catch (error) {
      showToast(error.message, "error");
    }
  });
  loadStaff();
}
