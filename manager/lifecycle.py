"""Restarting the manager itself, and bringing servers back afterwards.

The process exits with RESTART_EXIT_CODE; whatever started it (app.py's supervisor or the Werkzeug
reloader) starts it again in the same terminal. Browsers poll /api/boot and reload when `boot` changes."""

import json
import os
import secrets
import sys
import threading
import time

from flask import jsonify, request

from . import app
from .auth import current_account, require_owner
from .config import BASE_DIR, DEV_MODE, RESTART_EXIT_CODE, RESTART_STATE_FILE, VERSION
from .notify import notify
from .procs import start_server, stop_playit, stop_server
from .state import playit_processes, restart_flags, running_servers
from .store import audit, is_running, load_meta

BOOT_ID = secrets.token_hex(6)
STARTED_AT = time.time()
WATCHED_SUFFIXES = (".py", ".html", ".css", ".js")
_source_cache: dict = {}


def supervised() -> bool:
    """True when something will relaunch us after we exit with RESTART_EXIT_CODE."""
    return os.environ.get("MCM_CHILD") == "1" or os.environ.get("WERKZEUG_RUN_MAIN") == "true"


def source_version():
    """Newest modification time of the code and UI files. Only computed in dev mode, where files change under us."""
    if not DEV_MODE:
        return None
    cached = _source_cache.get("value")
    if cached and time.time() - cached[0] < 1.5:
        return cached[1]
    newest = 0
    for folder in ("manager", "templates", "static"):
        for path in (BASE_DIR / folder).rglob("*"):
            if path.suffix in WATCHED_SUFFIXES:
                try:
                    newest = max(newest, path.stat().st_mtime_ns)
                except OSError:
                    continue
    try:
        newest = max(newest, (BASE_DIR / "app.py").stat().st_mtime_ns)
    except OSError:
        pass
    _source_cache["value"] = (time.time(), newest)
    return newest


def running_server_ids() -> list:
    return [sid for sid in list(running_servers) if is_running(sid)]


def _shutdown_and_exit(resume: list):
    time.sleep(1.0)  # let the HTTP response that triggered this reach the browser
    threads = [threading.Thread(target=stop_server, args=(sid, False)) for sid in resume]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(timeout=60)
    for sid in list(playit_processes):
        stop_playit(sid)
    sys.stdout.flush()
    sys.stderr.flush()
    os._exit(RESTART_EXIT_CODE)


def shutdown_servers():
    """Registered with atexit in normal mode: Ctrl+C or closing the terminal saves and stops servers instead of orphaning them."""
    running = running_server_ids()
    if not running:
        return
    print(f"Stopping {len(running)} server(s) safely...", flush=True)
    threads = [threading.Thread(target=stop_server, args=(sid, False)) for sid in running]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(timeout=60)
    for sid in list(playit_processes):
        stop_playit(sid)

def begin_restart(by: str, reason: str = "restart") -> list:
    """Stop servers safely, remember which were running, then exit so the supervisor relaunches us.
    Returns the server ids that will be resumed."""
    resume = running_server_ids()
    if resume:
        RESTART_STATE_FILE.write_text(json.dumps({"resume": resume, "at": time.time()}), encoding="utf-8")
    audit(by, f"manager restart ({reason})", resume=",".join(resume))
    threading.Thread(target=_shutdown_and_exit, args=(resume,), daemon=True).start()
    return resume


def resume_servers():
    """After a restart, start whatever was running before it. The state file is removed first so a crash loop cannot repeat it."""
    try:
        state = json.loads(RESTART_STATE_FILE.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return
    try:
        RESTART_STATE_FILE.unlink()
    except OSError:
        pass
    if time.time() - float(state.get("at", 0)) > 600:
        return  # stale file from an unrelated shutdown
    for sid in state.get("resume", []):
        meta = load_meta(sid)
        if not meta:
            continue
        ok, message = start_server(sid, int(meta.get("ram", 2048) or 2048), automatic=True)
        audit("system", "resumed server after manager restart" if ok else f"could not resume server: {message}", server=sid)
    if state.get("resume"):
        notify("manager_update", f"Manager is back (v{VERSION}). Resumed {len(state['resume'])} server(s).")


@app.route("/api/boot")
def api_boot():
    """Public and tiny: lets any open page notice that the manager restarted or its files changed."""
    return jsonify({
        "boot": BOOT_ID,
        "version": VERSION,
        "dev": source_version(),
        "pending": restart_flags["pending"],
        "can_restart": supervised()
    })


@app.route("/api/manager/restart", methods=["POST"])
def api_manager_restart():
    denied = require_owner()
    if denied:
        return denied
    if not supervised():
        return jsonify({"ok": False, "error": "This copy was not started by start.bat or app.py, so it cannot restart itself. Close it and start it again."}), 409
    data = request.get_json(silent=True) or {}
    running = running_server_ids()
    if running and not data.get("stop_servers"):
        names = [load_meta(sid).get("name") or sid for sid in running]
        return jsonify({"ok": False, "needs_confirm": True, "servers": names,
                        "error": "Servers are running. They will be stopped safely and started again after the restart."}), 409
    begin_restart(current_account()["username"], "requested from the panel")
    return jsonify({"ok": True, "restarting": True, "resumes": len(running)})
