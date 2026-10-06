"""Self-update from GitHub (latest release or the main branch) with snapshot and rollback."""

import io
import json
import os
import subprocess
import zipfile
from datetime import datetime
from pathlib import Path

import requests
from flask import jsonify, request

from . import app
from .auth import require_admin, require_owner
from .config import BACKUPS_DIR, BASE_DIR, DATA_DIR, HEADERS, SERVERS_DIR
from .lifecycle import supervised
from .notify import notify
from .state import restart_flags
from .store import is_running, write_json_atomic
from .util import sanitize_relative_parts

UPDATE_REPO = os.environ.get("MC_MANAGER_REPO", "bliper2/mc-server-manager-v2")
UPDATE_BRANCH = os.environ.get("MC_MANAGER_BRANCH", "main")
GITHUB_API = os.environ.get("MC_MANAGER_UPDATE_API", "https://api.github.com").rstrip("/")
UPDATE_STATE_FILE = DATA_DIR / "update_state.json"
UPDATE_SNAPSHOTS = BACKUPS_DIR / "_manager"
UPDATE_CHECK_INTERVAL = 6 * 3600
# Anything holding the user's own data, or the environment the app runs in.
UPDATE_PROTECTED = {"servers", "backups", ".imports", ".venv", ".git", "__pycache__", "update_state.json", "staff.json", ".secret_key",
                    "audit.jsonl", "manager_settings.json", "restart_state.json", "manager.log", "logos", "banners"}
UPDATE_REQUIRED = ("app.py", "templates/index.html", "manager/__init__.py")
CHANNELS = ("releases", "main")

def load_update_state() -> dict:
    defaults = {"installed": None, "auto_check": True, "auto_install": False, "channel": "releases", "last_check": None, "latest": None}
    if not UPDATE_STATE_FILE.exists():
        return defaults
    try:
        return {**defaults, **json.loads(UPDATE_STATE_FILE.read_text(encoding="utf-8"))}
    except (OSError, json.JSONDecodeError):
        return defaults

def save_update_state(state: dict):
    write_json_atomic(UPDATE_STATE_FILE, state, indent=2)

def installed_commit(state: dict):
    """Falls back to git so a cloned checkout knows where it stands before the first update."""
    if state.get("installed"):
        return state["installed"]
    try:
        result = subprocess.run(["git", "rev-parse", "HEAD"], cwd=str(BASE_DIR),
                                capture_output=True, text=True, timeout=5)
        if result.returncode == 0:
            return result.stdout.strip()
    except (OSError, subprocess.SubprocessError):
        pass
    return None

def github_json(path: str):
    response = requests.get(f"{GITHUB_API}{path}", headers={**HEADERS, "Accept": "application/vnd.github+json"}, timeout=20)
    if response.status_code == 403 and "rate limit" in response.text.lower():
        raise RuntimeError("GitHub rate limit reached, try again later")
    response.raise_for_status()
    return response.json()

def resolve_target(state: dict):
    """Which git ref the update follows. The releases channel tracks the newest published release and falls
    back to the branch when the repository has none. Returns (ref, release info or None)."""
    if state.get("channel", "releases") == "releases":
        try:
            release = github_json(f"/repos/{UPDATE_REPO}/releases/latest")
            return release["tag_name"], {
                "tag": release["tag_name"],
                "name": release.get("name") or release["tag_name"],
                "url": release.get("html_url"),
                "notes": (release.get("body") or "")[:1500],
            }
        except requests.HTTPError as exc:
            if exc.response is None or exc.response.status_code != 404:
                raise
    return UPDATE_BRANCH, None

def fetch_update_status(state: dict) -> dict:
    ref, release = resolve_target(state)
    latest = github_json(f"/repos/{UPDATE_REPO}/commits/{ref}")
    current = installed_commit(state)
    info = {
        "sha": latest["sha"],
        "short": latest["sha"][:7],
        "ref": ref,
        "release": release,
        "channel": state.get("channel", "releases"),
        "message": latest["commit"]["message"].splitlines()[0],
        "date": latest["commit"]["committer"]["date"],
        "behind": [],
        "files": [],
        "update_available": bool(current) and current != latest["sha"]
    }
    if current and current != latest["sha"]:
        try:
            compare = github_json(f"/repos/{UPDATE_REPO}/compare/{current}...{ref}")
            info["behind"] = [c["commit"]["message"].splitlines()[0] for c in compare.get("commits", [])][-20:]
            info["count"] = compare.get("ahead_by", len(info["behind"]))
            info["files"] = [
                {"name": f["filename"], "status": f["status"], "added": f.get("additions", 0), "removed": f.get("deletions", 0)}
                for f in compare.get("files", [])[:60]
            ]
            info["files_total"] = len(compare.get("files", []))
        except (requests.RequestException, RuntimeError, KeyError):
            info["count"] = None
    elif not current:
        info["update_available"] = True
    return info

def is_protected(relative: str) -> bool:
    return relative.split("/", 1)[0] in UPDATE_PROTECTED

def snapshot_manager_files(paths) -> Path:
    UPDATE_SNAPSHOTS.mkdir(parents=True, exist_ok=True)
    archive = UPDATE_SNAPSHOTS / f"manager_{datetime.now().strftime('%Y%m%d-%H%M%S')}.zip"
    with zipfile.ZipFile(archive, "w", zipfile.ZIP_DEFLATED) as bundle:
        for relative in paths:
            source = BASE_DIR / relative
            if source.is_file():
                bundle.write(source, relative)
    for stale in sorted(UPDATE_SNAPSHOTS.glob("manager_*.zip"), key=lambda f: f.stat().st_mtime, reverse=True)[5:]:
        stale.unlink(missing_ok=True)
    return archive

def apply_manager_update() -> dict:
    state = load_update_state()
    ref, _ = resolve_target(state)
    latest = github_json(f"/repos/{UPDATE_REPO}/commits/{ref}")
    sha = latest["sha"]
    response = requests.get(f"{GITHUB_API}/repos/{UPDATE_REPO}/zipball/{ref}", headers=HEADERS, timeout=180)
    response.raise_for_status()

    with zipfile.ZipFile(io.BytesIO(response.content)) as bundle:
        members = [m for m in bundle.infolist() if not m.is_dir()]
        if not members:
            raise RuntimeError("Downloaded archive was empty")
        root = members[0].filename.split("/", 1)[0] + "/"
        incoming = {}
        for member in members:
            if not member.filename.startswith(root):
                continue
            relative = member.filename[len(root):]
            parts = sanitize_relative_parts(relative)
            if not parts or is_protected(relative):
                continue
            incoming["/".join(parts)] = member
        missing = [name for name in UPDATE_REQUIRED if name not in incoming]
        if missing:
            raise RuntimeError(f"Archive is missing {', '.join(missing)}; refusing to install it")

        snapshot = snapshot_manager_files(list(incoming))
        written = []
        for relative, member in sorted(incoming.items()):
            destination = BASE_DIR.joinpath(*relative.split("/"))
            destination.parent.mkdir(parents=True, exist_ok=True)
            payload = bundle.read(member)
            if destination.exists() and destination.read_bytes() == payload:
                continue
            destination.write_bytes(payload)
            written.append(relative)

    state.update({"installed": sha, "last_check": datetime.now().isoformat(timespec="seconds")})
    if isinstance(state.get("latest"), dict) and state["latest"].get("sha") == sha:
        state["latest"].update({"update_available": False, "behind": [], "count": 0})
    save_update_state(state)
    restart_flags["pending"] = True
    return {"sha": sha, "short": sha[:7], "ref": ref, "files": written, "snapshot": snapshot.name}

def restore_manager_snapshot() -> dict:
    archives = sorted(UPDATE_SNAPSHOTS.glob("manager_*.zip"), key=lambda f: f.stat().st_mtime, reverse=True)
    if not archives:
        raise RuntimeError("No snapshot to roll back to")
    restored = []
    with zipfile.ZipFile(archives[0]) as bundle:
        for member in bundle.infolist():
            if member.is_dir() or is_protected(member.filename):
                continue
            parts = sanitize_relative_parts(member.filename)
            if not parts:
                continue
            destination = BASE_DIR.joinpath(*parts)
            destination.parent.mkdir(parents=True, exist_ok=True)
            destination.write_bytes(bundle.read(member))
            restored.append("/".join(parts))
    state = load_update_state()
    state["installed"] = None
    save_update_state(state)
    restart_flags["pending"] = True
    return {"snapshot": archives[0].name, "files": restored}

@app.route("/api/manager/update")
def api_manager_update():
    state = load_update_state()
    return jsonify({
        "ok": True,
        "installed": installed_commit(state),
        "auto_check": state["auto_check"],
        "auto_install": state["auto_install"],
        "channel": state["channel"],
        "last_check": state["last_check"],
        "latest": state["latest"],
        "repo": UPDATE_REPO,
        "branch": UPDATE_BRANCH,
        "can_restart": supervised(),
        "restart_pending": restart_flags["pending"]
    })

@app.route("/api/manager/update/settings", methods=["POST"])
def api_manager_update_settings():
    denied = require_owner() or require_admin()
    if denied:
        return denied
    data = request.get_json(silent=True) or {}
    state = load_update_state()
    state["auto_check"] = bool(data.get("auto_check"))
    state["auto_install"] = bool(data.get("auto_install"))
    if data.get("channel") in CHANNELS:
        if data["channel"] != state["channel"]:
            state["latest"] = None  # the old answer described another channel
        state["channel"] = data["channel"]
    save_update_state(state)
    return jsonify({"ok": True, "auto_check": state["auto_check"], "auto_install": state["auto_install"], "channel": state["channel"]})

@app.route("/api/manager/update/check", methods=["POST"])
def api_manager_update_check():
    state = load_update_state()
    try:
        latest = fetch_update_status(state)
    except (requests.RequestException, RuntimeError, KeyError) as exc:
        return jsonify({"ok": False, "error": f"Could not reach GitHub: {exc}"}), 502
    state["latest"] = latest
    state["last_check"] = datetime.now().isoformat(timespec="seconds")
    save_update_state(state)
    return jsonify({"ok": True, "latest": latest, "installed": installed_commit(state), "last_check": state["last_check"]})

@app.route("/api/manager/update/apply", methods=["POST"])
def api_manager_update_apply():
    denied = require_owner() or require_admin()
    if denied:
        return denied
    if any(is_running(folder.name) for folder in SERVERS_DIR.iterdir() if folder.is_dir()):
        if not (request.get_json(silent=True) or {}).get("force"):
            return jsonify({"ok": False, "error": "A server is running. Stop it first, or send force to update anyway."}), 409
    try:
        result = apply_manager_update()
    except (requests.RequestException, RuntimeError, zipfile.BadZipFile, OSError) as exc:
        return jsonify({"ok": False, "error": f"Update failed: {exc}"}), 500
    notify("manager_update", f"Installed **{result['ref']}** ({result['short']}), {len(result['files'])} file(s) changed.")
    return jsonify({"ok": True, **result, "restart_required": True, "can_restart": supervised()})

@app.route("/api/manager/update/rollback", methods=["POST"])
def api_manager_update_rollback():
    denied = require_owner() or require_admin()
    if denied:
        return denied
    try:
        result = restore_manager_snapshot()
    except (RuntimeError, zipfile.BadZipFile, OSError) as exc:
        return jsonify({"ok": False, "error": str(exc)}), 500
    return jsonify({"ok": True, **result, "restart_required": True, "can_restart": supervised()})

def check_manager_update():
    state = load_update_state()
    if not state.get("auto_check"):
        return
    last = state.get("last_check")
    if last:
        try:
            if (datetime.now() - datetime.fromisoformat(last)).total_seconds() < UPDATE_CHECK_INTERVAL:
                return
        except ValueError:
            pass
    try:
        latest = fetch_update_status(state)
    except (requests.RequestException, RuntimeError, KeyError) as exc:
        print("Manager update check failed:", exc)
        return
    state["latest"] = latest
    state["last_check"] = datetime.now().isoformat(timespec="seconds")
    save_update_state(state)
    # Unattended installs only ever follow published releases; a bad commit on main must not reach the panel by itself.
    if latest.get("update_available") and state.get("auto_install") and state.get("channel") == "releases" and latest.get("release"):
        try:
            result = apply_manager_update()
            restart_flags["auto"] = True  # nobody is waiting on this install, so the manager may restart itself when idle
            notify("manager_update", f"Automatically installed **{result['ref']}**. The manager restarts when no server is running.")
            print(f"Manager updated to {result['short']}; it restarts once no server is running")
        except (requests.RequestException, RuntimeError, zipfile.BadZipFile, OSError) as exc:
            print("Manager auto-update failed:", exc)
