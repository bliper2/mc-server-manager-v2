"""Installing a modpack as a new server: the Modrinth (.mrpack) pipeline, and the routes for starting an install from
Modrinth or CurseForge and following its progress.

A .mrpack is a zip with `modrinth.index.json` (the files to download, each with checksums) plus `overrides/` and
`server-overrides/` folders that are copied into the server. Fabric, Forge and NeoForge packs are installed; Quilt is
refused with a message instead of producing a server that will not start."""

import hashlib
import io
import json
import re
import shutil
import threading
import time
import uuid
import zipfile

import requests
from flask import jsonify, request

from . import app
from .config import MAX_RAM_MB, MIN_RAM_MB, SERVERS_DIR
from .curseforge import configured as curseforge_configured, install_curseforge
from .loaders import check_supported, install_loader
from .packtools import (MAX_FILES, MAX_PACK_BYTES, MIN_FREE_BYTES, ModpackError, extract_overrides, fetch_bytes, finish_server, job_update,
                        new_server_dir, run_downloads, safe_target_parts, trusted_url)
from .providers import modrinth_version, primary_version_file
from .state import modpack_jobs
from .store import list_servers
from .util import clamp_ram

# The hosts the .mrpack specification allows a pack to download from.
PACK_HOSTS = {"cdn.modrinth.com", "github.com", "raw.githubusercontent.com", "gitlab.com"}
MAX_INDEX_BYTES = 5 * 1024 * 1024
LOADER_KEYS = {"fabric-loader": "fabric", "forge": "forge", "neoforge": "neoforge", "quilt-loader": "quilt"}

_install_lock = threading.Lock()


def trusted_pack_url(url) -> bool:
    return trusted_url(url, PACK_HOSTS)


def parse_index(raw: bytes) -> dict:
    try:
        index = json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError):
        raise ModpackError("modrinth.index.json is not valid")
    if not isinstance(index, dict) or index.get("game") != "minecraft" or index.get("formatVersion") != 1:
        raise ModpackError("This is not a Minecraft .mrpack (format 1)")
    if not isinstance(index.get("files"), list) or not isinstance(index.get("dependencies"), dict):
        raise ModpackError("The pack index is incomplete")
    if len(index["files"]) > MAX_FILES:
        raise ModpackError(f"The pack lists more than {MAX_FILES} files")
    return index


def loader_of(index: dict):
    """(Minecraft version, loader kind, loader version) from the pack's dependencies."""
    deps = index["dependencies"]
    minecraft = str(deps.get("minecraft") or "")
    for key, kind in LOADER_KEYS.items():
        if key in deps:
            check_supported(kind)
            return minecraft, kind, str(deps[key])
    raise ModpackError("This pack does not name a mod loader, so the manager cannot run it as a server")


def install_modpack(job_id: str, version_id: str, name: str, ram: int, port: int, title: str = ""):
    """Runs on a worker thread. Progress and the result are reported through modpack_jobs[job_id]. The caller holds the install lock."""
    job = modpack_jobs[job_id]
    server_dir = None
    try:
        job_update(job, "Looking up the pack", 1)
        version = modrinth_version(version_id)
        pack = primary_version_file(version)
        if not pack or not str(pack.get("filename", "")).endswith(".mrpack") or not trusted_pack_url(pack.get("url")):
            raise ModpackError("That version is not a downloadable .mrpack")
        job_update(job, "Downloading the pack", 3)
        data = fetch_bytes(pack["url"], MAX_PACK_BYTES)
        expected = (pack.get("hashes") or {}).get("sha1")
        if expected and hashlib.sha1(data).hexdigest() != expected.lower():
            raise ModpackError("The downloaded pack failed its checksum")
        try:
            bundle = zipfile.ZipFile(io.BytesIO(data))
            info = bundle.getinfo("modrinth.index.json")
        except (zipfile.BadZipFile, KeyError):
            raise ModpackError("This file is not a Modrinth modpack")
        if info.file_size > MAX_INDEX_BYTES:
            raise ModpackError("The pack index is unreasonably large")
        index = parse_index(bundle.read(info))
        minecraft, kind, loader_version = loader_of(index)

        # Plan everything that can fail on the pack's content before creating the server.
        entries = []
        for item in index["files"]:
            if not isinstance(item, dict) or (item.get("env") or {}).get("server") == "unsupported":
                continue  # client-only (shaders, minimaps...) has no place on a server
            parts = safe_target_parts(item.get("path"))
            hashes = item.get("hashes") or {}
            if not hashes.get("sha1") or not isinstance(item.get("downloads"), list):
                raise ModpackError(f'"{item.get("path")}" has no checksum or download address')
            entries.append((parts, item["downloads"], hashes["sha1"], hashes.get("sha512")))

        server_id, server_dir = new_server_dir(name)
        for folder in ("mods", "config"):
            (server_dir / folder).mkdir(exist_ok=True)
        entry = install_loader(kind, minecraft, loader_version, server_dir, lambda message: job_update(job, message, 8))
        run_downloads(entries, server_dir, job, PACK_HOSTS)
        job_update(job, "Applying the pack's settings", 90)
        extract_overrides(bundle, server_dir, ["overrides/", "server-overrides/"])
        job_update(job, "Finishing", 97)
        finish_server(server_id, server_dir, name, minecraft, kind, entry, ram, port, {
            "name": (title or str(index.get("name") or name))[:80],
            "version": str(index.get("versionId") or version.get("version_number") or "")[:40],
            "project_id": str(version.get("project_id") or "")[:20], "version_id": version_id, "mods": len(entries), "source": "modrinth",
        })
        job.update(state="done", message=f"Created \"{name}\" with {len(entries)} mods", progress=100, server_id=server_id, updated=time.time())
    except (ModpackError, requests.RequestException, zipfile.BadZipFile, OSError, KeyError, ValueError) as exc:
        if server_dir is not None:
            shutil.rmtree(server_dir, ignore_errors=True)
        message = str(exc) if isinstance(exc, ModpackError) else f"Install failed: {type(exc).__name__}: {exc}"
        job.update(state="error", message=message, progress=0, updated=time.time())
    finally:
        _install_lock.release()


def prune_jobs():
    for key in [k for k, v in modpack_jobs.items() if v["state"] != "running" and time.time() - v["updated"] > 3600]:
        modpack_jobs.pop(key, None)


@app.route("/api/modpacks/install", methods=["POST"])
def api_modpack_install():
    data = request.get_json(silent=True) or {}
    source = data.get("source") or "modrinth"
    name = str(data.get("name") or "").strip()
    if source not in ("modrinth", "curseforge"):
        return jsonify({"ok": False, "error": "Unknown modpack source"}), 400
    if source == "modrinth" and not re.fullmatch(r"[A-Za-z0-9]{6,16}", str(data.get("version_id") or "")):
        return jsonify({"ok": False, "error": "Choose a modpack version"}), 400
    if source == "curseforge":
        if not all(str(data.get(key) or "").isdigit() for key in ("project_id", "file_id")):
            return jsonify({"ok": False, "error": "Choose a modpack version"}), 400
        if not curseforge_configured():
            return jsonify({"ok": False, "error": "Add your CurseForge API key in Settings first"}), 400
    if not name or len(name) > 40 or any(ord(ch) < 32 for ch in name):
        return jsonify({"ok": False, "error": "Use 1 to 40 characters for the server name"}), 400
    if data.get("accept_eula") is not True:
        return jsonify({"ok": False, "error": "Accept the Minecraft EULA (https://aka.ms/MinecraftEULA) to create a server"}), 400
    try:
        port = int(data.get("port") or 25565)
        ram = int(data.get("ram") or 4096)
    except (TypeError, ValueError):
        return jsonify({"ok": False, "error": "Port and memory must be numbers"}), 400
    if not 1024 <= port <= 65535 or not MIN_RAM_MB <= ram <= MAX_RAM_MB:
        return jsonify({"ok": False, "error": "Port must be 1024-65535 and memory 512-65536 MB"}), 400
    taken = next((s for s in list_servers() if int(s.get("port") or 0) == port), None)
    if taken:
        return jsonify({"ok": False, "error": f"Port {port} is already used by \"{taken.get('name')}\". Pick another."}), 409
    if shutil.disk_usage(SERVERS_DIR).free < MIN_FREE_BYTES:
        return jsonify({"ok": False, "error": "Less than 3 GB of disk space is free. Free some space first."}), 507
    if not _install_lock.acquire(blocking=False):
        return jsonify({"ok": False, "error": "Another modpack is being installed. Wait for it to finish."}), 409
    prune_jobs()
    job_id = uuid.uuid4().hex[:12]
    modpack_jobs[job_id] = {"state": "running", "message": "Starting", "progress": 0, "server_id": None, "updated": time.time()}
    title = str(data.get("pack_title") or "").strip()[:80]
    if source == "curseforge":
        worker = (install_curseforge, (job_id, int(data["project_id"]), int(data["file_id"]), name, clamp_ram(ram), port, title))
    else:
        worker = (install_modpack, (job_id, str(data["version_id"]), name, clamp_ram(ram), port, title))
    threading.Thread(target=worker[0], args=worker[1], daemon=True, name="modpack-install").start()
    return jsonify({"ok": True, "job": job_id})


@app.route("/api/modpacks/job/<job_id>")
def api_modpack_job(job_id):
    job = modpack_jobs.get(job_id)
    if not job:
        return jsonify({"ok": False, "error": "Unknown install"}), 404
    return jsonify({"ok": True, **{key: job[key] for key in ("state", "message", "progress", "server_id")}})
