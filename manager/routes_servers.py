"""Server list, create, import, lifecycle, console and player routes."""

import json
import shutil
import struct
import time
import uuid
from datetime import datetime
from pathlib import Path

import requests
from flask import abort, jsonify, make_response, render_template, request, send_from_directory
from werkzeug.utils import secure_filename

from . import app
from .auth import account_permissions, current_account, is_local_request, needs_setup
from .config import ASSET_VERSION, BACKUPS_DIR, HEADERS, VERSION, IMPORTS_DIR, IMPORT_BATCH_BYTES, IMPORT_SESSION_TTL, MAX_RAM_MB, MIN_RAM_MB
from . import metrics
from .procs import ping_minecraft_server, send_command, start_playit, start_server, stop_playit, stop_server
from .providers import download_paper, download_purpur, download_vanilla, get_paper_versions, get_purpur_versions
from .rconmap import Rcon, RconError, rcon_settings
from .state import active_players, backup_jobs, console_dropped, console_logs, import_sessions, playit_logs
from .store import get_server_path, is_playit_running, is_running, list_servers, load_meta, read_audit, save_meta
from .util import PLAYER_NAME_PATTERN, clamp_ram, detect_server_type, detect_version, has_line_break, host_memory, read_port_from_properties, sanitize_relative_parts, suggested_ram_ceiling, unique_server_id

def purge_stale_imports():
    now = time.time()
    for token, session in list(import_sessions.items()):
        if now - session["created"] > IMPORT_SESSION_TTL:
            shutil.rmtree(session["path"], ignore_errors=True)
            import_sessions.pop(token, None)
    for folder in IMPORTS_DIR.iterdir():
        if folder.is_dir() and folder.name not in import_sessions and now - folder.stat().st_mtime > IMPORT_SESSION_TTL:
            shutil.rmtree(folder, ignore_errors=True)

@app.route("/")
def index():
    account = current_account()
    if account:
        page = render_template("index.html", user=account["username"], role=account["role"], perms=" ".join(account_permissions(account)), version=VERSION, asset=ASSET_VERSION)
    else:
        state = "open" if needs_setup() and is_local_request() else ("remote" if needs_setup() else "login")
        page = render_template("login.html", state=state, version=VERSION, asset=ASSET_VERSION)
    response = make_response(page)
    response.headers["Cache-Control"] = "no-store"
    return response

@app.route("/api/stats")
def api_stats():
    return jsonify(metrics.collect())

@app.route("/api/server/<sid>/activity")
def api_server_activity(sid):
    return jsonify({"ok": True, "entries": read_audit(60, server=sid)})

@app.route("/api/servers")
def api_servers():
    servers = list_servers()
    for server in servers:
        server["players"] = len(active_players.get(server["id"], [])) if server["running"] else 0
    return jsonify(servers)

@app.route("/api/server/<sid>/ping")
def api_server_ping(sid):
    meta = load_meta(sid)
    if not meta:
        return jsonify({"ok": False, "ping": None}), 404
    ping = ping_minecraft_server(meta.get("port", 25565))
    return jsonify({"ok": ping is not None, "ping": ping, "port": meta.get("port", 25565)})

@app.route("/api/versions/<stype>")
def api_versions(stype):
    stype = stype.lower()
    if stype == "paper":
        return jsonify(get_paper_versions())
    if stype == "purpur":
        return jsonify(get_purpur_versions())
    if stype == "vanilla":
        try:
            m = requests.get("https://launchermeta.mojang.com/mc/game/version_manifest_v2.json", headers=HEADERS, timeout=15).json()
            releases = [v["id"] for v in m["versions"] if v["type"] == "release"]
            return jsonify(releases[:50])
        except Exception:
            return jsonify([])
    return jsonify(["1.21.1", "1.21", "1.20.6", "1.20.4", "1.20.1"])

@app.route("/api/create", methods=["POST"])
def api_create():
    data = request.json or {}
    name = (data.get("name") or "").strip()
    stype = (data.get("type") or "paper").lower()
    version = (data.get("version") or "").strip()
    try:
        ram = clamp_ram(data.get("ram"))
        port = max(1024, min(65535, int(data.get("port") or 25565)))
        max_players = max(1, min(500, int(data.get("max_players") or 20)))
    except (TypeError, ValueError):
        return jsonify({"ok": False, "error": "RAM, port, and max players must be valid numbers"}), 400
    if not name or not version:
        return jsonify({"ok": False, "error": "Name and version required"}), 400
    if data.get("accept_eula") is not True:
        return jsonify({"ok": False, "error": "Accept the Minecraft EULA (https://aka.ms/MinecraftEULA) to create a server"}), 400
    taken = next((s for s in list_servers() if int(s.get("port") or 0) == port), None)
    if taken:
        return jsonify({"ok": False, "error": f"Port {port} is already used by \"{taken.get('name')}\". Pick another port, or both servers will fail to start."}), 409
    safe = "".join(c if c.isalnum() or c in "-_" else "_" for c in name)[:40] or "server"
    server_id = f"{safe}_{int(time.time())}"
    path = get_server_path(server_id)
    try:
        path.mkdir(parents=True)
    except FileExistsError:
        return jsonify({"ok": False, "error": "A server with that name was just created. Wait a second and try again."}), 409
    jar_bytes, jar_name = None, "server.jar"
    if stype == "paper":
        jar_bytes, jar_name = download_paper(version)
    elif stype == "purpur":
        jar_bytes, jar_name = download_purpur(version)
    elif stype == "vanilla":
        jar_bytes, jar_name = download_vanilla(version)
    else:
        jar_bytes, jar_name = download_paper(version)
        stype = "paper"
    if jar_bytes is None:
        shutil.rmtree(path, ignore_errors=True)
        return jsonify({"ok": False, "error": f"Download failed: {jar_name}"}), 500
    (path / jar_name).write_bytes(jar_bytes)
    for d in ("plugins", "mods", "config", "world"):
        (path / d).mkdir(exist_ok=True)
    motd = str(data.get("motd") or name).replace("\n", " ").replace("\r", " ")
    gamemode = str(data.get("gamemode") or "survival")
    difficulty = str(data.get("difficulty") or "normal")
    if gamemode not in ("survival", "creative", "adventure", "spectator"):
        gamemode = "survival"
    if difficulty not in ("peaceful", "easy", "normal", "hard"):
        difficulty = "normal"
    props = "\n".join([
        f"server-port={port}",
        f"gamemode={gamemode}",
        f"difficulty={difficulty}",
        f"max-players={max_players}",
        f"motd={motd}",
        f"online-mode={'true' if data.get('online_mode', True) else 'false'}",
        f"pvp={'true' if data.get('pvp', True) else 'false'}",
        f"enable-command-block={'true' if data.get('command_blocks', False) else 'false'}",
        f"white-list={'true' if data.get('whitelist', False) else 'false'}",
        "view-distance=10",
        "spawn-protection=16",
        ""
    ])
    (path / "server.properties").write_text(props, encoding="utf-8")
    logo = data.get("logo") if isinstance(data.get("logo"), dict) else {}
    meta = {"name": name, "type": stype, "version": version, "jar": jar_name, "ram": ram, "port": port, "logo": {"mark": str(logo.get("mark") or name[:2]).upper()[:2], "style": str(logo.get("style") or "avatar-lime")}, "eula_accepted": True, "created": datetime.now().isoformat()}
    save_meta(server_id, meta)
    return jsonify({"ok": True, "id": server_id, "meta": meta})

@app.route("/api/system/memory")
def api_system_memory():
    memory = host_memory()
    return jsonify({
        "ok": True,
        "total": memory["total"],
        "available": memory["available"],
        "min": MIN_RAM_MB,
        "max": MAX_RAM_MB,
        "suggested": suggested_ram_ceiling()
    })

@app.route("/api/import/start", methods=["POST"])
def api_import_start():
    purge_stale_imports()
    token = uuid.uuid4().hex
    staging = IMPORTS_DIR / token
    staging.mkdir(parents=True)
    import_sessions[token] = {
        "path": staging,
        "created": time.time(),
        "files": 0,
        "bytes": 0,
        "name": ((request.json or {}).get("name") or "").strip()
    }
    return jsonify({"ok": True, "token": token, "batch_bytes": IMPORT_BATCH_BYTES})

@app.route("/api/import/upload", methods=["POST"])
def api_import_upload():
    session = import_sessions.get(request.form.get("token", ""))
    if not session:
        return jsonify({"ok": False, "error": "Import session expired. Start the import again."}), 404
    staging = session["path"].resolve()
    for uploaded in request.files.getlist("files"):
        parts = sanitize_relative_parts(uploaded.filename)
        if not parts:
            continue
        destination = staging.joinpath(*parts)
        if staging not in destination.parents:
            continue
        destination.parent.mkdir(parents=True, exist_ok=True)
        uploaded.save(str(destination))
        session["files"] += 1
        session["bytes"] += destination.stat().st_size
    session["created"] = time.time()
    return jsonify({"ok": True, "files": session["files"], "bytes": session["bytes"]})

@app.route("/api/import/finish", methods=["POST"])
def api_import_finish():
    data = request.json or {}
    session = import_sessions.pop(data.get("token", ""), None)
    if not session:
        return jsonify({"ok": False, "error": "Import session expired. Start the import again."}), 404
    staging = session["path"]
    if not session["files"]:
        shutil.rmtree(staging, ignore_errors=True)
        return jsonify({"ok": False, "error": "No files were received from that folder"}), 400
    chosen_name = (data.get("name") or session["name"] or "").strip()
    server_id = unique_server_id(chosen_name or "Imported Server")
    target = get_server_path(server_id)
    try:
        shutil.move(str(staging), str(target))
    except OSError as exc:
        shutil.rmtree(staging, ignore_errors=True)
        return jsonify({"ok": False, "error": f"Could not move the imported files: {exc}"}), 500
    existing = {}
    meta_file = target / "manager_meta.json"
    if meta_file.exists():
        try:
            existing = json.loads(meta_file.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            existing = {}
    jar = next(iter(sorted(target.glob("*.jar"), key=lambda item: item.stat().st_size, reverse=True)), None)
    jar_name = existing.get("jar") if (target / str(existing.get("jar", ""))).is_file() else (jar.name if jar else "server.jar")
    meta = {
        **existing,
        "name": chosen_name or existing.get("name") or "Imported Server",
        "type": existing.get("type") or detect_server_type(jar_name),
        "version": existing.get("version") or detect_version(jar_name),
        "jar": jar_name,
        "ram": clamp_ram(existing.get("ram"), 2048),
        "port": existing.get("port") or read_port_from_properties(target / "server.properties"),
        "created": existing.get("created") or datetime.now().isoformat(),
        "imported": datetime.now().isoformat(timespec="seconds")
    }
    meta.pop("id", None)
    save_meta(server_id, meta)
    return jsonify({"ok": True, "id": server_id, "files": session["files"], "meta": {**meta, "id": server_id}})

@app.route("/api/import/cancel", methods=["POST"])
def api_import_cancel():
    session = import_sessions.pop((request.json or {}).get("token", ""), None)
    if session:
        shutil.rmtree(session["path"], ignore_errors=True)
    return jsonify({"ok": True})

@app.route("/api/server/<sid>/start", methods=["POST"])
def api_start(sid):
    data = request.get_json(silent=True) or {}
    ram = clamp_ram(data.get("ram") or load_meta(sid).get("ram", 2048))
    ok, msg = start_server(sid, ram)
    return jsonify({"ok": ok, "message": msg})

@app.route("/api/server/<sid>/stop", methods=["POST"])
def api_stop(sid):
    ok, msg = stop_server(sid)
    return jsonify({"ok": ok, "message": msg})

@app.route("/api/server/<sid>/restart", methods=["POST"])
def api_restart(sid):
    if is_running(sid):
        stop_server(sid)
    ok, msg = start_server(sid, clamp_ram(load_meta(sid).get("ram", 2048)))
    return jsonify({"ok": ok, "message": msg})

@app.route("/api/server/<sid>/reload-components", methods=["POST"])
def api_reload_components(sid):
    if not is_running(sid):
        return jsonify({"ok": False, "error": "Server offline"}), 400
    ok, msg = send_command(sid, "reload confirm")
    return jsonify({"ok": ok, "message": "Plugin reload requested" if ok else msg})

@app.route("/api/server/<sid>/command", methods=["POST"])
def api_command(sid):
    cmd = (request.json or {}).get("command", "").strip()
    if not cmd:
        return jsonify({"ok": False, "error": "Empty"}), 400
    ok, msg = send_command(sid, cmd)
    return jsonify({"ok": ok, "message": msg})

@app.route("/api/server/<sid>/console")
def api_console(sid):
    lines = console_logs.get(sid, [])
    dropped = console_dropped.get(sid, 0)
    total = dropped + len(lines)
    since = request.args.get("since", 0, type=int)
    # An offset ahead of the log means the server restarted and the log began again.
    reset = since > total
    if reset:
        since = 0
    return jsonify({"lines": lines[max(0, since - dropped):], "total": total, "running": is_running(sid), "reset": reset})

@app.route("/api/server/<sid>/delete", methods=["POST"])
def api_delete(sid):
    path = get_server_path(sid)
    if not path.is_dir():
        return jsonify({"ok": False, "error": "Server not found"}), 404
    if is_running(sid):
        stop_server(sid)
    if is_playit_running(sid):
        stop_playit(sid)
    shutil.rmtree(path, ignore_errors=True)
    shutil.rmtree(BACKUPS_DIR / sid, ignore_errors=True)
    backup_jobs.pop(sid, None)
    return jsonify({"ok": True})

@app.route("/api/server/<sid>/playit", methods=["GET", "POST"])
def api_playit(sid):
    meta = load_meta(sid)
    if request.method == "POST":
        data = request.json or {}
        executable = (data.get("executable") or "playit").strip()
        secret = (data.get("secret") or "").strip()
        if not secret:
            return jsonify({"ok": False, "error": "Playit secret is required"}), 400
        meta["playit_executable"] = executable
        meta["playit_secret"] = secret
        save_meta(sid, meta)
        return jsonify({"ok": True, "message": "Playit settings saved"})
    return jsonify({
        "ok": True,
        "configured": bool(meta.get("playit_secret")),
        "executable": meta.get("playit_executable", "playit"),
        "running": is_playit_running(sid),
        "logs": playit_logs.get(sid, [])[-100:]
    })

@app.route("/api/server/<sid>/playit/start", methods=["POST"])
def api_playit_start(sid):
    ok, message = start_playit(sid)
    return jsonify({"ok": ok, "message": message})

@app.route("/api/server/<sid>/playit/stop", methods=["POST"])
def api_playit_stop(sid):
    ok, message = stop_playit(sid)
    return jsonify({"ok": ok, "message": message})

@app.route("/api/server/<sid>/logo", methods=["POST"])
def api_server_logo(sid):
    upload = request.files.get("logo")
    if not upload or not upload.filename:
        return jsonify({"ok": False, "error": "Choose a logo image"}), 400
    extension = Path(secure_filename(upload.filename)).suffix.lower()
    if extension not in (".png", ".jpg", ".jpeg", ".webp"):
        return jsonify({"ok": False, "error": "Use a PNG, JPG, or WebP image"}), 400
    server_path = get_server_path(sid)
    if not server_path.exists():
        return jsonify({"ok": False, "error": "Server not found"}), 404
    for old_logo in server_path.glob("manager_logo.*"):
        old_logo.unlink(missing_ok=True)
    filename = f"manager_logo{extension}"
    upload.save(str(server_path / filename))
    meta = load_meta(sid)
    logo = meta.get("logo") if isinstance(meta.get("logo"), dict) else {}
    logo["file"] = filename
    meta["logo"] = logo
    save_meta(sid, meta)
    return jsonify({"ok": True, "url": f"/api/server/{sid}/logo/{filename}"})

@app.route("/api/server/<sid>/logo/<filename>")
def api_server_logo_file(sid, filename):
    if Path(filename).name != filename or not filename.startswith("manager_logo."):
        abort(404)
    return send_from_directory(get_server_path(sid), filename)

@app.route("/api/server/<sid>/players")
def api_players(sid):
    path = get_server_path(sid)
    def read_json_list(name):
        f = path / name
        if not f.exists():
            return []
        try:
            data = json.loads(f.read_text(encoding="utf-8"))
            return data if isinstance(data, list) else []
        except Exception:
            return []
    return jsonify({
        "active": active_players.get(sid, []) if is_running(sid) else [],
        "ops": read_json_list("ops.json"),
        "whitelist": read_json_list("whitelist.json"),
        "banned": read_json_list("banned-players.json"),
        "banned_ips": read_json_list("banned-ips.json")
    })

@app.route("/api/server/<sid>/anticheat", methods=["GET", "POST"])
def api_anticheat(sid):
    path = get_server_path(sid) / "anti_cheat.json"
    defaults = {"enabled": False, "movement": False, "combat": False, "alerts": True, "threshold": 5, "command": "notify"}
    if request.method == "POST":
        data = request.json or {}
        try:
            threshold = max(1, min(100, int(data.get("threshold") or 5)))
        except (TypeError, ValueError):
            return jsonify({"ok": False, "error": "Threshold must be a number"}), 400
        config = {
            "enabled": bool(data.get("enabled")),
            "movement": bool(data.get("movement")),
            "combat": bool(data.get("combat")),
            "alerts": bool(data.get("alerts", True)),
            "threshold": threshold,
            "command": str(data.get("command") or "notify")[:80]
        }
        path.write_text(json.dumps(config, indent=2), encoding="utf-8")
        return jsonify({"ok": True, "config": config})
    config = defaults
    if path.exists():
        try:
            config = {**defaults, **json.loads(path.read_text(encoding="utf-8"))}
        except (OSError, json.JSONDecodeError):
            pass
    plugin_names = [p.stem.lower() for p in (get_server_path(sid) / "plugins").glob("*.jar")]
    known = [name for name in ("grim", "matrix", "nocheatplus", "spartan", "vulcan", "anticheat") if any(name in plugin for plugin in plugin_names)]
    return jsonify({"ok": True, "config": config, "plugins": known})

@app.route("/api/server/<sid>/active-players")
def api_active_players(sid):
    if not is_running(sid):
        return jsonify({"ok": True, "running": False, "players": []})
    send_command(sid, "list")
    time.sleep(0.3)
    return jsonify({"ok": True, "running": True, "players": active_players.get(sid, [])})

@app.route("/api/server/<sid>/player-action", methods=["POST"])
def api_player_action(sid):
    data = request.json or {}
    action = data.get("action")
    player = (data.get("player") or "").strip()
    reason = (data.get("reason") or "Banned by admin").strip()
    if not player and action not in ("whitelist_on", "whitelist_off"):
        return jsonify({"ok": False, "error": "player required"}), 400
    if player and not PLAYER_NAME_PATTERN.fullmatch(player):
        return jsonify({"ok": False, "error": "Player names use letters, numbers, underscore, dot and dash (up to 32 characters)"}), 400
    if has_line_break(player, reason, data.get("value"), data.get("item"), data.get("target")):
        # send_command writes one line per command; a newline would smuggle in a second command.
        return jsonify({"ok": False, "error": "Line breaks are not allowed in player actions"}), 400
    try:
        int(data.get("count") or 1)
        float(data.get("x", 0)); float(data.get("y", 64)); float(data.get("z", 0))
    except (TypeError, ValueError):
        return jsonify({"ok": False, "error": "Item count and coordinates must be numbers"}), 400
    cmds = {
        "op": f"op {player}", "deop": f"deop {player}",
        "kick": f"kick {player} {reason}", "ban": f"ban {player} {reason}",
        "pardon": f"pardon {player}",
        "whitelist_add": f"whitelist add {player}",
        "whitelist_remove": f"whitelist remove {player}",
        "whitelist_on": "whitelist on", "whitelist_off": "whitelist off",
        "troll_fake_op": f'tellraw {player} {{"text":"You are now an operator!","color":"green"}}',
        "troll_scare": f"playsound minecraft:entity.warden.sonic_boom master {player}",
        "troll_blind": f"effect give {player} minecraft:blindness 5 1 true",
        "troll_slow": f"effect give {player} minecraft:slowness 5 4 true",
        "troll_message": f"tell {player} {(data.get('value') or 'The admin has entered your chat.').strip()}",
        "heal": f"effect give {player} minecraft:instant_health 1 10 true",
        "kill": f"kill {player}",
        "freeze": f"effect give {player} minecraft:slowness 999999 255 true",
        "unfreeze": f"effect clear {player} minecraft:slowness",
        "give": f"give {player} {(data.get('item') or 'minecraft:stone').strip()} {max(1, min(64, int(data.get('count') or 1)))}",
        "tp_to_player": f"tp {player} {(data.get('target') or '').strip()}",
        "tp_to_coords": f"tp {player} {data.get('x', 0)} {data.get('y', 64)} {data.get('z', 0)}",
    }
    cmd = cmds.get(action)
    if not cmd:
        return jsonify({"ok": False, "error": "Unknown action"}), 400
    if action == "tp_to_player" and not (data.get("target") or "").strip():
        return jsonify({"ok": False, "error": "Choose a player to teleport to"}), 400
    settings = rcon_settings(load_meta(sid))
    if settings["enabled"] and settings["password"]:
        # RCON hands back the server's own reply, which is what the action log shows.
        try:
            with Rcon("127.0.0.1", settings["port"], settings["password"]) as rcon:
                reply = rcon.command(cmd).strip()
            return jsonify({"ok": True, "message": reply or "Command sent", "command": cmd})
        except (OSError, RconError, struct.error):
            pass
    ok, msg = send_command(sid, cmd)
    return jsonify({"ok": ok, "message": msg, "command": cmd})
