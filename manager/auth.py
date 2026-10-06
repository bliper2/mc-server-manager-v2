"""Accounts, sessions, permissions, two-factor sign-in and staff management."""

import csv
import hmac
import io
import json
import os
import re
import threading
import time
from datetime import datetime

from flask import Response, abort, jsonify, request, session
from werkzeug.security import check_password_hash, generate_password_hash

from . import app, totp
from .config import STAFF_FILE, VERSION
from .notify import notify
from .store import audit, read_audit
from .util import SERVER_ID_PATTERN

def require_admin():
    """No-op unless MC_MANAGER_TOKEN is set; an optional extra lock on top of sign-in."""
    token = os.environ.get("MC_MANAGER_TOKEN")
    if not token:
        return None
    sent = request.headers.get("X-Admin-Token") or (request.json or {}).get("token") if request.is_json else request.headers.get("X-Admin-Token")
    if not hmac.compare_digest(str(sent or ""), token):
        return jsonify({"ok": False, "error": "Admin token required"}), 401
    return None

MAX_STAFF = 2
USERNAME_PATTERN = re.compile(r"[A-Za-z0-9_.\-]{3,24}")
PASSWORD_MIN, PASSWORD_MAX = 8, 128
LOGIN_MAX_FAILS = 5
LOGIN_LOCK_SECONDS = 300
PUBLIC_API = {"/api/auth/login", "/api/auth/setup", "/api/auth/status", "/api/boot", "/api/health"}
LAST_SEEN_EVERY = 60
last_seen_cache = {}
# Passwords that are guessed first. Not exhaustive, just the obvious ones.
COMMON_PASSWORDS = {"password", "password1", "password12", "password123", "12345678", "123456789", "1234567890", "qwertyui", "qwerty123",
                    "qwertyuiop", "iloveyou", "admin123", "letmein1", "welcome1", "minecraft", "minecraft1", "minecraft123", "11111111",
                    "00000000", "abcd1234", "abc12345", "passw0rd", "p@ssw0rd", "changeme", "football", "baseball", "dragon123", "monkey123"}
account_lock = threading.Lock()
login_attempts = {}
# Checked against when a username does not exist, so a miss costs the same time as a wrong password.
DUMMY_HASH = generate_password_hash("not-a-real-password")

# What a staff account may do. Reading is always allowed; these gate changes.
PERMISSION_LABELS = {
    "control": "Start, stop and restart servers",
    "console": "Send console commands and moderate players",
    "files": "Edit files, properties, plugins and mods",
    "backups": "Create, restore and delete backups",
    "manage": "Create, import and delete servers",
}
ALL_PERMISSIONS = tuple(PERMISSION_LABELS)
DEFAULT_STAFF_PERMISSIONS = ("control", "console", "backups")

# Every state-changing endpoint must be listed: a permission, "owner", or "any" (any signed-in account).
# Unlisted write endpoints are refused, so a new route cannot ship without a decision (tests enforce this).
ENDPOINT_RULES = {
    "api_start": "control", "api_stop": "control", "api_restart": "control", "api_reload_components": "control",
    "api_playit": "control", "api_playit_start": "control", "api_playit_stop": "control", "api_automation": "control",
    "api_command": "console", "api_player_action": "console", "api_map_markers": "console",
    "api_fs_write": "files", "api_fs_delete": "files", "api_fs_mkdir": "files", "api_fs_upload": "files",
    "api_props_set": "files", "api_props_raw_set": "files", "api_install": "files", "api_updates_apply": "files",
    "api_server_logo": "files", "api_anticheat": "files", "api_auto_update": "files", "api_rcon": "files",
    "api_backup_create": "backups", "api_backup_restore": "backups", "api_backup_delete": "backups", "api_auto_backup": "backups",
    "api_create": "manage", "api_logos_import": "manage", "api_logos_pinterest": "manage", "api_logos_delete": "manage",
    "api_banners_import": "manage", "api_banners_pinterest": "manage", "api_banners_delete": "manage",
    "api_server_banner": "files", "api_server_banner_remove": "files", "api_import_start": "manage", "api_import_upload": "manage", "api_import_finish": "manage",
    "api_import_cancel": "manage", "api_delete": "manage",
    "api_staff_create": "owner", "api_staff_reset": "owner", "api_staff_delete": "owner", "api_staff_permissions": "owner",
    "api_staff_2fa_reset": "owner", "api_signout_all": "any", "api_diagnostics": "owner",
    "api_server_rename": "manage", "api_server_clone": "manage", "api_launch": "control", "api_fs_rename": "files",
    "api_plugin_toggle": "files", "api_plugin_delete": "files", "api_modpack_install": "manage", "api_curseforge_key": "owner", "api_manager_update_settings": "owner", "api_manager_update_apply": "owner",
    "api_manager_update_rollback": "owner", "api_manager_restart": "owner", "api_notifications": "owner",
    "api_notifications_test": "owner", "api_java_install": "owner",
    "api_manager_update_check": "any", "api_auth_logout": "any", "api_auth_password": "any", "api_auth_2fa_begin": "any",
    "api_auth_2fa_enable": "any", "api_auth_2fa_disable": "any",
    "api_auth_login": "public", "api_auth_setup": "public",
}

def load_accounts() -> list:
    try:
        data = json.loads(STAFF_FILE.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return []
    return data if isinstance(data, list) else []

def save_accounts(accounts: list):
    temp = STAFF_FILE.with_suffix(".tmp")
    temp.write_text(json.dumps(accounts, indent=2), encoding="utf-8")
    os.replace(temp, STAFF_FILE)

def find_account(username: str, accounts=None):
    wanted = (username or "").strip().lower()
    return next((a for a in (accounts if accounts is not None else load_accounts()) if a["username"].lower() == wanted), None)

def account_permissions(account: dict) -> list:
    if account["role"] == "owner":
        return list(ALL_PERMISSIONS)
    granted = account.get("permissions")
    if not isinstance(granted, list):
        granted = ALL_PERMISSIONS  # staff created before permissions existed keep the access they had
    return [p for p in ALL_PERMISSIONS if p in granted]

def clean_permissions(value):
    """Validated permission list from request data, or None when it is not a list of known permissions."""
    if not isinstance(value, list) or any(p not in ALL_PERMISSIONS for p in value):
        return None
    return [p for p in ALL_PERMISSIONS if p in value]

def public_account(account: dict) -> dict:
    info = {key: account.get(key) for key in ("username", "role", "created", "last_login", "last_seen")}
    info["permissions"] = account_permissions(account)
    info["totp_enabled"] = bool(account.get("totp_enabled"))
    info["recovery_left"] = len(account.get("recovery") or [])
    return info

def needs_setup() -> bool:
    return not STAFF_FILE.exists()

def is_local_request() -> bool:
    """First-run setup is limited to the host PC. A Tailscale or proxy hop adds X-Forwarded-For."""
    return request.remote_addr in ("127.0.0.1", "::1") and not request.headers.get("X-Forwarded-For")

def client_ip() -> str:
    forwarded = request.headers.get("X-Forwarded-For", "").split(",")[0].strip()
    return forwarded or request.remote_addr or "unknown"

def current_account():
    if "account" in request.environ:
        return request.environ["account"]
    account = None
    name = session.get("user")
    if name:
        account = find_account(name)
        if not account or session.get("sv") != account.get("sv"):
            session.clear()
            account = None
    request.environ["account"] = account
    if account:
        note_activity(account["username"])
    return account

def note_activity(username: str):
    """Remembers when someone last used the panel, written at most once a minute per person."""
    now = time.time()
    if now - last_seen_cache.get(username, 0) < LAST_SEEN_EVERY:
        return
    last_seen_cache[username] = now
    with account_lock:
        accounts = load_accounts()
        stored = find_account(username, accounts)
        if stored:
            stored["last_seen"] = datetime.now().isoformat(timespec="seconds")
            save_accounts(accounts)

def start_session(account: dict):
    session.clear()
    session.permanent = True
    session["user"] = account["username"]
    session["sv"] = account["sv"]
    request.environ["account"] = account

def validate_credentials(username: str, password: str):
    if not USERNAME_PATTERN.fullmatch(username or ""):
        return "Username must be 3-24 characters: letters, numbers, dot, dash or underscore"
    if not isinstance(password, str) or not PASSWORD_MIN <= len(password) <= PASSWORD_MAX:
        return f"Password must be {PASSWORD_MIN}-{PASSWORD_MAX} characters"
    if password.strip().lower() == username.strip().lower():
        return "Password cannot be the same as the username"
    if password.lower() in COMMON_PASSWORDS or len(set(password)) < 4:
        return "That password is too easy to guess. Use something longer or less common."
    return None

def new_account(username: str, password: str, role: str, permissions=None) -> dict:
    account = {
        "username": username,
        "role": role,
        "password_hash": generate_password_hash(password),
        "created": datetime.now().isoformat(timespec="seconds"),
        "last_login": None,
        "sv": 1
    }
    if role == "staff":
        account["permissions"] = list(permissions if permissions is not None else DEFAULT_STAFF_PERMISSIONS)
    return account

def lock_remaining(key: str) -> int:
    entry = login_attempts.get(key)
    if entry and entry["until"] > time.time():
        return int(entry["until"] - time.time()) + 1
    return 0

def record_login_failure(key: str, username: str = ""):
    now = time.time()
    entry = login_attempts.get(key)
    if not entry or now - entry["first"] > LOGIN_LOCK_SECONDS * 2:
        entry = {"count": 0, "first": now, "until": 0}
    entry["count"] += 1
    if entry["count"] >= LOGIN_MAX_FAILS:
        entry["until"] = now + LOGIN_LOCK_SECONDS
        entry["count"] = 0
        entry["first"] = now
        notify("security", f"Sign-in locked for 5 minutes after repeated failures for **{username or '?'}**.", None, [("From", key.split("|")[0])])
    login_attempts[key] = entry

def require_owner():
    account = current_account()
    if not account or account["role"] != "owner":
        return jsonify({"ok": False, "error": "Only the owner can do that"}), 403
    return None

def check_second_factor(account: dict, code: str) -> bool:
    """Accepts a current authenticator code (once) or an unused recovery code. Mutates `account`; caller saves."""
    digits = "".join(ch for ch in code if ch.isdigit())
    if len(digits) == totp.DIGITS and len(code.strip().replace(" ", "")) == totp.DIGITS:
        step = totp.verify(account["totp_secret"], digits, int(account.get("totp_last", 0)))
        if step is None:
            return False
        account["totp_last"] = step
        return True
    remaining = totp.consume_recovery(code, account.get("recovery") or [])
    if remaining is None:
        return False
    account["recovery"] = remaining
    return True

@app.before_request
def guard_request():
    sid = (request.view_args or {}).get("sid")
    if sid is not None and not SERVER_ID_PATTERN.fullmatch(sid):
        abort(404)  # "." and ".." would otherwise resolve to the servers folder or the app itself
    if not request.path.startswith("/api/") or request.path in PUBLIC_API:
        return None
    account = current_account()
    if not account:
        return jsonify({"ok": False, "error": "Sign in required"}), 401
    if request.method in ("GET", "HEAD", "OPTIONS"):
        return None
    rule = ENDPOINT_RULES.get(request.endpoint)
    if rule in ("any", "public"):
        return None
    if rule == "owner":
        return None if account["role"] == "owner" else (jsonify({"ok": False, "error": "Only the owner can do that"}), 403)
    if rule is None:
        return jsonify({"ok": False, "error": "This action is not available"}), 403
    if rule not in account_permissions(account):
        return jsonify({"ok": False, "error": f"Your account cannot do this ({PERMISSION_LABELS[rule].lower()}). Ask the owner for access."}), 403
    return None

@app.after_request
def record_staff_actions(response):
    if request.method == "POST" and request.path.startswith("/api/") and request.path not in PUBLIC_API \
            and request.path not in ("/api/import/upload", "/api/auth/logout"):
        account = request.environ.get("account")
        if account:
            detail = {"status": response.status_code}
            sid = (request.view_args or {}).get("sid")
            if sid:
                detail["server"] = sid
            audit(account["username"], f"POST {request.path}", **detail)
    return response

@app.route("/api/auth/status")
def api_auth_status():
    account = current_account()
    return jsonify({
        "ok": True,
        "setup_required": needs_setup(),
        "setup_allowed": needs_setup() and is_local_request(),
        "user": public_account(account) if account else None,
        "max_staff": MAX_STAFF,
        "version": VERSION
    })

@app.route("/api/auth/setup", methods=["POST"])
def api_auth_setup():
    if not needs_setup():
        return jsonify({"ok": False, "error": "An owner account already exists"}), 409
    if not is_local_request():
        return jsonify({"ok": False, "error": "Create the owner account from the PC that runs the manager"}), 403
    data = request.get_json(silent=True) or {}
    username = str(data.get("username") or "").strip()
    password = data.get("password")
    problem = validate_credentials(username, password)
    if problem:
        return jsonify({"ok": False, "error": problem}), 400
    with account_lock:
        if not needs_setup():
            return jsonify({"ok": False, "error": "An owner account already exists"}), 409
        owner = new_account(username, password, "owner")
        owner["last_login"] = datetime.now().isoformat(timespec="seconds")
        save_accounts([owner])
    start_session(owner)
    audit(username, "owner account created")
    return jsonify({"ok": True, "user": public_account(owner)})

@app.route("/api/auth/login", methods=["POST"])
def api_auth_login():
    data = request.get_json(silent=True) or {}
    username = str(data.get("username") or "").strip()
    password = data.get("password") if isinstance(data.get("password"), str) else ""
    code = str(data.get("code") or "").strip()
    key = f"{client_ip()}|{username.lower()}"
    wait = lock_remaining(key)
    if wait:
        return jsonify({"ok": False, "error": f"Too many failed attempts. Try again in {wait} seconds."}), 429
    reason = "failed sign-in"
    with account_lock:
        accounts = load_accounts()
        account = find_account(username, accounts)
        valid = check_password_hash(account["password_hash"] if account else DUMMY_HASH, password) and account is not None
        if valid and account.get("totp_enabled"):
            if not code:
                # The password was right: ask for the second factor without counting a failure.
                return jsonify({"ok": False, "totp_required": True})
            if check_second_factor(account, code):
                save_accounts(accounts)
            else:
                valid = False
                reason = "failed two-factor code"
        if valid:
            login_attempts.pop(key, None)
            account["last_login"] = datetime.now().isoformat(timespec="seconds")
            save_accounts(accounts)
    if not valid:
        record_login_failure(key, username[:24])
        audit(username[:24] or "?", reason, ip=client_ip())
        return jsonify({"ok": False, "error": "Wrong code" if reason.endswith("code") else "Wrong username or password",
                        "totp_required": reason.endswith("code")}), 401
    start_session(account)
    audit(account["username"], "signed in", ip=client_ip())
    return jsonify({"ok": True, "user": public_account(account)})

@app.route("/api/auth/logout", methods=["POST"])
def api_auth_logout():
    account = current_account()
    if account:
        audit(account["username"], "signed out")
    session.clear()
    return jsonify({"ok": True})

@app.route("/api/auth/password", methods=["POST"])
def api_auth_password():
    account = current_account()
    data = request.get_json(silent=True) or {}
    current, new = data.get("current"), data.get("new")
    if not isinstance(current, str) or not check_password_hash(account["password_hash"], current):
        return jsonify({"ok": False, "error": "Current password is wrong"}), 400
    problem = validate_credentials(account["username"], new)
    if problem:
        return jsonify({"ok": False, "error": problem}), 400
    with account_lock:
        accounts = load_accounts()
        stored = find_account(account["username"], accounts)
        stored["password_hash"] = generate_password_hash(new)
        stored["sv"] = int(stored.get("sv", 1)) + 1
        save_accounts(accounts)
    start_session(stored)
    audit(account["username"], "changed own password")
    return jsonify({"ok": True})

@app.route("/api/auth/signout-all", methods=["POST"])
def api_signout_all():
    """Ends every other session of this account (stolen cookie, forgotten browser) and keeps this one."""
    account = current_account()
    with account_lock:
        accounts = load_accounts()
        stored = find_account(account["username"], accounts)
        stored["sv"] = int(stored.get("sv", 1)) + 1
        save_accounts(accounts)
    start_session(stored)
    audit(account["username"], "signed out everywhere else")
    return jsonify({"ok": True})

@app.route("/api/auth/2fa/begin", methods=["POST"])
def api_auth_2fa_begin():
    account = current_account()
    if account.get("totp_enabled"):
        return jsonify({"ok": False, "error": "Two-factor sign-in is already on"}), 409
    secret = totp.new_secret()
    with account_lock:
        accounts = load_accounts()
        stored = find_account(account["username"], accounts)
        stored["totp_pending"] = secret
        save_accounts(accounts)
    return jsonify({"ok": True, "secret": secret, "uri": totp.otpauth_uri(secret, account["username"])})

@app.route("/api/auth/2fa/enable", methods=["POST"])
def api_auth_2fa_enable():
    account = current_account()
    code = str((request.get_json(silent=True) or {}).get("code") or "")
    with account_lock:
        accounts = load_accounts()
        stored = find_account(account["username"], accounts)
        pending = stored.get("totp_pending")
        if not pending:
            return jsonify({"ok": False, "error": "Start setup first"}), 400
        step = totp.verify(pending, code)
        if step is None:
            return jsonify({"ok": False, "error": "That code is not right. Check the clock on your phone and try the next code."}), 400
        plain, hashes = totp.new_recovery_codes()
        stored.update(totp_secret=pending, totp_enabled=True, totp_last=step, recovery=hashes)
        stored.pop("totp_pending", None)
        save_accounts(accounts)
    audit(account["username"], "turned on two-factor sign-in")
    return jsonify({"ok": True, "recovery_codes": plain})

@app.route("/api/auth/2fa/disable", methods=["POST"])
def api_auth_2fa_disable():
    account = current_account()
    data = request.get_json(silent=True) or {}
    if not check_password_hash(account["password_hash"], str(data.get("password") or "")):
        return jsonify({"ok": False, "error": "Password is wrong"}), 400
    with account_lock:
        accounts = load_accounts()
        stored = find_account(account["username"], accounts)
        if stored.get("totp_enabled") and not check_second_factor(stored, str(data.get("code") or "")):
            return jsonify({"ok": False, "error": "Enter a current code or a recovery code"}), 400
        for field in ("totp_secret", "totp_enabled", "totp_last", "totp_pending", "recovery"):
            stored.pop(field, None)
        save_accounts(accounts)
    audit(account["username"], "turned off two-factor sign-in")
    return jsonify({"ok": True})

@app.route("/api/staff")
def api_staff_list():
    denied = require_owner()
    if denied:
        return denied
    accounts = [public_account(a) for a in load_accounts()]
    return jsonify({
        "ok": True,
        "accounts": accounts,
        "staff_count": sum(1 for a in accounts if a["role"] == "staff"),
        "max_staff": MAX_STAFF,
        "permissions": [{"id": key, "label": label} for key, label in PERMISSION_LABELS.items()],
        "default_permissions": list(DEFAULT_STAFF_PERMISSIONS),
        "audit": read_audit()
    })

@app.route("/api/staff", methods=["POST"])
def api_staff_create():
    denied = require_owner()
    if denied:
        return denied
    data = request.get_json(silent=True) or {}
    username = str(data.get("username") or "").strip()
    password = data.get("password")
    problem = validate_credentials(username, password)
    if problem:
        return jsonify({"ok": False, "error": problem}), 400
    permissions = DEFAULT_STAFF_PERMISSIONS
    if "permissions" in data:
        permissions = clean_permissions(data["permissions"])
        if permissions is None:
            return jsonify({"ok": False, "error": "Unknown permission"}), 400
    with account_lock:
        accounts = load_accounts()
        if sum(1 for a in accounts if a["role"] == "staff") >= MAX_STAFF:
            return jsonify({"ok": False, "error": f"Only {MAX_STAFF} staff accounts are allowed. Remove one first."}), 409
        if find_account(username, accounts):
            return jsonify({"ok": False, "error": "That username is already taken"}), 409
        accounts.append(new_account(username, password, "staff", permissions))
        save_accounts(accounts)
    audit(current_account()["username"], "created staff account", target=username)
    notify("staff_change", f"**{current_account()['username']}** created staff account **{username}**")
    return jsonify({"ok": True})

@app.route("/api/staff/<username>/permissions", methods=["POST"])
def api_staff_permissions(username):
    denied = require_owner()
    if denied:
        return denied
    permissions = clean_permissions((request.get_json(silent=True) or {}).get("permissions"))
    if permissions is None:
        return jsonify({"ok": False, "error": "Unknown permission"}), 400
    with account_lock:
        accounts = load_accounts()
        target = find_account(username, accounts)
        if not target or target["role"] != "staff":
            return jsonify({"ok": False, "error": "Staff account not found"}), 404
        target["permissions"] = permissions
        save_accounts(accounts)
    audit(current_account()["username"], "changed staff permissions", target=target["username"], permissions=",".join(permissions) or "none")
    return jsonify({"ok": True, "permissions": permissions})

@app.route("/api/staff/<username>/password", methods=["POST"])
def api_staff_reset(username):
    denied = require_owner()
    if denied:
        return denied
    password = (request.get_json(silent=True) or {}).get("password")
    problem = validate_credentials(username, password)
    if problem:
        return jsonify({"ok": False, "error": problem}), 400
    with account_lock:
        accounts = load_accounts()
        target = find_account(username, accounts)
        if not target or target["role"] != "staff":
            return jsonify({"ok": False, "error": "Staff account not found"}), 404
        target["password_hash"] = generate_password_hash(password)
        target["sv"] = int(target.get("sv", 1)) + 1  # signs that person out everywhere
        save_accounts(accounts)
    audit(current_account()["username"], "reset staff password", target=target["username"])
    notify("staff_change", f"**{current_account()['username']}** reset the password of **{target['username']}**")
    return jsonify({"ok": True})

@app.route("/api/staff/<username>/2fa/reset", methods=["POST"])
def api_staff_2fa_reset(username):
    denied = require_owner()
    if denied:
        return denied
    with account_lock:
        accounts = load_accounts()
        target = find_account(username, accounts)
        if not target or target["role"] != "staff":
            return jsonify({"ok": False, "error": "Staff account not found"}), 404
        for field in ("totp_secret", "totp_enabled", "totp_last", "totp_pending", "recovery"):
            target.pop(field, None)
        target["sv"] = int(target.get("sv", 1)) + 1
        save_accounts(accounts)
    audit(current_account()["username"], "reset staff two-factor", target=target["username"])
    return jsonify({"ok": True})

@app.route("/api/staff/<username>/delete", methods=["POST"])
def api_staff_delete(username):
    denied = require_owner()
    if denied:
        return denied
    with account_lock:
        accounts = load_accounts()
        target = find_account(username, accounts)
        if not target or target["role"] != "staff":
            return jsonify({"ok": False, "error": "Staff account not found"}), 404
        save_accounts([a for a in accounts if a is not target])
    audit(current_account()["username"], "removed staff account", target=target["username"])
    notify("staff_change", f"**{current_account()['username']}** removed staff account **{target['username']}**")
    return jsonify({"ok": True})

@app.route("/api/staff/audit.csv")
def api_audit_csv():
    denied = require_owner()
    if denied:
        return denied
    rows = read_audit(5000)
    columns = ["at", "user", "action", "server", "target", "status", "ip", "exit_code"]
    out = io.StringIO()
    writer = csv.writer(out)
    writer.writerow(columns)
    for entry in rows:
        # A leading = + - @ would be run as a formula when the file is opened in a spreadsheet.
        writer.writerow([("'" + str(entry.get(col)) if str(entry.get(col, ""))[:1] in "=+-@" else entry.get(col, "")) for col in columns])
    return Response(out.getvalue(), mimetype="text/csv", headers={"Content-Disposition": "attachment; filename=audit.csv"})
