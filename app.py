#!/usr/bin/env python3
import hashlib
import io
import os
import secrets
import json
import re
import shutil
import socket
import struct
import subprocess
import threading
import time
import uuid
import zipfile
from pathlib import Path
from datetime import datetime

import requests
from flask import (
    Flask, render_template, request, jsonify,
    send_from_directory, abort
)
from werkzeug.exceptions import HTTPException
from werkzeug.utils import secure_filename

try:
    import psutil
except ImportError:
    psutil = None

app = Flask(__name__)
app.config["MAX_CONTENT_LENGTH"] = 256 * 1024 * 1024
DEV_MODE = os.environ.get("MC_MANAGER_DEV", "1") == "1"
OWNER_WATERMARK = "MC-SERVER-MANAGER / Mrkraps aka orgeco"

BASE_DIR = Path(__file__).parent.resolve()
SERVERS_DIR = BASE_DIR / "servers"
SERVERS_DIR.mkdir(exist_ok=True)
BACKUPS_DIR = BASE_DIR / "backups"
BACKUPS_DIR.mkdir(exist_ok=True)
IMPORTS_DIR = BASE_DIR / ".imports"
IMPORTS_DIR.mkdir(exist_ok=True)

IMPORT_SESSION_TTL = 6 * 3600
IMPORT_BATCH_BYTES = 24 * 1024 * 1024
BACKUP_SKIP_DIRS = {"logs", "crash-reports", "cache", "debug", "libraries", "versions"}
BACKUP_SKIP_NAMES = {"session.lock", "usercache.json"}
MIN_RAM_MB = 512
MAX_RAM_MB = 65536
MAINTENANCE_INTERVAL = 300
UPDATE_CACHE_TTL = 3600

HEADERS = {
    "User-Agent": "MC-Server-Manager/2.0 (https://github.com/local; contact@local)"
}

running_servers = {}
console_logs = {}
active_players = {}
playit_processes = {}
playit_logs = {}
import_sessions = {}
backup_jobs = {}
update_cache = {}

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

def sanitize_relative_parts(relative: str) -> list:
    parts = [part for part in str(relative).replace("\\", "/").split("/") if part not in ("", ".")]
    if any(part == ".." or ":" in part for part in parts):
        return []
    return parts

def unique_server_id(name: str) -> str:
    safe = "".join(c if c.isalnum() or c in "-_" else "_" for c in name)[:40] or "server"
    return f"{safe}_{int(time.time())}"

def host_memory() -> dict:
    total = available = None
    if psutil is not None:
        try:
            memory = psutil.virtual_memory()
            total, available = memory.total // (1024 * 1024), memory.available // (1024 * 1024)
        except Exception:
            total = available = None
    if total is None:
        try:
            total = (os.sysconf("SC_PAGE_SIZE") * os.sysconf("SC_PHYS_PAGES")) // (1024 * 1024)
        except (AttributeError, ValueError, OSError):
            total = None
    return {"total": total, "available": available}

def suggested_ram_ceiling() -> int:
    total = host_memory().get("total")
    if not total:
        return 16384
    return max(2048, min(MAX_RAM_MB, int(total * 0.8) // 512 * 512))

def clamp_ram(value, fallback=2048) -> int:
    try:
        return max(MIN_RAM_MB, min(MAX_RAM_MB, int(value)))
    except (TypeError, ValueError):
        return fallback

def detect_server_type(jar_name: str) -> str:
    lowered = (jar_name or "").lower()
    for marker in ("paper", "purpur", "fabric", "forge", "spigot", "bukkit", "velocity", "waterfall"):
        if marker in lowered:
            return marker
    return "imported"

def detect_version(jar_name: str) -> str:
    match = re.search(r"(1\.\d{1,2}(?:\.\d{1,2})?)", jar_name or "")
    return match.group(1) if match else "unknown"

def read_port_from_properties(path: Path) -> int:
    if not path.exists():
        return 25565
    for line in path.read_text(encoding="utf-8", errors="replace").splitlines():
        if line.strip().startswith("server-port="):
            try:
                return int(line.split("=", 1)[1].strip())
            except ValueError:
                break
    return 25565

def purge_stale_imports():
    now = time.time()
    for token, session in list(import_sessions.items()):
        if now - session["created"] > IMPORT_SESSION_TTL:
            shutil.rmtree(session["path"], ignore_errors=True)
            import_sessions.pop(token, None)
    for folder in IMPORTS_DIR.iterdir():
        if folder.is_dir() and folder.name not in import_sessions and now - folder.stat().st_mtime > IMPORT_SESSION_TTL:
            shutil.rmtree(folder, ignore_errors=True)

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
        run_backup(server_id, label, include_world, keep, automatic=automatic)
    finally:
        if live:
            send_command(server_id, "save-on")

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
            run_backup(server_id, "Automatic copy taken before a restore", True, keep, automatic=True)
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

def file_sha1(path: Path) -> str:
    digest = hashlib.sha1()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()

def modrinth_version_by_hash(sha1_hash: str):
    try:
        r = requests.get(f"https://api.modrinth.com/v2/version_file/{sha1_hash}", headers=HEADERS, timeout=15)
        if r.status_code == 404:
            return None
        r.raise_for_status()
        return r.json()
    except Exception:
        return None

def modrinth_project_title(project_id: str, fallback: str) -> str:
    try:
        r = requests.get(f"https://api.modrinth.com/v2/project/{project_id}", headers=HEADERS, timeout=15)
        r.raise_for_status()
        return r.json().get("title") or fallback
    except Exception:
        return fallback

def primary_version_file(version: dict):
    files = version.get("files") or []
    return next((f for f in files if f.get("primary")), files[0] if files else None)

def scan_plugin_updates(server_id: str) -> list:
    root = get_server_path(server_id)
    game_version = load_meta(server_id).get("version")
    results = []
    for folder_name in ("plugins", "mods"):
        folder = root / folder_name
        if not folder.exists():
            continue
        for jar in sorted(folder.glob("*.jar")):
            entry = {"file": jar.name, "folder": folder_name, "matched": False, "update_available": False}
            try:
                current = modrinth_version_by_hash(file_sha1(jar))
            except OSError:
                current = None
            if not current:
                results.append(entry)
                continue
            project_id = current.get("project_id")
            candidates = [v for v in modrinth_versions(project_id, game_version=game_version) if v.get("id") != current.get("id")]
            candidates.sort(key=lambda v: v.get("date_published", ""), reverse=True)
            newest = candidates[0] if candidates else None
            entry.update({
                "matched": True,
                "project_id": project_id,
                "name": modrinth_project_title(project_id, jar.stem),
                "current_version": current.get("version_number"),
                "latest_version": newest.get("version_number") if newest else current.get("version_number"),
                "update_available": bool(newest)
            })
            newest_file = primary_version_file(newest) if newest else None
            if newest_file:
                entry["download_url"] = newest_file.get("url")
                entry["download_filename"] = newest_file.get("filename")
            results.append(entry)
    update_cache[server_id] = {"checked": time.time(), "items": results}
    return results

def apply_plugin_update(server_id: str, folder_name: str, filename: str, download_url: str, new_filename: str):
    folder = get_server_path(server_id) / folder_name
    target = folder / secure_filename(filename)
    if not target.exists():
        return False, "That plugin or mod file is no longer there"
    content = download_url_bytes(download_url)
    if not content:
        return False, "Could not download the new version"
    saved_name = secure_filename(new_filename or filename)
    try:
        if target.name != saved_name:
            target.unlink(missing_ok=True)
        (folder / saved_name).write_bytes(content)
        return True, saved_name
    except OSError as exc:
        return False, f"Could not replace the file (is the server running?): {exc}"

RCON_AUTH = 3
RCON_COMMAND = 2
RCON_TIMEOUT = 4
MAP_CACHE_TTL = 0.9

map_cache = {}

class RconError(Exception):
    pass

class Rcon:
    """Minimal Source RCON client. Minecraft splits long replies across packets,
    so reads keep draining until the socket goes quiet."""

    def __init__(self, host, port, password):
        self.address = (host, int(port))
        self.password = password
        self.sock = None
        self.request_id = 0

    def __enter__(self):
        self.sock = socket.create_connection(self.address, timeout=RCON_TIMEOUT)
        self.sock.settimeout(RCON_TIMEOUT)
        if self._send(RCON_AUTH, self.password)[0] == -1:
            raise RconError("RCON password rejected")
        return self

    def __exit__(self, *_):
        if self.sock:
            self.sock.close()
            self.sock = None

    def _send(self, kind, body):
        self.request_id += 1
        payload = struct.pack("<ii", self.request_id, kind) + body.encode("utf-8") + b"\x00\x00"
        self.sock.sendall(struct.pack("<i", len(payload)) + payload)
        return self._read()

    def _read(self):
        header = self._recv_exact(12)
        length, response_id, _ = struct.unpack("<iii", header)
        body = self._recv_exact(length - 8)
        return response_id, body[:-2].decode("utf-8", errors="replace")

    def _recv_exact(self, count):
        chunks = b""
        while len(chunks) < count:
            piece = self.sock.recv(count - len(chunks))
            if not piece:
                raise RconError("RCON connection closed")
            chunks += piece
        return chunks

    def command(self, text):
        return self._send(RCON_COMMAND, text)[1]

def rcon_settings(meta: dict) -> dict:
    stored = meta.get("rcon") if isinstance(meta.get("rcon"), dict) else {}
    return {
        "enabled": bool(stored.get("enabled")),
        "port": int(stored.get("port") or 25575),
        "password": stored.get("password") or ""
    }

def write_properties(path: Path, updates: dict):
    lines = path.read_text(encoding="utf-8").splitlines() if path.exists() else []
    seen = set()
    out = []
    for line in lines:
        key = line.split("=", 1)[0].strip()
        if key in updates:
            out.append(f"{key}={updates[key]}")
            seen.add(key)
        else:
            out.append(line)
    out.extend(f"{k}={v}" for k, v in updates.items() if k not in seen)
    path.write_text("\n".join(out) + "\n", encoding="utf-8")

POS_PATTERN = re.compile(r"\[([-\d.]+)d?,\s*([-\d.]+)d?,\s*([-\d.]+)d?\]")
NUMBER_PATTERN = re.compile(r"([-\d.]+)[fdb]?\s*$")

def parse_entity_pos(reply: str):
    match = POS_PATTERN.search(reply or "")
    if not match:
        return None
    return [round(float(v), 2) for v in match.groups()]

def parse_entity_number(reply: str):
    match = NUMBER_PATTERN.search((reply or "").strip())
    return round(float(match.group(1)), 1) if match else None

def rcon_player_snapshot(server_id: str) -> dict:
    meta = load_meta(server_id)
    settings = rcon_settings(meta)
    if not settings["enabled"] or not settings["password"]:
        return {"ok": False, "error": "RCON is not configured for this server", "players": []}
    if not is_running(server_id):
        return {"ok": False, "error": "Server is offline", "players": []}
    try:
        with Rcon("127.0.0.1", settings["port"], settings["password"]) as rcon:
            listed = rcon.command("list")
            names = []
            match = re.search(r"players online:\s*(.*)$", listed.strip())
            if match:
                names = [n.strip() for n in match.group(1).split(",") if n.strip()]
            players = []
            for name in names[:40]:
                position = parse_entity_pos(rcon.command(f"data get entity {name} Pos"))
                if not position:
                    continue
                players.append({
                    "name": name,
                    "x": position[0],
                    "y": position[1],
                    "z": position[2],
                    "health": parse_entity_number(rcon.command(f"data get entity {name} Health")),
                    "dimension": (rcon.command(f"data get entity {name} Dimension") or "").split()[-1].strip('"')
                })
        return {"ok": True, "players": players}
    except (OSError, RconError, struct.error, ValueError) as exc:
        return {"ok": False, "error": f"RCON: {exc}", "players": []}

def markers_file(server_id: str) -> Path:
    return get_server_path(server_id) / "map_markers.json"

def load_markers(server_id: str) -> list:
    path = markers_file(server_id)
    if not path.exists():
        return []
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        return data if isinstance(data, list) else []
    except (OSError, json.JSONDecodeError):
        return []

def save_markers(server_id: str, markers: list):
    markers_file(server_id).write_text(json.dumps(markers, indent=2), encoding="utf-8")

def require_admin():
    """No-op unless MC_MANAGER_TOKEN is set; the panel is otherwise bound to localhost."""
    token = os.environ.get("MC_MANAGER_TOKEN")
    if not token:
        return None
    sent = request.headers.get("X-Admin-Token") or (request.json or {}).get("token") if request.is_json else request.headers.get("X-Admin-Token")
    if sent != token:
        return jsonify({"ok": False, "error": "Admin token required"}), 401
    return None

UPDATE_REPO = os.environ.get("MC_MANAGER_REPO", "bliper2/mc-server-manager-v2")
UPDATE_BRANCH = os.environ.get("MC_MANAGER_BRANCH", "main")
GITHUB_API = os.environ.get("MC_MANAGER_UPDATE_API", "https://api.github.com").rstrip("/")
UPDATE_STATE_FILE = BASE_DIR / "update_state.json"
UPDATE_SNAPSHOTS = BACKUPS_DIR / "_manager"
UPDATE_CHECK_INTERVAL = 6 * 3600
# Anything holding the user's own data, or the environment the app runs in.
UPDATE_PROTECTED = {"servers", "backups", ".imports", ".venv", ".git", "__pycache__", "update_state.json"}
UPDATE_REQUIRED = ("app.py", "templates/index.html")

def load_update_state() -> dict:
    defaults = {"installed": None, "auto_check": True, "auto_install": False, "last_check": None, "latest": None}
    if not UPDATE_STATE_FILE.exists():
        return defaults
    try:
        return {**defaults, **json.loads(UPDATE_STATE_FILE.read_text(encoding="utf-8"))}
    except (OSError, json.JSONDecodeError):
        return defaults

def save_update_state(state: dict):
    UPDATE_STATE_FILE.write_text(json.dumps(state, indent=2), encoding="utf-8")

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

def fetch_update_status(state: dict) -> dict:
    latest = github_json(f"/repos/{UPDATE_REPO}/commits/{UPDATE_BRANCH}")
    current = installed_commit(state)
    info = {
        "sha": latest["sha"],
        "short": latest["sha"][:7],
        "message": latest["commit"]["message"].splitlines()[0],
        "date": latest["commit"]["committer"]["date"],
        "behind": [],
        "update_available": bool(current) and current != latest["sha"]
    }
    if current and current != latest["sha"]:
        try:
            compare = github_json(f"/repos/{UPDATE_REPO}/compare/{current}...{UPDATE_BRANCH}")
            info["behind"] = [c["commit"]["message"].splitlines()[0] for c in compare.get("commits", [])][-20:]
            info["count"] = compare.get("ahead_by", len(info["behind"]))
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
    latest = github_json(f"/repos/{UPDATE_REPO}/commits/{UPDATE_BRANCH}")
    sha = latest["sha"]
    response = requests.get(f"{GITHUB_API}/repos/{UPDATE_REPO}/zipball/{UPDATE_BRANCH}", headers=HEADERS, timeout=180)
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
    return {"sha": sha, "short": sha[:7], "files": written, "snapshot": snapshot.name}

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
    return {"snapshot": archives[0].name, "files": restored}

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

def wants_json() -> bool:
    return request.path.startswith("/api/")

@app.errorhandler(413)
def handle_upload_too_large(_error):
    limit = app.config["MAX_CONTENT_LENGTH"] // (1024 * 1024)
    return jsonify({
        "ok": False,
        "error": f"That upload is larger than the {limit} MB limit for a single request. Folder imports are sent in batches, so this usually means one individual file is oversized."
    }), 413

@app.errorhandler(HTTPException)
def handle_http_error(error):
    if not wants_json():
        return error
    return jsonify({"ok": False, "error": error.description or error.name}), error.code

@app.errorhandler(Exception)
def handle_unexpected_error(error):
    if not wants_json():
        raise error
    app.logger.exception("Unhandled error on %s", request.path)
    return jsonify({"ok": False, "error": f"{type(error).__name__}: {error}"}), 500

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
        ram = clamp_ram(data.get("ram"))
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

@app.route("/api/manager/update")
def api_manager_update():
    state = load_update_state()
    return jsonify({
        "ok": True,
        "installed": installed_commit(state),
        "auto_check": state["auto_check"],
        "auto_install": state["auto_install"],
        "last_check": state["last_check"],
        "latest": state["latest"],
        "repo": UPDATE_REPO,
        "branch": UPDATE_BRANCH
    })

@app.route("/api/manager/update/settings", methods=["POST"])
def api_manager_update_settings():
    denied = require_admin()
    if denied:
        return denied
    data = request.json or {}
    state = load_update_state()
    state["auto_check"] = bool(data.get("auto_check"))
    state["auto_install"] = bool(data.get("auto_install"))
    save_update_state(state)
    return jsonify({"ok": True, "auto_check": state["auto_check"], "auto_install": state["auto_install"]})

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
    denied = require_admin()
    if denied:
        return denied
    if any(is_running(folder.name) for folder in SERVERS_DIR.iterdir() if folder.is_dir()):
        if not (request.json or {}).get("force"):
            return jsonify({"ok": False, "error": "A server is running. Stop it first, or send force to update anyway."}), 409
    try:
        result = apply_manager_update()
    except (requests.RequestException, RuntimeError, zipfile.BadZipFile, OSError) as exc:
        return jsonify({"ok": False, "error": f"Update failed: {exc}"}), 500
    return jsonify({"ok": True, **result, "restart_required": True})

@app.route("/api/manager/update/rollback", methods=["POST"])
def api_manager_update_rollback():
    denied = require_admin()
    if denied:
        return denied
    try:
        result = restore_manager_snapshot()
    except (RuntimeError, zipfile.BadZipFile, OSError) as exc:
        return jsonify({"ok": False, "error": str(exc)}), 500
    return jsonify({"ok": True, **result, "restart_required": True})

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
    if latest.get("update_available") and state.get("auto_install"):
        try:
            result = apply_manager_update()
            print(f"Manager updated to {result['short']}; restart to load it")
        except (requests.RequestException, RuntimeError, zipfile.BadZipFile, OSError) as exc:
            print("Manager auto-update failed:", exc)

def run_scheduled_maintenance():
    now = time.time()
    check_manager_update()
    for folder in SERVERS_DIR.iterdir():
        if not folder.is_dir() or not (folder / "manager_meta.json").exists():
            continue
        sid = folder.name
        meta = load_meta(sid)
        dirty = False

        backup_cfg = auto_backup_settings(meta)
        if backup_cfg["enabled"]:
            due_at = meta.get("last_auto_backup_at", 0) + backup_cfg["interval_hours"] * 3600
            job = backup_jobs.get(sid)
            if now >= due_at and not (job and job.get("state") == "running"):
                keep = int(meta.get("backup_keep", 10) or 10)
                started, _ = start_job(sid, run_backup_task, "Automatic backup", backup_cfg["world"], keep, True)
                if started:
                    meta["last_auto_backup_at"] = now
                    meta["last_auto_backup"] = datetime.now().isoformat(timespec="seconds")
                    dirty = True

        update_cfg = auto_update_settings(meta)
        if update_cfg["enabled"]:
            due_at = meta.get("last_update_check_at", 0) + update_cfg["interval_hours"] * 3600
            if now >= due_at:
                try:
                    items = scan_plugin_updates(sid)
                    meta["last_update_check_at"] = now
                    meta["last_update_check"] = datetime.now().isoformat(timespec="seconds")
                    dirty = True
                    if update_cfg["install"]:
                        for item in items:
                            if item.get("update_available") and item.get("download_url"):
                                apply_plugin_update(sid, item["folder"], item["file"], item["download_url"], item.get("download_filename"))
                except Exception as exc:
                    print(f"Update check failed for {sid}:", exc)

        if dirty:
            save_meta(sid, meta)

def maintenance_loop():
    while True:
        time.sleep(MAINTENANCE_INTERVAL)
        try:
            run_scheduled_maintenance()
        except Exception as exc:
            print("Maintenance loop error:", exc)

@app.route("/api/server/<sid>/rcon", methods=["GET", "POST"])
def api_rcon(sid):
    denied = require_admin()
    if denied:
        return denied
    if not get_server_path(sid).exists():
        return jsonify({"ok": False, "error": "Server not found"}), 404
    meta = load_meta(sid)
    if request.method == "POST":
        data = request.json or {}
        try:
            port = max(1024, min(65535, int(data.get("port") or 25575)))
        except (TypeError, ValueError):
            return jsonify({"ok": False, "error": "RCON port must be a number"}), 400
        enabled = bool(data.get("enabled"))
        password = (data.get("password") or rcon_settings(meta)["password"] or secrets.token_urlsafe(12)).strip()
        meta["rcon"] = {"enabled": enabled, "port": port, "password": password}
        save_meta(sid, meta)
        write_properties(get_server_path(sid) / "server.properties", {
            "enable-rcon": "true" if enabled else "false",
            "rcon.port": port,
            "rcon.password": password,
            "broadcast-rcon-to-ops": "false"
        })
        return jsonify({"ok": True, "settings": meta["rcon"], "restart_required": is_running(sid)})
    settings = rcon_settings(meta)
    return jsonify({"ok": True, "settings": settings, "running": is_running(sid)})

@app.route("/api/server/<sid>/map")
def api_map(sid):
    if not get_server_path(sid).exists():
        return jsonify({"ok": False, "error": "Server not found"}), 404
    cached = map_cache.get(sid)
    if cached and time.time() - cached["at"] < MAP_CACHE_TTL:
        snapshot = cached["snapshot"]
    else:
        snapshot = rcon_player_snapshot(sid)
        map_cache[sid] = {"at": time.time(), "snapshot": snapshot}
    return jsonify({
        "ok": True,
        "running": is_running(sid),
        "players": snapshot["players"],
        "error": snapshot.get("error"),
        "markers": load_markers(sid)
    })

@app.route("/api/server/<sid>/map/markers", methods=["POST"])
def api_map_markers(sid):
    denied = require_admin()
    if denied:
        return denied
    if not get_server_path(sid).exists():
        return jsonify({"ok": False, "error": "Server not found"}), 404
    data = request.json or {}
    markers = load_markers(sid)
    if data.get("remove"):
        markers = [m for m in markers if m.get("id") != data["remove"]]
        save_markers(sid, markers)
        return jsonify({"ok": True, "markers": markers})
    label = (data.get("label") or "").strip()[:60]
    if not label:
        return jsonify({"ok": False, "error": "Give the structure a name"}), 400
    try:
        marker = {
            "id": uuid.uuid4().hex[:8],
            "label": label,
            "owner": (data.get("owner") or "").strip()[:40],
            "kind": (data.get("kind") or "base").strip()[:24],
            "x": float(data.get("x") or 0),
            "z": float(data.get("z") or 0)
        }
    except (TypeError, ValueError):
        return jsonify({"ok": False, "error": "Coordinates must be numbers"}), 400
    markers.append(marker)
    save_markers(sid, markers)
    return jsonify({"ok": True, "markers": markers, "marker": marker})

if os.environ.get("WERKZEUG_RUN_MAIN") == "true" or not DEV_MODE:
    threading.Thread(target=maintenance_loop, daemon=True).start()

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
