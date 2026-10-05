"""Backup, restore and scheduled-backup logic and routes."""

import json
import shutil
import threading
import time
import zipfile
from datetime import datetime
from pathlib import Path
from flask import abort, jsonify, request, send_from_directory

from . import app
from .config import BACKUPS_DIR, BACKUP_SKIP_DIRS, BACKUP_SKIP_NAMES
from .notify import notify
from .procs import send_command
from .state import backup_jobs
from .store import get_server_path, is_running, load_meta, save_meta
from .util import sanitize_relative_parts

def backup_dir(server_id: str) -> Path:
    return BACKUPS_DIR / server_id

def backup_entry(archive: Path) -> dict:
    sidecar = archive.with_suffix(".json")
    info = {}
    if sidecar.exists():
        try:
            info = json.loads(sidecar.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            info = {}
    return {
        "name": archive.name,
        "label": info.get("label", ""),
        "size": archive.stat().st_size,
        "files": info.get("files", 0),
        "world": info.get("world", True),
        "automatic": info.get("automatic", False),
        "created": info.get("created") or datetime.fromtimestamp(archive.stat().st_mtime).isoformat(timespec="seconds")
    }

def list_backups(server_id: str) -> list:
    entries = [backup_entry(archive) for archive in backup_dir(server_id).glob("*.zip")]
    return sorted(entries, key=lambda item: item["created"], reverse=True)

def resolve_backup(server_id: str, name: str):
    if Path(name).name != name or not name.endswith(".zip"):
        return None
    archive = backup_dir(server_id) / name
    return archive if archive.exists() else None

def is_world_folder(path: Path) -> bool:
    return path.is_dir() and (path.name.startswith("world") or (path / "level.dat").exists())

def collect_backup_files(root: Path, include_world: bool) -> list:
    files = []
    for entry in sorted(root.iterdir(), key=lambda item: item.name.lower()):
        if entry.name in BACKUP_SKIP_NAMES:
            continue
        if entry.is_dir():
            if entry.name in BACKUP_SKIP_DIRS or (not include_world and is_world_folder(entry)):
                continue
            files.extend(child for child in entry.rglob("*") if child.is_file() and child.name not in BACKUP_SKIP_NAMES)
        elif entry.is_file():
            files.append(entry)
    return files

def prune_backups(server_id: str, keep: int):
    if keep <= 0:
        return
    archives = sorted(backup_dir(server_id).glob("*.zip"), key=lambda item: item.stat().st_mtime, reverse=True)
    for stale in archives[keep:]:
        stale.with_suffix(".json").unlink(missing_ok=True)
        stale.unlink(missing_ok=True)

def set_job(server_id: str, state: str, message: str, progress: int = 0, **extra):
    backup_jobs[server_id] = {"state": state, "message": message, "progress": progress, "updated": time.time(), **extra}

def next_backup_path(server_id: str) -> Path:
    folder = backup_dir(server_id)
    folder.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    archive = folder / f"backup_{stamp}.zip"
    suffix = 2
    while archive.exists():
        archive = folder / f"backup_{stamp}-{suffix}.zip"
        suffix += 1
    return archive

def run_backup(server_id: str, label: str, include_world: bool, keep: int, automatic: bool = False):
    root = get_server_path(server_id)
    archive = next_backup_path(server_id)
    try:
        set_job(server_id, "running", "Collecting files...", 0)
        files = collect_backup_files(root, include_world)
        total = len(files) or 1
        with zipfile.ZipFile(archive, "w", zipfile.ZIP_DEFLATED, compresslevel=6) as bundle:
            for index, source in enumerate(files, start=1):
                try:
                    bundle.write(source, source.relative_to(root).as_posix())
                except (OSError, ValueError):
                    continue
                if index % 25 == 0 or index == total:
                    set_job(server_id, "running", f"Archiving {index} of {total} files", int(index / total * 100))
        archive.with_suffix(".json").write_text(json.dumps({
            "label": label,
            "created": datetime.now().isoformat(timespec="seconds"),
            "files": len(files),
            "world": include_world,
            "automatic": automatic
        }, indent=2), encoding="utf-8")
        prune_backups(server_id, keep)
        set_job(server_id, "done", f"Backup saved ({len(files)} files)", 100, backup=archive.name)
        return archive
    except Exception as exc:
        archive.unlink(missing_ok=True)
        set_job(server_id, "error", f"Backup failed: {exc}", 0)
        return None

def run_backup_task(server_id: str, label: str, include_world: bool, keep: int, automatic: bool = False):
    live = is_running(server_id)
    if live:
        send_command(server_id, "save-off")
        send_command(server_id, "save-all flush")
        time.sleep(2)
    try:
        archive = run_backup(server_id, label, include_world, keep, automatic=automatic)
    finally:
        if live:
            send_command(server_id, "save-on")
    if archive:
        size = archive.stat().st_size / 1048576
        notify("backup_done", f"{'Automatic' if automatic else 'Manual'} backup saved ({size:.1f} MB).", server_id)
    else:
        notify("backup_failed", backup_jobs.get(server_id, {}).get("message", "Backup failed"), server_id)

def auto_backup_settings(meta: dict) -> dict:
    stored = meta.get("auto_backup") if isinstance(meta.get("auto_backup"), dict) else {}
    return {
        "enabled": bool(stored.get("enabled")),
        "interval_hours": max(1, min(168, int(stored.get("interval_hours", 6) or 6))),
        "world": stored.get("world", True) is not False
    }

def auto_update_settings(meta: dict) -> dict:
    stored = meta.get("auto_update") if isinstance(meta.get("auto_update"), dict) else {}
    return {
        "enabled": bool(stored.get("enabled")),
        "interval_hours": max(1, min(168, int(stored.get("interval_hours", 12) or 12))),
        "install": bool(stored.get("install"))
    }

def run_restore(server_id: str, archive_name: str, safety: bool, keep: int):
    root = get_server_path(server_id)
    archive = backup_dir(server_id) / archive_name
    try:
        if safety:
            # keep=0 so pruning can never delete the very archive being restored, and a failed
            # safety copy aborts before anything is wiped.
            if run_backup(server_id, "Automatic copy taken before a restore", True, 0, automatic=True) is None:
                set_job(server_id, "error", "Restore cancelled: could not save a safety copy of the current files", 0)
                return False
        if not archive.exists():
            set_job(server_id, "error", "Restore failed: that backup no longer exists", 0)
            return False
        set_job(server_id, "running", "Clearing current server files...", 30)
        for entry in root.iterdir():
            if entry.is_dir():
                shutil.rmtree(entry, ignore_errors=True)
            else:
                entry.unlink(missing_ok=True)
        set_job(server_id, "running", "Extracting backup...", 45)
        with zipfile.ZipFile(archive) as bundle:
            members = [member for member in bundle.infolist() if not member.is_dir()]
            total = len(members) or 1
            for index, member in enumerate(members, start=1):
                parts = sanitize_relative_parts(member.filename)
                if not parts:
                    continue
                destination = root.joinpath(*parts)
                destination.parent.mkdir(parents=True, exist_ok=True)
                with bundle.open(member) as source, open(destination, "wb") as target:
                    shutil.copyfileobj(source, target)
                if index % 25 == 0 or index == total:
                    set_job(server_id, "running", f"Restoring {index} of {total} files", 45 + int(index / total * 55))
        set_job(server_id, "done", "Backup restored", 100)
        return True
    except Exception as exc:
        set_job(server_id, "error", f"Restore failed: {exc}", 0)
        return False

def start_job(server_id: str, worker, *args):
    current = backup_jobs.get(server_id)
    if current and current.get("state") == "running":
        return False, "A backup task is already running for this server"
    set_job(server_id, "running", "Starting...", 0)
    threading.Thread(target=worker, args=(server_id, *args), daemon=True).start()
    return True, "Task started"

def backup_keep_value(data: dict, server_id: str) -> int:
    fallback = int(load_meta(server_id).get("backup_keep", 10) or 10)
    try:
        return max(0, min(50, int(data.get("keep", fallback))))
    except (TypeError, ValueError):
        return fallback

@app.route("/api/server/<sid>/backups")
def api_backups(sid):
    if not get_server_path(sid).exists():
        return jsonify({"ok": False, "error": "Server not found"}), 404
    return jsonify({
        "ok": True,
        "backups": list_backups(sid),
        "keep": int(load_meta(sid).get("backup_keep", 10) or 10),
        "running": is_running(sid),
        "job": backup_jobs.get(sid)
    })

@app.route("/api/server/<sid>/backups/create", methods=["POST"])
def api_backup_create(sid):
    if not get_server_path(sid).exists():
        return jsonify({"ok": False, "error": "Server not found"}), 404
    data = request.json or {}
    keep = backup_keep_value(data, sid)
    meta = load_meta(sid)
    meta["backup_keep"] = keep
    save_meta(sid, meta)
    ok, message = start_job(sid, run_backup_task, str(data.get("label") or "").strip()[:80], data.get("world", True) is not False, keep)
    return jsonify({"ok": ok, "message": message})

@app.route("/api/server/<sid>/backups/job")
def api_backup_job(sid):
    return jsonify({"ok": True, "job": backup_jobs.get(sid)})

@app.route("/api/server/<sid>/backups/<name>/restore", methods=["POST"])
def api_backup_restore(sid, name):
    archive = resolve_backup(sid, name)
    if not archive:
        return jsonify({"ok": False, "error": "Backup not found"}), 404
    if is_running(sid):
        return jsonify({"ok": False, "error": "Stop the server before restoring a backup"}), 400
    data = request.json or {}
    ok, message = start_job(sid, run_restore, archive.name, data.get("safety", True) is not False, backup_keep_value(data, sid))
    return jsonify({"ok": ok, "message": message})

@app.route("/api/server/<sid>/backups/<name>/delete", methods=["POST"])
def api_backup_delete(sid, name):
    archive = resolve_backup(sid, name)
    if not archive:
        return jsonify({"ok": False, "error": "Backup not found"}), 404
    archive.with_suffix(".json").unlink(missing_ok=True)
    archive.unlink(missing_ok=True)
    return jsonify({"ok": True, "message": "Backup deleted"})

@app.route("/api/server/<sid>/backups/<name>/download")
def api_backup_download(sid, name):
    archive = resolve_backup(sid, name)
    if not archive:
        abort(404)
    return send_from_directory(archive.parent, archive.name, as_attachment=True)

@app.route("/api/server/<sid>/auto-backup", methods=["GET", "POST"])
def api_auto_backup(sid):
    if not get_server_path(sid).exists():
        return jsonify({"ok": False, "error": "Server not found"}), 404
    meta = load_meta(sid)
    if request.method == "POST":
        data = request.json or {}
        try:
            interval = max(1, min(168, int(data.get("interval_hours", 6))))
        except (TypeError, ValueError):
            return jsonify({"ok": False, "error": "Interval must be a number"}), 400
        meta["auto_backup"] = {
            "enabled": bool(data.get("enabled")),
            "interval_hours": interval,
            "world": data.get("world", True) is not False
        }
        save_meta(sid, meta)
        return jsonify({"ok": True, "settings": meta["auto_backup"]})
    return jsonify({"ok": True, "settings": auto_backup_settings(meta), "last_run": meta.get("last_auto_backup")})
