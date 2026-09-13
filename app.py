#!/usr/bin/env python3
import os
import json
import re
import shutil
import socket
import struct
import subprocess
import threading
import time
from pathlib import Path
from datetime import datetime

import requests
from flask import (
    Flask, render_template, request, jsonify,
    send_from_directory, abort
)
from werkzeug.utils import secure_filename

app = Flask(__name__)
app.config["MAX_CONTENT_LENGTH"] = 100 * 1024 * 1024
DEV_MODE = os.environ.get("MC_MANAGER_DEV", "1") == "1"
OWNER_WATERMARK = "MC-SERVER-MANAGER / Mrkraps aka orgeco"

BASE_DIR = Path(__file__).parent.resolve()
SERVERS_DIR = BASE_DIR / "servers"
SERVERS_DIR.mkdir(exist_ok=True)

HEADERS = {
    "User-Agent": "MC-Server-Manager/2.0 (https://github.com/local; contact@local)"
}

running_servers = {}
console_logs = {}
active_players = {}
playit_processes = {}
playit_logs = {}

def get_server_path(server_id: str) -> Path:
    return SERVERS_DIR / server_id

def load_meta(server_id: str) -> dict:
    f = get_server_path(server_id) / "manager_meta.json"
    if f.exists():
        return json.loads(f.read_text(encoding="utf-8"))
    return {}

def save_meta(server_id: str, data: dict):
    f = get_server_path(server_id) / "manager_meta.json"
    f.write_text(json.dumps(data, indent=2), encoding="utf-8")

def is_running(server_id: str) -> bool:
    p = running_servers.get(server_id)
    return p is not None and p.poll() is None

def is_playit_running(server_id: str) -> bool:
    process = playit_processes.get(server_id)
    return process is not None and process.poll() is None

def list_servers() -> list:
    out = []
    for d in SERVERS_DIR.iterdir():
        if d.is_dir() and (d / "manager_meta.json").exists():
            m = load_meta(d.name)
            m["id"] = d.name
            m["running"] = is_running(d.name)
            m["playit_running"] = is_playit_running(d.name)
            out.append(m)
    return sorted(out, key=lambda x: x.get("created", ""), reverse=True)

def safe_path(server_id: str, rel: str):
    base = get_server_path(server_id).resolve()
    target = (base / rel).resolve()
    if not str(target).startswith(str(base)):
        return None
    return target

def encode_varint(value):
    output = bytearray()
    value &= 0xFFFFFFFF
    while True:
        byte = value & 0x7F
        value >>= 7
        output.append(byte | (0x80 if value else 0))
        if not value:
            return bytes(output)

def read_varint(stream):
    value = 0
    shift = 0
    while shift < 35:
        byte = stream.recv(1)
        if not byte:
            raise ConnectionError("Minecraft server closed the connection")
        current = byte[0]
        value |= (current & 0x7F) << shift
        if not current & 0x80:
            return value
        shift += 7
    raise ValueError("Invalid Minecraft packet length")

def send_packet(stream, payload):
    stream.sendall(encode_varint(len(payload)) + payload)

def ping_minecraft_server(port):
    started = time.perf_counter()
    try:
        with socket.create_connection(("127.0.0.1", int(port)), timeout=2) as stream:
            address = b"127.0.0.1"
            handshake = b"\x00" + encode_varint(760) + encode_varint(len(address)) + address + struct.pack(">H", int(port)) + b"\x01"
            send_packet(stream, handshake)
            send_packet(stream, b"\x00")
            packet_length = read_varint(stream)
            packet = stream.recv(packet_length)
            if not packet:
                raise ConnectionError("Empty status response")
            return round((time.perf_counter() - started) * 1000)
    except (OSError, ValueError, ConnectionError, struct.error):
        return None

def get_paper_versions() -> list:
    try:
        r = requests.get("https://fill.papermc.io/v3/projects/paper", headers=HEADERS, timeout=20)
        r.raise_for_status()
        data = r.json()
        versions = []
        for group, vs in data.get("versions", {}).items():
            versions.extend(vs)
        return versions
    except Exception as e:
        print("Paper versions error:", e)
        return []

def download_paper(version: str):
    try:
        r = requests.get(
            f"https://fill.papermc.io/v3/projects/paper/versions/{version}/builds",
            headers=HEADERS, timeout=20
        )
        r.raise_for_status()
        builds = r.json()
        if not isinstance(builds, list) or not builds:
            return None, "No builds found"
        stable = [b for b in builds if b.get("channel") == "STABLE"]
        build = stable[0] if stable else builds[0]
        dl = build.get("downloads", {}).get("server:default")
        if not dl or not dl.get("url"):
            return None, "No download URL"
        jr = requests.get(dl["url"], headers=HEADERS, timeout=180)
        jr.raise_for_status()
        name = dl.get("name") or f"paper-{version}.jar"
        return jr.content, name
    except Exception as e:
        print("Paper download error:", e)
        return None, str(e)

def get_purpur_versions() -> list:
    try:
        r = requests.get("https://api.purpurmc.org/v2/purpur", headers=HEADERS, timeout=15)
        r.raise_for_status()
        return list(reversed(r.json().get("versions", [])))
    except Exception as e:
        print("Purpur versions error:", e)
        return []

def download_purpur(version: str):
    try:
        url = f"https://api.purpurmc.org/v2/purpur/{version}/latest/download"
        r = requests.get(url, headers=HEADERS, timeout=180)
        r.raise_for_status()
        return r.content, f"purpur-{version}.jar"
    except Exception as e:
        return None, str(e)

def download_vanilla(version: str):
    try:
        manifest = requests.get(
            "https://launchermeta.mojang.com/mc/game/version_manifest_v2.json",
            headers=HEADERS, timeout=15
        ).json()
        ver_info = next((v for v in manifest["versions"] if v["id"] == version), None)
        if not ver_info:
            return None, "Version not found"
        ver_json = requests.get(ver_info["url"], headers=HEADERS, timeout=15).json()
        server_url = ver_json.get("downloads", {}).get("server", {}).get("url")
        if not server_url:
            return None, "No server jar"
        r = requests.get(server_url, headers=HEADERS, timeout=180)
        r.raise_for_status()
        return r.content, f"vanilla-{version}.jar"
    except Exception as e:
        return None, str(e)

def modrinth_search(query, project_type="plugin", limit=24, game_version=None, loader=None, index="relevance"):
    try:
        facets = []
        if project_type == "plugin":
            facets.append(["project_type:plugin"])
            facets.append(["categories:bukkit", "categories:spigot", "categories:paper", "categories:purpur"])
        elif project_type == "mod":
            facets.append(["project_type:mod"])
            if loader:
                facets.append([f"categories:{loader}"])
        if game_version:
            facets.append([f"versions:{game_version}"])
        params = {"query": query or "", "limit": limit, "index": index}
        if facets:
            params["facets"] = json.dumps(facets)
        r = requests.get("https://api.modrinth.com/v2/search", params=params, headers=HEADERS, timeout=20)
        r.raise_for_status()
        return r.json()
    except Exception as e:
        return {"hits": [], "error": str(e)}

def modrinth_versions(project_id, game_version=None, loader=None):
    try:
        params = {}
        if game_version:
            params["game_versions"] = json.dumps([game_version])
        if loader:
            params["loaders"] = json.dumps([loader])
        r = requests.get(f"https://api.modrinth.com/v2/project/{project_id}/version", params=params, headers=HEADERS, timeout=20)
        r.raise_for_status()
        return r.json()
    except Exception:
        return []

def download_url_bytes(url):
    try:
        r = requests.get(url, headers=HEADERS, timeout=120)
        r.raise_for_status()
        return r.content
    except Exception:
        return None

def read_console(server_id, process):
    console_logs.setdefault(server_id, [])
    try:
        for line in iter(process.stdout.readline, b""):
            text = line.decode("utf-8", errors="replace").rstrip()
            console_logs[server_id].append(text)
            update_active_players(server_id, text)
            if len(console_logs[server_id]) > 3000:
                console_logs[server_id] = console_logs[server_id][-2000:]
    except Exception:
        pass
    finally:
        running_servers.pop(server_id, None)

def update_active_players(server_id, text):
    players = active_players.setdefault(server_id, [])
    joined = re.search(r":\s+([^:]+) joined the game\s*$", text)
    left = re.search(r":\s+([^:]+) left the game\s*$", text)
    listed = re.search(r"There are \d+ of a max of \d+ players online:\s*(.*)$", text)
    if listed:
        names = [name.strip() for name in listed.group(1).split(",") if name.strip()]
        active_players[server_id] = names
    elif joined:
        name = joined.group(1).strip()
        if name not in players:
            players.append(name)
    elif left:
        name = left.group(1).strip()
        active_players[server_id] = [player for player in players if player != name]

def start_server(server_id, ram_mb=2048):
    if is_running(server_id):
        return False, "Already running"
    path = get_server_path(server_id)
    meta = load_meta(server_id)
    jar_name = meta.get("jar", "server.jar")
    jar_path = path / jar_name
    if not jar_path.exists():
        return False, f"JAR missing: {jar_name}"
    (path / "eula.txt").write_text("eula=true\n", encoding="utf-8")
    cmd = ["java", f"-Xms{max(512, ram_mb // 2)}M", f"-Xmx{ram_mb}M", "-jar", str(jar_path), "nogui"]
    try:
        process = subprocess.Popen(cmd, cwd=str(path), stdout=subprocess.PIPE, stderr=subprocess.STDOUT, stdin=subprocess.PIPE)
        running_servers[server_id] = process
        active_players[server_id] = []
        console_logs[server_id] = [f"[{datetime.now().strftime('%H:%M:%S')}] Starting {jar_name}..."]
        threading.Thread(target=read_console, args=(server_id, process), daemon=True).start()
        return True, "Server started"
    except FileNotFoundError:
        return False, "Java not found in PATH. Install Java 17/21/25."
    except Exception as e:
        return False, str(e)

def stop_server(server_id):
    if not is_running(server_id):
        return False, "Not running"
    process = running_servers[server_id]
    try:
        if process.stdin:
            process.stdin.write(b"stop\n")
            process.stdin.flush()
        process.wait(timeout=45)
    except Exception:
        process.kill()
    finally:
        running_servers.pop(server_id, None)
        active_players.pop(server_id, None)
        if is_playit_running(server_id):
            stop_playit(server_id)
    return True, "Server stopped"

def read_playit_output(server_id, process):
    playit_logs.setdefault(server_id, [])
    try:
        for line in iter(process.stdout.readline, b""):
            text = line.decode("utf-8", errors="replace").rstrip()
            playit_logs[server_id].append(text)
            if len(playit_logs[server_id]) > 500:
                playit_logs[server_id] = playit_logs[server_id][-300:]
    except Exception:
        pass
    finally:
        if playit_processes.get(server_id) is process:
            playit_processes.pop(server_id, None)

def start_playit(server_id):
    if is_playit_running(server_id):
        return False, "Playit is already running"
    meta = load_meta(server_id)
    secret = (meta.get("playit_secret") or "").strip()
    executable = (meta.get("playit_executable") or "playit").strip() or "playit"
    if not secret:
        return False, "Add a Playit secret first"
    try:
        process = subprocess.Popen(
            [executable, "--secret", secret],
            cwd=str(get_server_path(server_id)),
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
        )
        playit_processes[server_id] = process
        playit_logs[server_id] = ["Starting Playit agent..."]
        threading.Thread(target=read_playit_output, args=(server_id, process), daemon=True).start()
        return True, "Playit agent started"
    except FileNotFoundError:
        return False, f"Playit executable not found: {executable}"
    except Exception as exc:
        return False, str(exc)

def stop_playit(server_id):
    process = playit_processes.get(server_id)
    if not process or process.poll() is not None:
        playit_processes.pop(server_id, None)
        return False, "Playit is not running"
    process.terminate()
    try:
        process.wait(timeout=8)
    except subprocess.TimeoutExpired:
        process.kill()
    playit_processes.pop(server_id, None)
    return True, "Playit agent stopped"

def send_command(server_id, cmd):
    if not is_running(server_id):
        return False, "Server offline"
    process = running_servers[server_id]
    try:
        process.stdin.write((cmd.rstrip() + "\n").encode("utf-8"))
        process.stdin.flush()
        return True, "OK"
    except Exception as e:
        return False, str(e)

@app.route("/")
def index():
    return render_template("index.html")

@app.route("/api/dev-version")
def api_dev_version():
    if not DEV_MODE:
        return jsonify({"enabled": False})
    watched = [BASE_DIR / "app.py", BASE_DIR / "templates" / "index.html"]
    watched.extend((BASE_DIR / "static").rglob("*"))
    version = max((path.stat().st_mtime_ns for path in watched if path.is_file()), default=0)
    return jsonify({"enabled": True, "version": version})

@app.route("/api/servers")
def api_servers():
    return jsonify(list_servers())

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
        ram = max(512, min(65536, int(data.get("ram") or 2048)))
        port = max(1024, min(65535, int(data.get("port") or 25565)))
        max_players = max(1, min(500, int(data.get("max_players") or 20)))
    except (TypeError, ValueError):
        return jsonify({"ok": False, "error": "RAM, port, and max players must be valid numbers"}), 400
    if not name or not version:
        return jsonify({"ok": False, "error": "Name and version required"}), 400
    safe = "".join(c if c.isalnum() or c in "-_" else "_" for c in name)[:40] or "server"
    server_id = f"{safe}_{int(time.time())}"
    path = get_server_path(server_id)
    path.mkdir(parents=True)
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
    props = "\n".join([
        f"server-port={port}",
        f"gamemode={data.get('gamemode') or 'survival'}",
        f"difficulty={data.get('difficulty') or 'normal'}",
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
    meta = {"name": name, "type": stype, "version": version, "jar": jar_name, "ram": ram, "port": port, "logo": {"mark": str(logo.get("mark") or name[:2]).upper()[:2], "style": str(logo.get("style") or "avatar-lime")}, "created": datetime.now().isoformat()}
    save_meta(server_id, meta)
    return jsonify({"ok": True, "id": server_id, "meta": meta})

@app.route("/api/import", methods=["POST"])
def api_import_server():
    files = request.files.getlist("files")
    name = (request.form.get("name") or "").strip()
    if not files:
        return jsonify({"ok": False, "error": "Choose a server folder first"}), 400
    first_path = files[0].filename.replace("\\", "/")
    folder_name = first_path.split("/", 1)[0] if "/" in first_path else "Imported Server"
    prefixes = [file.filename.replace("\\", "/").split("/", 1)[0] for file in files]
    root_prefix = prefixes[0] if "/" in first_path and prefixes and all(prefix == prefixes[0] for prefix in prefixes) else ""
    server_name = name or folder_name or "Imported Server"
    safe = "".join(c if c.isalnum() or c in "-_" else "_" for c in server_name)[:40] or "imported-server"
    server_id = f"{safe}_{int(time.time())}"
    target_root = get_server_path(server_id)
    target_root.mkdir(parents=True)
    try:
        for uploaded in files:
            relative = uploaded.filename.replace("\\", "/")
            parts = [part for part in relative.split("/") if part not in ("", ".")]
            if root_prefix and parts and parts[0] == root_prefix:
                parts = parts[1:]
            if not parts or ".." in parts or any(":" in part for part in parts):
                continue
            destination = target_root.joinpath(*parts).resolve()
            if target_root.resolve() not in destination.parents:
                continue
            destination.parent.mkdir(parents=True, exist_ok=True)
            uploaded.save(str(destination))
    except Exception as exc:
        shutil.rmtree(target_root, ignore_errors=True)
        return jsonify({"ok": False, "error": f"Import failed: {exc}"}), 500
    meta_path = target_root / "manager_meta.json"
    if meta_path.exists():
        try:
            meta = json.loads(meta_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            meta = {}
    else:
        jar = next(iter(target_root.glob("*.jar")), None)
        properties = target_root / "server.properties"
        port = 25565
        if properties.exists():
            for line in properties.read_text(encoding="utf-8", errors="replace").splitlines():
                if line.startswith("server-port="):
                    try:
                        port = int(line.split("=", 1)[1])
                    except ValueError:
                        pass
        meta = {"name": server_name, "type": "imported", "version": "unknown", "jar": jar.name if jar else "server.jar", "ram": 2048, "port": port, "created": datetime.now().isoformat()}
        save_meta(server_id, meta)
    meta["name"] = meta.get("name") or server_name
    meta["id"] = server_id
    save_meta(server_id, {key: value for key, value in meta.items() if key != "id"})
    return jsonify({"ok": True, "id": server_id, "meta": meta})

@app.route("/api/server/<sid>/start", methods=["POST"])
def api_start(sid):
    data = request.json or {}
    ram = int(data.get("ram") or load_meta(sid).get("ram", 2048))
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
    ok, msg = start_server(sid, load_meta(sid).get("ram", 2048))
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
    since = request.args.get("since", 0, type=int)
    return jsonify({"lines": lines[since:], "total": len(lines), "running": is_running(sid)})

@app.route("/api/server/<sid>/delete", methods=["POST"])
def api_delete(sid):
    if is_running(sid):
        stop_server(sid)
    if is_playit_running(sid):
        stop_playit(sid)
    path = get_server_path(sid)
    if path.exists():
        shutil.rmtree(path, ignore_errors=True)
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
    }
    cmd = cmds.get(action)
    if not cmd:
        return jsonify({"ok": False, "error": "Unknown action"}), 400
    ok, msg = send_command(sid, cmd)
    return jsonify({"ok": ok, "message": msg, "command": cmd})

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

@app.route("/api/server/<sid>/fs/read")
def api_fs_read(sid):
    rel = request.args.get("path", "").lstrip("/")
    target = safe_path(sid, rel)
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
    if target is None or not target.is_file():
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
    lines = ["# Edited by MC Server Manager", ""]
    for k, v in props.items():
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
        game_version=request.args.get("version"),
        loader=request.args.get("loader")
    ))

@app.route("/api/modrinth/featured")
def api_modrinth_featured():
    return jsonify(modrinth_search(
        "",
        request.args.get("type", "plugin"),
        limit=100,
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
    path = get_server_path(sid)
    if not path.exists():
        return jsonify({"ok": False, "error": "Server missing"}), 404
    folder = path / target
    folder.mkdir(exist_ok=True)
    content = download_url_bytes(url)
    if not content:
        return jsonify({"ok": False, "error": "Download failed"}), 500
    dest = folder / secure_filename(filename)
    dest.write_bytes(content)
    return jsonify({"ok": True, "path": str(dest.relative_to(path))})

@app.route("/api/server/<sid>/files")
def api_files(sid):
    folder = request.args.get("folder", "plugins")
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

if __name__ == "__main__":
    print("=" * 55)
    print("  Minecraft All-in-One Server Manager v2")
    print("  http://127.0.0.1:5000")
    print("=" * 55)
    if DEV_MODE:
        app.run(host="127.0.0.1", port=5000, debug=True, use_reloader=True, threaded=True)
    else:
        try:
            from waitress import serve
            serve(app, host="127.0.0.1", port=5000, threads=8)
        except ImportError:
            app.run(host="127.0.0.1", port=5000, debug=False, threaded=True)
