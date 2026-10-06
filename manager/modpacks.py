"""Modrinth modpacks (.mrpack): install one as a new Fabric server.

A .mrpack is a zip with `modrinth.index.json` (the files to download, each with checksums) plus `overrides/` and
`server-overrides/` folders that are copied into the server. Only Fabric packs are installed; the manager cannot yet run
the Forge, NeoForge or Quilt installers, and says so instead of producing a server that will not start."""

import hashlib
import io
import json
import re
import shutil
import threading
import time
import uuid
import zipfile
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime
from urllib.parse import quote, urlparse

import requests
from flask import jsonify, request

from . import app
from .config import HEADERS, MAX_RAM_MB, MIN_RAM_MB, SERVERS_DIR
from .providers import modrinth_version, primary_version_file
from .state import modpack_jobs
from .store import get_server_path, list_servers, save_meta
from .util import clamp_ram, sanitize_relative_parts, unique_server_id

FABRIC_META = "https://meta.fabricmc.net/v2"
# The hosts the .mrpack specification allows a pack to download from.
PACK_HOSTS = {"cdn.modrinth.com", "github.com", "raw.githubusercontent.com", "gitlab.com"}
MAX_PACK_BYTES = 400 * 1024 * 1024
MAX_FILE_BYTES = 300 * 1024 * 1024
MAX_INDEX_BYTES = 5 * 1024 * 1024
MAX_FILES = 1500
MAX_OVERRIDE_BYTES = 2 * 1024 * 1024 * 1024
MIN_FREE_BYTES = 3 * 1024 * 1024 * 1024
DOWNLOAD_WORKERS = 4
LAUNCHER = "fabric-server-launch.jar"
# Never writable from a pack: the manager's own files and the launcher.
ROOT_PROTECTED = {"manager_meta.json", "manager_playtime.json", "eula.txt", "server.properties", LAUNCHER, "server.jar", "staff.json"}
SAFE_VERSION = re.compile(r"^[0-9A-Za-z.\-+_]{1,40}$")
UNSUPPORTED_LOADERS = {"forge": "Forge", "neoforge": "NeoForge", "quilt-loader": "Quilt"}

_install_lock = threading.Lock()


class ModpackError(Exception):
    """A problem with the pack itself or the download, phrased for the person installing it."""


def trusted_pack_url(url) -> bool:
    try:
        parsed = urlparse(str(url))
    except ValueError:
        return False
    return parsed.scheme == "https" and (parsed.hostname or "") in PACK_HOSTS


def _stream(url: str, limit: int):
    """Yields the response body in chunks and refuses anything bigger than `limit` bytes."""
    with requests.get(url, headers=HEADERS, stream=True, timeout=(10, 60)) as response:
        response.raise_for_status()
        if not response.url.lower().startswith("https://"):
            raise ModpackError("A download was redirected to an unencrypted address")
        total = 0
        for chunk in response.iter_content(65536):
            total += len(chunk)
            if total > limit:
                raise ModpackError("A download was larger than the allowed size")
            yield chunk


def fetch_bytes(url: str, limit: int) -> bytes:
    return b"".join(_stream(url, limit))


def download_verified(urls, destination, limit: int, sha1: str, sha512: str | None = None):
    """Tries each mirror in turn and keeps the file only if its checksums match."""
    last = "no download address"
    for url in urls:
        if not trusted_pack_url(url):
            last = f"{urlparse(str(url)).hostname or 'that address'} is not an allowed download host"
            continue
        digest_1, digest_512 = hashlib.sha1(), hashlib.sha512()
        temp = destination.with_name(destination.name + ".part")
        try:
            with open(temp, "wb") as handle:
                for chunk in _stream(url, limit):
                    digest_1.update(chunk)
                    digest_512.update(chunk)
                    handle.write(chunk)
        except (requests.RequestException, OSError) as exc:
            temp.unlink(missing_ok=True)
            last = f"{type(exc).__name__}"
            continue
        if digest_1.hexdigest() != sha1.lower() or (sha512 and digest_512.hexdigest() != sha512.lower()):
            temp.unlink(missing_ok=True)
            raise ModpackError(f"{destination.name} failed its checksum, so it was not installed")
        temp.replace(destination)
        return
    raise ModpackError(f"Could not download {destination.name}: {last}")


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


def fabric_versions(index: dict):
    """(minecraft version, fabric loader version). Refuses every other loader with a clear reason."""
    deps = index["dependencies"]
    for key, label in UNSUPPORTED_LOADERS.items():
        if key in deps:
            raise ModpackError(f"This pack needs {label}. The manager can install Fabric packs for now.")
    if "fabric-loader" not in deps:
        raise ModpackError("This pack does not use Fabric, so the manager cannot run it as a server")
    minecraft, loader = str(deps.get("minecraft") or ""), str(deps["fabric-loader"])
    if not SAFE_VERSION.match(minecraft) or not SAFE_VERSION.match(loader):
        raise ModpackError("The pack names an invalid Minecraft or Fabric version")
    return minecraft, loader


def pack_file_path(path) -> list | None:
    """Safe path parts for a file the pack wants to place, or None to skip it. Raises for paths that try to escape."""
    parts = sanitize_relative_parts(str(path or ""))
    if not parts or len(parts) < 2 and parts[0] in ROOT_PROTECTED:
        raise ModpackError(f'The pack tries to write "{path}", which is not allowed')
    return parts


def install_fabric(minecraft: str, loader: str, destination):
    try:
        installers = json.loads(fetch_bytes(f"{FABRIC_META}/versions/installer", 2 * 1024 * 1024))
        installer = next((item["version"] for item in installers if item.get("stable")), installers[0]["version"])
    except (requests.RequestException, ValueError, KeyError, IndexError):
        raise ModpackError("Could not reach the Fabric download service")
    url = f"{FABRIC_META}/versions/loader/{quote(minecraft)}/{quote(loader)}/{quote(installer)}/server/jar"
    try:
        destination.write_bytes(fetch_bytes(url, 60 * 1024 * 1024))
    except requests.RequestException:
        raise ModpackError(f"Fabric has no server for Minecraft {minecraft} with loader {loader}")


def apply_overrides(bundle: zipfile.ZipFile, server_dir):
    """Copies overrides/ then server-overrides/ into the server. Entries are read and written as plain files, so a
    crafted archive cannot create links or leave the server folder."""
    total = 0
    for prefix in ("overrides/", "server-overrides/"):
        for info in bundle.infolist():
            if info.is_dir() or not info.filename.startswith(prefix):
                continue
            parts = sanitize_relative_parts(info.filename[len(prefix):])
            if not parts or (len(parts) == 1 and parts[0] in ROOT_PROTECTED):
                continue
            total += info.file_size
            if info.file_size > MAX_FILE_BYTES or total > MAX_OVERRIDE_BYTES:
                raise ModpackError("The pack's settings folder is unreasonably large")
            target = server_dir.joinpath(*parts)
            target.parent.mkdir(parents=True, exist_ok=True)
            with bundle.open(info) as source, open(target, "wb") as handle:
                shutil.copyfileobj(source, handle)


def job_update(job: dict, message: str, progress: int):
    job.update(message=message, progress=progress, updated=time.time())


def install_modpack(job_id: str, version_id: str, name: str, ram: int, port: int, title: str = ""):
    """Runs on a worker thread. Progress and the result are reported through modpack_jobs[job_id]."""
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
        minecraft, loader = fabric_versions(index)

        server_id = unique_server_id(name)
        while get_server_path(server_id).exists():
            server_id += "x"
        server_dir = get_server_path(server_id)
        server_dir.mkdir(parents=True)
        for folder in ("mods", "config"):
            (server_dir / folder).mkdir()
        job_update(job, f"Installing Fabric for Minecraft {minecraft}", 8)
        install_fabric(minecraft, loader, server_dir / LAUNCHER)

        entries = []
        for item in index["files"]:
            if not isinstance(item, dict) or (item.get("env") or {}).get("server") == "unsupported":
                continue  # client-only (shaders, minimaps...) has no place on a server
            parts = pack_file_path(item.get("path"))
            hashes = item.get("hashes") or {}
            if not hashes.get("sha1") or not isinstance(item.get("downloads"), list):
                raise ModpackError(f'"{item.get("path")}" has no checksum or download address')
            entries.append((parts, item["downloads"], hashes))
        done = {"count": 0}
        lock = threading.Lock()

        def fetch_one(entry):
            parts, urls, hashes = entry
            target = server_dir.joinpath(*parts)
            target.parent.mkdir(parents=True, exist_ok=True)
            download_verified(urls, target, MAX_FILE_BYTES, hashes["sha1"], hashes.get("sha512"))
            with lock:
                done["count"] += 1
                job_update(job, f"Downloading mods ({done['count']} of {len(entries)})", 10 + int(done["count"] / max(1, len(entries)) * 78))

        if entries:
            with ThreadPoolExecutor(max_workers=DOWNLOAD_WORKERS) as pool:
                futures = [pool.submit(fetch_one, entry) for entry in entries]
                try:
                    for future in futures:
                        future.result()
                except BaseException:
                    for future in futures:
                        future.cancel()  # stop queued downloads; the few already running finish
                    raise
        job_update(job, "Applying the pack's settings", 90)
        apply_overrides(bundle, server_dir)
        job_update(job, "Finishing", 97)
        (server_dir / "server.properties").write_text(
            f"server-port={port}\nmotd={name.replace(chr(10), ' ')}\nmax-players=20\nview-distance=10\nonline-mode=true\n", encoding="utf-8")
        save_meta(server_id, {
            "name": name, "type": "fabric", "version": minecraft, "jar": LAUNCHER, "ram": ram, "port": port,
            "logo": {"mark": name[:2].upper(), "style": "avatar-blue"}, "eula_accepted": True,
            "modpack": {"name": (title or str(index.get("name") or name))[:80], "version": str(index.get("versionId") or version.get("version_number") or "")[:40],
                        "project_id": str(version.get("project_id") or "")[:20], "version_id": version_id, "mods": len(entries)},
            "created": datetime.now().isoformat(),
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
    version_id = str(data.get("version_id") or "")
    name = str(data.get("name") or "").strip()
    if not re.fullmatch(r"[A-Za-z0-9]{6,16}", version_id):
        return jsonify({"ok": False, "error": "Choose a modpack version"}), 400
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
    threading.Thread(target=install_modpack, args=(job_id, version_id, name, clamp_ram(ram), port, str(data.get("pack_title") or "").strip()[:80]), daemon=True, name="modpack-install").start()
    return jsonify({"ok": True, "job": job_id})


@app.route("/api/modpacks/job/<job_id>")
def api_modpack_job(job_id):
    job = modpack_jobs.get(job_id)
    if not job:
        return jsonify({"ok": False, "error": "Unknown install"}), 404
    return jsonify({"ok": True, **{key: job[key] for key in ("state", "message", "progress", "server_id")}})
