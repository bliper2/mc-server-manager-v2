"""Operations: health check, diagnostics, the manager log and disk usage."""

import logging
import os
import platform
import shutil
import sys
import threading
import time
from logging.handlers import RotatingFileHandler

from flask import jsonify, request

from . import app
from .auth import require_owner
from .config import BACKUPS_DIR, DATA_DIR, DEV_MODE, LOG_FILE, PORT, SERVERS_DIR, VERSION
from .javatools import java_status
from .lifecycle import STARTED_AT, running_server_ids, supervised
from .notify import public_settings
from .state import restart_flags
from .store import list_servers
from .updater import load_update_state
from .util import folder_size, psutil

LOG_FORMAT = "%(asctime)s %(levelname)s %(name)s: %(message)s"
LOW_DISK_MB = 5 * 1024


def setup_logging():
    """Errors and warnings go to manager.log (1 MB x 3) as well as the terminal, so a crash can be read afterwards."""
    handler_name = "mcm-file"
    for logger in (app.logger, logging.getLogger("waitress"), logging.getLogger("werkzeug")):
        if any(getattr(h, "name", "") == handler_name for h in logger.handlers):
            continue
        try:
            handler = RotatingFileHandler(LOG_FILE, maxBytes=1_000_000, backupCount=3, encoding="utf-8")
        except OSError:
            return
        handler.name = handler_name
        handler.setLevel(logging.INFO)
        handler.setFormatter(logging.Formatter(LOG_FORMAT))
        logger.addHandler(handler)
        if logger.level in (logging.NOTSET, logging.WARNING):
            logger.setLevel(logging.INFO)


setup_logging()


def disk_free_mb() -> dict:
    try:
        usage = shutil.disk_usage(DATA_DIR)
    except OSError:
        return {"free_mb": None, "total_mb": None}
    return {"free_mb": usage.free // 1048576, "total_mb": usage.total // 1048576}


@app.route("/api/health")
def api_health():
    """Public and minimal, for uptime monitors."""
    return jsonify({"ok": True, "version": VERSION, "uptime": int(time.time() - STARTED_AT)})


@app.route("/api/disk")
def api_disk():
    servers, backups = {}, {}
    for server in list_servers():
        sid = server["id"]
        servers[sid] = folder_size(SERVERS_DIR / sid) // 1048576
        backups[sid] = folder_size(BACKUPS_DIR / sid) // 1048576
    return jsonify({"ok": True, **disk_free_mb(), "servers": servers, "backups": backups, "low": (disk_free_mb()["free_mb"] or LOW_DISK_MB + 1) < LOW_DISK_MB})


@app.route("/api/manager/log")
def api_manager_log():
    denied = require_owner()
    if denied:
        return denied
    limit = max(10, min(1000, request.args.get("lines", 200, type=int)))
    try:
        with open(LOG_FILE, "rb") as handle:
            handle.seek(0, os.SEEK_END)
            size = handle.tell()
            handle.seek(max(0, size - 256 * 1024))
            tail = handle.read().decode("utf-8", errors="replace").splitlines()[-limit:]
    except OSError:
        tail = []
    return jsonify({"ok": True, "lines": tail})


@app.route("/api/diagnostics")
def api_diagnostics():
    denied = require_owner()
    if denied:
        return denied
    java = java_status()
    update = load_update_state()
    return jsonify({
        "ok": True,
        "version": VERSION,
        "python": sys.version.split()[0],
        "platform": platform.platform(),
        "data_dir": str(DATA_DIR),
        "port": PORT,
        "dev_mode": DEV_MODE,
        "can_restart": supervised(),
        "uptime": int(time.time() - STARTED_AT),
        "threads": threading.active_count(),
        "psutil": psutil is not None,
        "java": {"found": java["found"], "major": java["major"], "path": java["path"]},
        "servers": len(list_servers()),
        "running": len(running_server_ids()),
        "update": {"installed": (update.get("installed") or "")[:7], "channel": update.get("channel"), "pending_restart": restart_flags["pending"]},
        "notifications": public_settings()["configured"],
        "log_bytes": LOG_FILE.stat().st_size if LOG_FILE.exists() else 0,
        **disk_free_mb(),
    })
