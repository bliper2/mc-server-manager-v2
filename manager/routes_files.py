"""File manager, properties, plugin and mod routes."""

import shutil
from datetime import datetime
from flask import abort, jsonify, request, send_from_directory
from werkzeug.utils import secure_filename

from . import app
from .auth import current_account
from .backups import auto_update_settings
from .providers import apply_plugin_update, download_url_bytes, modrinth_search, modrinth_versions, scan_plugin_updates
from .state import update_cache
from .store import get_server_path, load_meta, save_meta
from .util import DOWNLOAD_FOLDERS, has_line_break, is_trusted_download, safe_path

@app.route("/api/server/<sid>/fs/list")
def api_fs_list(sid):
    rel = request.args.get("path", "").lstrip("/")
    target = safe_path(sid, rel)
    if target is None or not target.exists():
        return jsonify({"ok": False, "error": "Invalid path"}), 400
    items = []
    if target.is_dir():
        for p in sorted(target.iterdir(), key=lambda x: (not x.is_dir(), x.name.lower())):
            items.append({
                "name": p.name, "is_dir": p.is_dir(),
                "size": p.stat().st_size if p.is_file() else 0,
                "modified": datetime.fromtimestamp(p.stat().st_mtime).isoformat(timespec="seconds")
            })
    return jsonify({"ok": True, "path": rel, "items": items})

PRIVATE_FILES = {"manager_meta.json"}  # holds the Playit secret and RCON password; owner only

def is_private(target) -> bool:
    account = current_account()
    return bool(target and target.name in PRIVATE_FILES and not (account and account["role"] == "owner"))

@app.route("/api/server/<sid>/fs/read")
def api_fs_read(sid):
    rel = request.args.get("path", "").lstrip("/")
    target = safe_path(sid, rel)
    if is_private(target):
        return jsonify({"ok": False, "error": "Only the owner can open this file"}), 403
    if target is None or not target.is_file():
        return jsonify({"ok": False, "error": "Not a file"}), 400
    if target.stat().st_size > 2 * 1024 * 1024:
        return jsonify({"ok": False, "error": "File too large (>2MB)"}), 400
    try:
        return jsonify({"ok": True, "content": target.read_text(encoding="utf-8"), "path": rel})
    except UnicodeDecodeError:
        return jsonify({"ok": False, "error": "Binary file"}), 400

@app.route("/api/server/<sid>/fs/write", methods=["POST"])
def api_fs_write(sid):
    data = request.json or {}
    rel = (data.get("path") or "").lstrip("/")
    content = data.get("content")
    if content is None:
        return jsonify({"ok": False, "error": "No content"}), 400
    target = safe_path(sid, rel)
    if target is None:
        return jsonify({"ok": False, "error": "Invalid path"}), 400
    if is_private(target):
        return jsonify({"ok": False, "error": "Only the owner can change this file"}), 403
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(content, encoding="utf-8")
    return jsonify({"ok": True})

@app.route("/api/server/<sid>/fs/delete", methods=["POST"])
def api_fs_delete(sid):
    data = request.json or {}
    rel = (data.get("path") or "").lstrip("/")
    if not rel or rel in (".", "manager_meta.json"):
        return jsonify({"ok": False, "error": "Cannot delete"}), 400
    target = safe_path(sid, rel)
    if target is None or not target.exists():
        return jsonify({"ok": False, "error": "Not found"}), 404
    if target.is_dir():
        shutil.rmtree(target)
    else:
        target.unlink()
    return jsonify({"ok": True})

@app.route("/api/server/<sid>/fs/mkdir", methods=["POST"])
def api_fs_mkdir(sid):
    data = request.json or {}
    rel = (data.get("path") or "").lstrip("/")
    target = safe_path(sid, rel)
    if target is None:
        return jsonify({"ok": False, "error": "Invalid"}), 400
    target.mkdir(parents=True, exist_ok=True)
    return jsonify({"ok": True})

@app.route("/api/server/<sid>/fs/upload", methods=["POST"])
def api_fs_upload(sid):
    rel = (request.form.get("path") or "").lstrip("/")
    folder = safe_path(sid, rel)
    if folder is None:
        return jsonify({"ok": False, "error": "Invalid path"}), 400
    if not folder.exists():
        folder.mkdir(parents=True, exist_ok=True)
    if "file" not in request.files:
        return jsonify({"ok": False, "error": "No file"}), 400
    f = request.files["file"]
    if not f.filename:
        return jsonify({"ok": False, "error": "Empty filename"}), 400
    name = secure_filename(f.filename)
    f.save(str(folder / name))
    return jsonify({"ok": True, "name": name})

@app.route("/api/server/<sid>/fs/download")
def api_fs_download(sid):
    rel = request.args.get("path", "").lstrip("/")
    target = safe_path(sid, rel)
    if target is None or not target.is_file() or is_private(target):
        abort(404)
    return send_from_directory(target.parent, target.name, as_attachment=True)

@app.route("/api/server/<sid>/properties")
def api_props_get(sid):
    path = get_server_path(sid) / "server.properties"
    if not path.exists():
        return jsonify({"ok": True, "props": {}})
    props = {}
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        k, v = line.split("=", 1)
        props[k.strip()] = v.strip()
    return jsonify({"ok": True, "props": props, "raw": path.read_text(encoding="utf-8")})

@app.route("/api/server/<sid>/properties", methods=["POST"])
def api_props_set(sid):
    data = request.json or {}
    props = data.get("props") or {}
    path = get_server_path(sid) / "server.properties"
    if not isinstance(props, dict):
        return jsonify({"ok": False, "error": "Properties must be an object"}), 400
    lines = ["# Edited by MC Server Manager", ""]
    for k, v in props.items():
        if has_line_break(k, v) or "=" in str(k):
            return jsonify({"ok": False, "error": f"Invalid property \"{k}\""}), 400
        lines.append(f"{k}={v}")
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    if "server-port" in props:
        meta = load_meta(sid)
        try:
            meta["port"] = int(props["server-port"])
            save_meta(sid, meta)
        except Exception:
            pass
    return jsonify({"ok": True})

@app.route("/api/server/<sid>/properties/raw", methods=["POST"])
def api_props_raw_set(sid):
    raw = (request.json or {}).get("raw")
    if not isinstance(raw, str):
        return jsonify({"ok": False, "error": "Raw properties must be text"}), 400
    path = get_server_path(sid) / "server.properties"
    path.write_text(raw if raw.endswith("\n") else raw + "\n", encoding="utf-8")
    for line in raw.splitlines():
        if line.strip().startswith("server-port="):
            try:
                meta = load_meta(sid)
                meta["port"] = int(line.split("=", 1)[1].strip())
                save_meta(sid, meta)
            except ValueError:
                pass
            break
    return jsonify({"ok": True})

@app.route("/api/modrinth/search")
def api_modrinth_search():
    return jsonify(modrinth_search(
        request.args.get("q", ""),
        request.args.get("type", "plugin"),
        limit=request.args.get("limit", 24, type=int),
        offset=request.args.get("offset", 0, type=int),
        game_version=request.args.get("version"),
        loader=request.args.get("loader")
    ))

@app.route("/api/modrinth/featured")
def api_modrinth_featured():
    return jsonify(modrinth_search(
        "",
        request.args.get("type", "plugin"),
        limit=30,
        offset=request.args.get("offset", 0, type=int),
        game_version=request.args.get("version"),
        loader=request.args.get("loader"),
        index="downloads"
    ))

@app.route("/api/modrinth/versions/<pid>")
def api_modrinth_versions(pid):
    return jsonify(modrinth_versions(pid, request.args.get("version"), request.args.get("loader")))

@app.route("/api/server/<sid>/install", methods=["POST"])
def api_install(sid):
    data = request.json or {}
    url = data.get("url")
    filename = data.get("filename") or "download.jar"
    target = data.get("target") or "plugins"
    if not url:
        return jsonify({"ok": False, "error": "No URL"}), 400
    if target not in DOWNLOAD_FOLDERS:
        return jsonify({"ok": False, "error": "Target must be plugins or mods"}), 400
    if not is_trusted_download(url):
        return jsonify({"ok": False, "error": "Downloads are only allowed from cdn.modrinth.com"}), 400
    path = get_server_path(sid)
    if not path.exists():
        return jsonify({"ok": False, "error": "Server missing"}), 404
    safe_name = secure_filename(filename)
    if not safe_name.lower().endswith(".jar"):
        return jsonify({"ok": False, "error": "Only .jar files can be installed"}), 400
    folder = path / target
    folder.mkdir(exist_ok=True)
    content = download_url_bytes(url)
    if not content:
        return jsonify({"ok": False, "error": "Download failed"}), 500
    dest = folder / safe_name
    dest.write_bytes(content)
    return jsonify({"ok": True, "path": str(dest.relative_to(path))})

@app.route("/api/server/<sid>/files")
def api_files(sid):
    folder = request.args.get("folder", "plugins")
    if folder not in DOWNLOAD_FOLDERS:
        return jsonify([])
    path = get_server_path(sid) / folder
    if not path.exists():
        return jsonify([])
    files = []
    for f in path.iterdir():
        if f.is_file() and f.suffix.lower() == ".jar":
            files.append({"name": f.name, "size": f.stat().st_size, "modified": datetime.fromtimestamp(f.stat().st_mtime).isoformat()})
    return jsonify(files)

@app.route("/api/server/<sid>/plugin-configs")
def api_plugin_configs(sid):
    root = get_server_path(sid) / "plugins"
    if not root.exists():
        return jsonify([])
    configs = []
    for path in root.rglob("*"):
        if path.is_file() and path.suffix.lower() in (".yml", ".yaml", ".json"):
            configs.append({"path": str(path.relative_to(get_server_path(sid))).replace("\\", "/"), "size": path.stat().st_size})
    return jsonify(sorted(configs, key=lambda item: item["path"].lower()))

@app.route("/api/server/<sid>/auto-update", methods=["GET", "POST"])
def api_auto_update(sid):
    if not get_server_path(sid).exists():
        return jsonify({"ok": False, "error": "Server not found"}), 404
    meta = load_meta(sid)
    if request.method == "POST":
        data = request.json or {}
        try:
            interval = max(1, min(168, int(data.get("interval_hours", 12))))
        except (TypeError, ValueError):
            return jsonify({"ok": False, "error": "Interval must be a number"}), 400
        meta["auto_update"] = {
            "enabled": bool(data.get("enabled")),
            "interval_hours": interval,
            "install": bool(data.get("install"))
        }
        save_meta(sid, meta)
        return jsonify({"ok": True, "settings": meta["auto_update"]})
    return jsonify({"ok": True, "settings": auto_update_settings(meta), "last_check": meta.get("last_update_check")})

@app.route("/api/server/<sid>/updates/check")
def api_updates_check(sid):
    if not get_server_path(sid).exists():
        return jsonify({"ok": False, "error": "Server not found"}), 404
    items = scan_plugin_updates(sid)
    meta = load_meta(sid)
    meta["last_update_check"] = datetime.now().isoformat(timespec="seconds")
    save_meta(sid, meta)
    return jsonify({"ok": True, "items": items, "checked": meta["last_update_check"]})

@app.route("/api/server/<sid>/updates/apply", methods=["POST"])
def api_updates_apply(sid):
    if not get_server_path(sid).exists():
        return jsonify({"ok": False, "error": "Server not found"}), 404
    data = request.json or {}
    targets = data.get("items")
    if not isinstance(targets, list) or not targets:
        cached = update_cache.get(sid, {}).get("items", [])
        targets = [item for item in cached if item.get("update_available") and item.get("download_url")]
    applied, failed = [], []
    for item in targets:
        folder, filename, url = item.get("folder"), item.get("file"), item.get("download_url")
        if not (folder and filename and url):
            continue
        ok, result = apply_plugin_update(sid, folder, filename, url, item.get("download_filename"))
        (applied if ok else failed).append({"file": filename, "detail": result})
    return jsonify({"ok": not failed, "applied": applied, "failed": failed})
