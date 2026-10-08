"""Per-server tools: rename, clone, launch settings, playtime, recent log and crash reports."""

import shutil
import time
from datetime import datetime

from flask import jsonify, request

from . import app
from .javatools import installed_javas, required_java_major
from .procs import AIKAR_FLAGS, launch_settings, parse_custom_flags
from .rconmap import write_properties
from .state import joined_at
from .store import get_server_path, is_running, list_servers, load_meta, load_playtime, save_meta
from .util import clamp_ram, folder_size, forget_size, unique_server_id
from .config import MAX_RAM_MB, MIN_RAM_MB, SERVERS_DIR

CLONE_SKIP = {"logs", "crash-reports", "cache", "debug", "session.lock", "manager_playtime.json"}
CLONE_META_DROP = ("playit_secret", "playit_executable", "rcon", "last_auto_backup", "last_auto_backup_at",
                   "last_update_check", "last_update_check_at", "imported")
MIN_FREE_AFTER_CLONE = 1024 * 1024 * 1024


def clean_name(value) -> str | None:
    name = str(value or "").strip()
    if not name or len(name) > 40 or any(ord(ch) < 32 for ch in name):
        return None
    return name


@app.route("/api/server/<sid>/rename", methods=["POST"])
def api_server_rename(sid):
    meta = load_meta(sid)
    if not meta:
        return jsonify({"ok": False, "error": "Server not found"}), 404
    name = clean_name((request.get_json(silent=True) or {}).get("name"))
    if not name:
        return jsonify({"ok": False, "error": "Use 1 to 40 characters for the name"}), 400
    meta["name"] = name
    save_meta(sid, meta)
    return jsonify({"ok": True, "name": name})


@app.route("/api/server/<sid>/launch", methods=["GET", "POST"])
def api_launch(sid):
    meta = load_meta(sid)
    if not meta:
        return jsonify({"ok": False, "error": "Server not found"}), 404
    if request.method == "POST":
        data = request.get_json(silent=True) or {}
        mode = data.get("flags", "default")
        if mode not in ("default", "optimized", "custom"):
            return jsonify({"ok": False, "error": "Unknown flag mode"}), 400
        flags, problem = parse_custom_flags(data.get("custom_flags", ""))
        if problem:
            return jsonify({"ok": False, "error": problem}), 400
        try:
            ram = int(data.get("ram", meta.get("ram", 2048)))
        except (TypeError, ValueError):
            return jsonify({"ok": False, "error": "Memory must be a number"}), 400
        if not MIN_RAM_MB <= ram <= MAX_RAM_MB:
            return jsonify({"ok": False, "error": f"Memory must be between {MIN_RAM_MB} and {MAX_RAM_MB} MB"}), 400
        java = str(data.get("java") or "")
        if java:
            majors = {major for _, major in installed_javas()}
            needed = required_java_major(meta.get("version")) or 0
            if not java.isdigit() or int(java) not in majors:
                return jsonify({"ok": False, "error": "That Java is not installed on this PC"}), 400
            if int(java) < needed:
                return jsonify({"ok": False, "error": f"Minecraft {meta.get('version')} needs Java {needed} or newer"}), 400
        meta["ram"] = clamp_ram(ram)
        meta["launch"] = {"flags": mode, "custom_flags": " ".join(flags), "java": java}
        save_meta(sid, meta)
    needed = required_java_major(meta.get("version")) or 0
    javas = sorted({major for _, major in installed_javas() if major >= needed})
    return jsonify({"ok": True, "ram": int(meta.get("ram", 2048)), **launch_settings(meta), "javas": javas, "java_required": needed or None,
                    "aikar": " ".join(AIKAR_FLAGS), "running": is_running(sid),
                    "restart_required": request.method == "POST" and is_running(sid)})


def next_free_port(start: int) -> int:
    used = {int(s.get("port") or 0) for s in list_servers()}
    port = max(1024, start + 1)
    while port in used and port < 65535:
        port += 1
    return port


@app.route("/api/server/<sid>/clone", methods=["POST"])
def api_server_clone(sid):
    source = get_server_path(sid)
    meta = load_meta(sid)
    if not meta:
        return jsonify({"ok": False, "error": "Server not found"}), 404
    if is_running(sid):
        return jsonify({"ok": False, "error": "Stop the server first so its world is copied in a consistent state"}), 409
    name = clean_name((request.get_json(silent=True) or {}).get("name") or f"{meta.get('name', sid)} (copy)")
    if not name:
        return jsonify({"ok": False, "error": "Use 1 to 40 characters for the name"}), 400
    size = folder_size(source, ttl=0)
    free = shutil.disk_usage(SERVERS_DIR).free
    if free < size + MIN_FREE_AFTER_CLONE:
        return jsonify({"ok": False, "error": f"Not enough disk space: the copy needs {size // 1048576} MB and 1 GB must stay free"}), 507
    new_id = unique_server_id(name)
    target = get_server_path(new_id)
    if target.exists():
        return jsonify({"ok": False, "error": "A server with that name was just created. Wait a second and try again."}), 409
    try:
        shutil.copytree(source, target, ignore=shutil.ignore_patterns(*CLONE_SKIP))
    except OSError as exc:
        shutil.rmtree(target, ignore_errors=True)
        return jsonify({"ok": False, "error": f"Copy failed: {exc}"}), 500
    port = next_free_port(int(meta.get("port") or 25565))
    copy = {key: value for key, value in meta.items() if key not in CLONE_META_DROP}
    copy.update(name=name, port=port, created=datetime.now().isoformat())
    save_meta(new_id, copy)
    # The copy must not fight the original for the game port or the RCON port.
    write_properties(target / "server.properties", {"server-port": port, "enable-rcon": "false", "rcon.password": ""})
    forget_size(target)
    return jsonify({"ok": True, "id": new_id, "name": name, "port": port})


@app.route("/api/server/<sid>/playtime")
def api_playtime(sid):
    if not get_server_path(sid).is_dir():
        return jsonify({"ok": False, "error": "Server not found"}), 404
    totals = {name: dict(entry) for name, entry in load_playtime(sid).items()}
    now = time.time()
    for name, joined in joined_at.get(sid, {}).items():  # sessions still running count too
        entry = totals.setdefault(name, {"seconds": 0, "sessions": 0})
        entry["seconds"] = int(entry["seconds"] + now - joined)
        entry["online"] = True
    ranked = sorted(({"name": name, **entry} for name, entry in totals.items()), key=lambda item: item["seconds"], reverse=True)
    return jsonify({"ok": True, "players": ranked[:25]})


@app.route("/api/server/<sid>/logs/latest")
def api_logs_latest(sid):
    path = get_server_path(sid) / "logs" / "latest.log"
    limit = max(10, min(2000, request.args.get("lines", 300, type=int)))
    try:
        with open(path, "rb") as handle:
            handle.seek(0, 2)
            size = handle.tell()
            handle.seek(max(0, size - 1024 * 1024))
            lines = handle.read().decode("utf-8", errors="replace").splitlines()[-limit:]
    except OSError:
        lines = []
    return jsonify({"ok": True, "lines": lines})


@app.route("/api/server/<sid>/crash-reports")
def api_crash_reports(sid):
    folder = get_server_path(sid) / "crash-reports"
    reports = []
    if folder.is_dir():
        for file in folder.iterdir():
            if file.is_file() and file.suffix.lower() == ".txt":
                stat = file.stat()
                reports.append({"name": file.name, "path": f"crash-reports/{file.name}", "size": stat.st_size,
                                "modified": datetime.fromtimestamp(stat.st_mtime).isoformat(timespec="seconds")})
    reports.sort(key=lambda item: item["modified"], reverse=True)
    return jsonify({"ok": True, "reports": reports[:30]})
