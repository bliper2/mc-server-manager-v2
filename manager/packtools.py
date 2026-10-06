"""Shared building blocks for installing a modpack as a server: safe downloads, safe extraction, parallel fetching."""

import hashlib
import shutil
import threading
import time
import zipfile
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime
from urllib.parse import urlparse

import requests

from .config import HEADERS
from .store import get_server_path, save_meta
from .util import sanitize_relative_parts, unique_server_id

MAX_PACK_BYTES = 400 * 1024 * 1024
MAX_FILE_BYTES = 300 * 1024 * 1024
MAX_EXTRACT_BYTES = 2 * 1024 * 1024 * 1024
MAX_FILES = 1500
MIN_FREE_BYTES = 3 * 1024 * 1024 * 1024
DOWNLOAD_WORKERS = 4
# Never writable from a pack: the manager's own files and the launcher.
ROOT_PROTECTED = {"manager_meta.json", "manager_playtime.json", "eula.txt", "server.properties", "fabric-server-launch.jar", "server.jar", "staff.json"}
# Root-level entries a server pack ships for its own start-up that must not be copied: the manager launches the server itself.
SCRIPT_SUFFIXES = (".bat", ".sh", ".cmd", ".ps1", ".command", ".exe", ".jar")
SKIP_ROOT_DIRS = {"libraries", "versions", ".fabric", "logs", "crash-reports", "cache", "backups"}
KNOWN_ROOT_DIRS = {"mods", "config", "defaultconfigs", "kubejs", "scripts", "resourcepacks", "datapacks", "world", "libraries", "plugins"}


class ModpackError(Exception):
    """A problem with the pack or its download, phrased for the person installing it."""


def trusted_url(url, hosts) -> bool:
    try:
        parsed = urlparse(str(url))
    except ValueError:
        return False
    return parsed.scheme == "https" and (parsed.hostname or "") in hosts


def _stream(url: str, limit: int, headers=None):
    """Yields the response body in chunks and refuses anything bigger than `limit` bytes."""
    with requests.get(url, headers=headers or HEADERS, stream=True, timeout=(10, 60)) as response:
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


def download_verified(urls, destination, hosts, limit: int = MAX_FILE_BYTES, sha1: str | None = None, sha512: str | None = None):
    """Tries each mirror in turn and keeps the file only if the checksums that were given match."""
    last = "no download address"
    for url in urls:
        if not trusted_url(url, hosts):
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
            last = type(exc).__name__
            continue
        if (sha1 and digest_1.hexdigest() != sha1.lower()) or (sha512 and digest_512.hexdigest() != sha512.lower()):
            temp.unlink(missing_ok=True)
            raise ModpackError(f"{destination.name} failed its checksum, so it was not installed")
        temp.replace(destination)
        return
    raise ModpackError(f"Could not download {destination.name}: {last}")


def safe_target_parts(path) -> list:
    """Safe path parts for a file a pack wants to place. Raises for anything that tries to escape or touch protected files."""
    parts = sanitize_relative_parts(str(path or ""))
    if not parts or (len(parts) < 2 and parts[0] in ROOT_PROTECTED):
        raise ModpackError(f'The pack tries to write "{path}", which is not allowed')
    return parts


def job_update(job: dict, message: str, progress: int):
    job.update(message=message, progress=progress, updated=time.time())


def new_server_dir(name: str):
    server_id = unique_server_id(name)
    while get_server_path(server_id).exists():
        server_id += "x"
    folder = get_server_path(server_id)
    folder.mkdir(parents=True)
    return server_id, folder


def run_downloads(entries, server_dir, job, hosts, first=10, last=88):
    """entries: (path parts, [urls], sha1, sha512). Four at a time; one failure cancels the queued rest."""
    if not entries:
        return
    done = {"count": 0}
    lock = threading.Lock()

    def fetch_one(entry):
        parts, urls, sha1, sha512 = entry
        target = server_dir.joinpath(*parts)
        target.parent.mkdir(parents=True, exist_ok=True)
        download_verified(urls, target, hosts, MAX_FILE_BYTES, sha1, sha512)
        with lock:
            done["count"] += 1
            job_update(job, f"Downloading mods ({done['count']} of {len(entries)})", first + int(done["count"] / len(entries) * (last - first)))

    with ThreadPoolExecutor(max_workers=DOWNLOAD_WORKERS) as pool:
        futures = [pool.submit(fetch_one, entry) for entry in entries]
        try:
            for future in futures:
                future.result()
        except BaseException:
            for future in futures:
                future.cancel()  # stop queued downloads; the few already running finish
            raise


def extract_overrides(bundle: zipfile.ZipFile, server_dir, prefixes):
    """Copies the given folders of the pack zip into the server. Entries are read and written as plain files, so a crafted
    archive cannot create links or leave the server folder. Later prefixes win over earlier ones."""
    total = 0
    for prefix in prefixes:
        for info in bundle.infolist():
            if info.is_dir() or not info.filename.startswith(prefix):
                continue
            parts = sanitize_relative_parts(info.filename[len(prefix):])
            if not parts or (len(parts) == 1 and parts[0] in ROOT_PROTECTED):
                continue
            total += info.file_size
            if info.file_size > MAX_FILE_BYTES or total > MAX_EXTRACT_BYTES:
                raise ModpackError("The pack's settings folder is unreasonably large")
            target = server_dir.joinpath(*parts)
            target.parent.mkdir(parents=True, exist_ok=True)
            with bundle.open(info) as source, open(target, "wb") as handle:
                shutil.copyfileobj(source, handle)


def extract_server_pack(bundle: zipfile.ZipFile, server_dir):
    """Copies a CurseForge server pack's content (mods, configs...) but not its start scripts, installers or libraries."""
    members = [(info, sanitize_relative_parts(info.filename)) for info in bundle.infolist() if not info.is_dir()]
    members = [(info, parts) for info, parts in members if parts]
    tops = {parts[0] for _, parts in members}
    # Server packs are often zipped inside one wrapper folder ("ServerFiles-1.2/"); step into it.
    strip = len(tops) == 1 and next(iter(tops)) not in KNOWN_ROOT_DIRS and all(len(parts) > 1 for _, parts in members)
    total = 0
    for info, parts in members:
        if strip:
            parts = parts[1:]
        if len(parts) == 1 and (parts[0] in ROOT_PROTECTED or parts[0].lower().endswith(SCRIPT_SUFFIXES) or parts[0] == "user_jvm_args.txt"):
            continue
        if parts[0] in SKIP_ROOT_DIRS:
            continue
        total += info.file_size
        if info.file_size > MAX_FILE_BYTES or total > MAX_EXTRACT_BYTES:
            raise ModpackError("The server pack is unreasonably large")
        target = server_dir.joinpath(*parts)
        target.parent.mkdir(parents=True, exist_ok=True)
        with bundle.open(info) as source, open(target, "wb") as handle:
            shutil.copyfileobj(source, handle)


def finish_server(server_id: str, server_dir, name: str, minecraft: str, kind: str, entry: dict, ram: int, port: int, modpack: dict):
    """Writes server.properties and the manager's metadata. The server is now listed and can be started."""
    motd = name.replace(chr(10), " ")
    properties = ["server-port=" + str(port), "motd=" + motd, "max-players=20", "view-distance=10", "online-mode=true", ""]
    (server_dir / "server.properties").write_text(chr(10).join(properties), encoding="utf-8")
    save_meta(server_id, {
        "name": name, "type": kind, "version": minecraft, "ram": ram, "port": port,
        "logo": {"mark": name[:2].upper(), "style": "avatar-blue"}, "eula_accepted": True,
        "modpack": modpack, "created": datetime.now().isoformat(), **entry,
    })
