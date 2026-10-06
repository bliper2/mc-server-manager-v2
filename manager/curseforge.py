"""CurseForge modpacks: browse with your own API key and install a pack as a server.

CurseForge's API needs a key (free at console.curseforge.com). It is stored only in manager_settings.json and never sent
back to the browser. Mods whose authors turned off third-party distribution have no download address; the manager does
not work around that, it names the mods and stops."""

import hashlib
import io
import json
import re
import shutil
import time
import zipfile

import requests
from flask import jsonify, request

from . import app
from .auth import require_owner
from .config import HEADERS
from .loaders import LOADER_NAMES, SUPPORTED_LOADERS, check_supported, install_loader
from .packtools import (MAX_FILES, MAX_PACK_BYTES, ModpackError, extract_overrides, extract_server_pack, fetch_bytes, finish_server,
                        job_update, new_server_dir, run_downloads, trusted_url)
from .state import modpack_jobs
from .store import load_settings, save_settings
from .util import sanitize_relative_parts

API = "https://api.curseforge.com/v1"
CF_HOSTS = {"edge.forgecdn.net", "mediafilez.forgecdn.net", "media.forgecdn.net"}
GAME_ID = 432          # Minecraft
MODPACK_CLASS = 4471
MAX_MANIFEST_BYTES = 5 * 1024 * 1024
RELEASE_TYPES = {1: "release", 2: "beta", 3: "alpha"}
KEY_PATTERN = re.compile(r"^[A-Za-z0-9$./_\-]{16,120}$")
KEY_LENGTH = 60        # CurseForge keys are bcrypt-style strings: "$2a$10$" followed by 53 characters
KEY_PREFIX = "$2a$10$"


class CurseForgeError(ModpackError):
    pass


def api_key() -> str:
    return str(load_settings().get("curseforge", {}).get("api_key") or "")


def configured() -> bool:
    return bool(api_key())


def cf_request(method: str, path: str, params=None, body=None, key: str | None = None):
    key = key or api_key()
    if not key:
        raise CurseForgeError("Add your CurseForge API key in Settings first")
    try:
        response = requests.request(method, API + path, params=params, json=body, timeout=20,
                                    headers={"x-api-key": key, "Accept": "application/json", "User-Agent": HEADERS["User-Agent"]})
    except requests.RequestException as exc:
        raise CurseForgeError(f"Could not reach CurseForge ({type(exc).__name__})")
    if response.status_code in (401, 403):
        # CurseForge answers 403 with an empty body for a missing, wrong, truncated or disabled key alike.
        app.logger.warning("CurseForge answered HTTP %s to a request for %s", response.status_code, path.split("?")[0])
        raise CurseForgeError(f"CurseForge rejected the API key (HTTP {response.status_code}). Check it in Settings.")
    if response.status_code == 429:
        raise CurseForgeError("CurseForge is rate limiting this key. Wait a minute and try again.")
    if response.status_code == 404:
        raise CurseForgeError("CurseForge does not have that pack or file")
    try:
        response.raise_for_status()
        return response.json()
    except (requests.RequestException, ValueError):
        raise CurseForgeError(f"CurseForge answered HTTP {response.status_code}")


def normalize_key(raw) -> str:
    """Pasted keys often carry spaces, a line break or surrounding quotes."""
    return "".join(str(raw or "").split()).strip("\"'`")


def key_shape_hint(key: str) -> str:
    """Why a rejected key probably is not a CurseForge key, said without repeating the key."""
    problems = []
    if len(key) != KEY_LENGTH:
        problems.append(f"has {len(key)} characters (a key has {KEY_LENGTH})")
    if not key.startswith(KEY_PREFIX):
        problems.append(f"does not start with {KEY_PREFIX}")
    return f" The text entered {' and '.join(problems)}. Copy the whole key with its Copy button in the CurseForge console." if problems else ""


def simplify(mod: dict) -> dict:
    authors = mod.get("authors") or [{}]
    return {
        "project_id": str(mod["id"]), "title": mod.get("name", ""), "description": mod.get("summary", ""),
        "icon_url": (mod.get("logo") or {}).get("thumbnailUrl") or "", "downloads": int(mod.get("downloadCount") or 0),
        "author": authors[0].get("name", ""), "url": (mod.get("links") or {}).get("websiteUrl", ""), "source": "curseforge",
    }


SEARCH_BLOCKED_NOTE = ("CurseForge has not enabled search for this API key (everything else works), so only featured packs are listed. "
                       "To use any other pack, open its CurseForge page and enter its Project ID in the box above.")
_search_blocked_until = 0.0


def featured_packs() -> list:
    """Modpacks from CurseForge's featured, popular and recently-updated lists. These work for every key."""
    groups = cf_request("POST", "/mods/featured", body={"gameId": GAME_ID, "excludedModIds": []}).get("data") or {}
    seen, packs = set(), []
    for name in ("featured", "popular", "recentlyUpdated"):
        for mod in groups.get(name, []):
            if mod.get("classId") == MODPACK_CLASS and mod["id"] not in seen:
                seen.add(mod["id"])
                packs.append(simplify(mod))
    return packs


def search_packs(query: str, offset: int) -> dict:
    """Searches CurseForge. Some keys are refused on /mods/search; then the featured list stands in and the answer says so."""
    global _search_blocked_until
    params = {"gameId": GAME_ID, "classId": MODPACK_CLASS, "pageSize": 30, "index": max(0, offset),
              "sortField": 2 if query else 6, "sortOrder": "desc"}
    if query:
        params["searchFilter"] = query
    if time.time() >= _search_blocked_until:
        try:
            data = cf_request("GET", "/mods/search", params)
            return {"hits": [simplify(mod) for mod in data.get("data", [])]}
        except CurseForgeError as exc:
            if "rejected" not in str(exc):
                raise
            # The same key may work elsewhere, so tell a dead key from a key that cannot search.
            cf_request("GET", f"/games/{GAME_ID}")
            _search_blocked_until = time.time() + 600
    packs = featured_packs()
    if query:
        needle = query.lower()
        packs = [p for p in packs if needle in p["title"].lower() or needle in p["description"].lower()]
    return {"hits": packs if offset == 0 else [], "search_available": False, "note": SEARCH_BLOCKED_NOTE}


def pack_by_id(mod_id: int) -> dict:
    mod = cf_request("GET", f"/mods/{mod_id}").get("data") or {}
    if mod.get("classId") != MODPACK_CLASS:
        raise CurseForgeError("That project is not a modpack")
    return simplify(mod)


def classify(file: dict) -> dict:
    versions = file.get("gameVersions") or []
    minecraft = next((v for v in versions if re.fullmatch(r"\d+\.\d+(\.\d+)?", v)), "")
    tokens = {v.lower() for v in versions}
    loader = "neoforge" if "neoforge" in tokens else next((k for k in ("forge", "fabric", "quilt") if k in tokens), "")
    return {
        "id": file["id"], "name": file.get("displayName") or file.get("fileName", ""), "minecraft": minecraft, "loader": loader,
        "loader_name": LOADER_NAMES.get(loader, "Unknown loader"), "supported": loader in SUPPORTED_LOADERS,
        "server_pack": bool(file.get("serverPackFileId")), "release": RELEASE_TYPES.get(file.get("releaseType"), "release"),
        "date": file.get("fileDate"),
    }


def pack_files(mod_id: int) -> list:
    data = cf_request("GET", f"/mods/{mod_id}/files", {"pageSize": 50})
    return [classify(f) for f in data.get("data", []) if not f.get("isServerPack") and f.get("isAvailable", True)]


def file_info(mod_id: int, file_id: int) -> dict:
    info = cf_request("GET", f"/mods/{mod_id}/files/{file_id}").get("data") or {}
    if int(info.get("modId") or 0) != mod_id:
        raise CurseForgeError("That file does not belong to the chosen pack")
    return info


def download_address(info: dict) -> str | None:
    url = info.get("downloadUrl")
    if not url:
        try:
            url = cf_request("GET", f"/mods/{info['modId']}/files/{info['id']}/download-url").get("data")
        except CurseForgeError:
            url = None
    return url if url and trusted_url(url, CF_HOSTS) else None


def sha1_of(info: dict) -> str | None:
    return next((h.get("value") for h in info.get("hashes", []) if h.get("algo") == 1), None)


def parse_manifest(bundle: zipfile.ZipFile) -> dict:
    try:
        info = bundle.getinfo("manifest.json")
    except KeyError:
        raise ModpackError("This is not a CurseForge modpack (there is no manifest.json)")
    if info.file_size > MAX_MANIFEST_BYTES:
        raise ModpackError("The pack manifest is unreasonably large")
    try:
        manifest = json.loads(bundle.read(info).decode("utf-8-sig"))
    except (UnicodeDecodeError, json.JSONDecodeError):
        raise ModpackError("The pack's manifest.json is not valid")
    if not isinstance(manifest, dict) or not isinstance(manifest.get("minecraft"), dict) or not isinstance(manifest.get("files"), list):
        raise ModpackError("The pack's manifest.json is incomplete")
    if len(manifest["files"]) > MAX_FILES:
        raise ModpackError(f"The pack lists more than {MAX_FILES} files")
    return manifest


def manifest_loader(manifest: dict):
    """(Minecraft version, loader kind, loader version) from the pack's primary mod loader."""
    minecraft = str(manifest["minecraft"].get("version") or "")
    loaders = [m for m in manifest["minecraft"].get("modLoaders", []) if isinstance(m, dict) and m.get("id")]
    chosen = next((m for m in loaders if m.get("primary")), loaders[0] if loaders else None)
    if not minecraft or not chosen:
        raise ModpackError("The pack does not say which Minecraft version and mod loader it needs")
    kind, _, version = str(chosen["id"]).partition("-")
    return minecraft, kind.lower(), version


def resolve_manifest_files(manifest: dict) -> list:
    """Download plan for the pack's own file list. Raises, naming the mods, if any cannot be downloaded."""
    wanted = [f for f in manifest["files"] if isinstance(f, dict) and f.get("required", True)]
    ids = [int(f["fileID"]) for f in wanted if str(f.get("fileID", "")).isdigit()]
    infos = {}
    for start in range(0, len(ids), 100):
        for info in cf_request("POST", "/mods/files", body={"fileIds": ids[start:start + 100]}).get("data", []):
            infos[info["id"]] = info
    blocked, entries = [], []
    for file_id in ids:
        info = infos.get(file_id)
        name = (info or {}).get("fileName") or f"file {file_id}"
        if info and not name.lower().endswith(".jar"):
            continue  # shaders and resource packs have no use on a server, so a block on them does not matter
        url = download_address(info) if info and info.get("isAvailable", True) else None
        parts = sanitize_relative_parts(name) if info else []
        if not url or len(parts) != 1:
            blocked.append(name)
            continue
        entries.append((["mods", parts[0]], [url], sha1_of(info), None))
    if blocked:
        shown = ", ".join(blocked[:8]) + (f" and {len(blocked) - 8} more" if len(blocked) > 8 else "")
        raise ModpackError(f"{len(blocked)} mod(s) cannot be downloaded because their authors turned off third-party downloads: {shown}. "
                           "Choose a pack version that has a server pack, or download those files from curseforge.com yourself.")
    return entries


def install_curseforge(job_id: str, project_id: int, file_id: int, name: str, ram: int, port: int, title: str = ""):
    """Runs on a worker thread; progress and the result go to modpack_jobs[job_id]. The caller holds the install lock."""
    from . import modpacks  # the lock and the Modrinth route live there; imported late to avoid a cycle
    job = modpack_jobs[job_id]
    server_dir = None
    try:
        job_update(job, "Looking up the pack", 1)
        info = file_info(project_id, file_id)
        url = download_address(info)
        if not url:
            raise ModpackError("The author of this pack does not allow it to be downloaded through CurseForge's API")
        job_update(job, "Downloading the pack", 3)
        data = fetch_bytes(url, MAX_PACK_BYTES)
        expected = sha1_of(info)
        if expected and hashlib.sha1(data).hexdigest() != expected.lower():
            raise ModpackError("The downloaded pack failed its checksum")
        try:
            bundle = zipfile.ZipFile(io.BytesIO(data))
        except zipfile.BadZipFile:
            raise ModpackError("This file is not a CurseForge modpack")
        manifest = parse_manifest(bundle)
        minecraft, kind, loader_version = manifest_loader(manifest)
        check_supported(kind)

        server_pack = None
        entries = []
        if info.get("serverPackFileId"):
            pack_info = file_info(project_id, int(info["serverPackFileId"]))
            pack_url = download_address(pack_info)
            if not pack_url:
                raise ModpackError("The pack's server pack cannot be downloaded through CurseForge's API")
            server_pack = (pack_url, sha1_of(pack_info))
        else:
            job_update(job, "Checking every mod can be downloaded", 5)
            entries = resolve_manifest_files(manifest)

        server_id, server_dir = new_server_dir(name)
        for folder in ("mods", "config"):
            (server_dir / folder).mkdir(exist_ok=True)
        entry = install_loader(kind, minecraft, loader_version, server_dir, lambda message: job_update(job, message, 8))
        if server_pack:
            job_update(job, "Downloading the server pack", 60)
            pack_bytes = fetch_bytes(server_pack[0], MAX_PACK_BYTES)
            if server_pack[1] and hashlib.sha1(pack_bytes).hexdigest() != server_pack[1].lower():
                raise ModpackError("The downloaded server pack failed its checksum")
            try:
                extract_server_pack(zipfile.ZipFile(io.BytesIO(pack_bytes)), server_dir)
            except zipfile.BadZipFile:
                raise ModpackError("The server pack is not a valid zip file")
            count = sum(1 for _ in (server_dir / "mods").glob("*.jar"))
        else:
            run_downloads(entries, server_dir, job, CF_HOSTS)
            job_update(job, "Applying the pack's settings", 90)
            extract_overrides(bundle, server_dir, [str(manifest.get("overrides") or "overrides").strip("/") + "/"])
            count = len(entries)
        job_update(job, "Finishing", 97)
        finish_server(server_id, server_dir, name, minecraft, kind, entry, ram, port, {
            "name": (title or str(manifest.get("name") or name))[:80], "version": str(manifest.get("version") or "")[:40],
            "project_id": str(project_id), "version_id": str(file_id), "mods": count, "source": "curseforge",
            "client_pack": server_pack is None,
        })
        note = "" if server_pack else " This pack has no server pack, so remove any client-only mods if the server crashes on start."
        job.update(state="done", message=f"Created \"{name}\" with {count} mods.{note}", progress=100, server_id=server_id, updated=time.time())
    except (ModpackError, requests.RequestException, zipfile.BadZipFile, OSError, KeyError, ValueError) as exc:
        if server_dir is not None:
            shutil.rmtree(server_dir, ignore_errors=True)
        message = str(exc) if isinstance(exc, ModpackError) else f"Install failed: {type(exc).__name__}: {exc}"
        job.update(state="error", message=message, progress=0, updated=time.time())
    finally:
        modpacks._install_lock.release()


# ----- routes -----

def failure(exc: ModpackError):
    return jsonify({"ok": False, "error": str(exc)}), 400


@app.route("/api/curseforge/status")
def api_curseforge_status():
    return jsonify({"ok": True, "configured": configured()})


@app.route("/api/curseforge/search")
def api_curseforge_search():
    try:
        return jsonify({"ok": True, **search_packs(request.args.get("q", "").strip()[:80], request.args.get("offset", 0, type=int))})
    except ModpackError as exc:
        return failure(exc)


@app.route("/api/curseforge/pack/<int:mod_id>")
def api_curseforge_pack(mod_id):
    try:
        return jsonify({"ok": True, "pack": pack_by_id(mod_id)})
    except ModpackError as exc:
        return failure(exc)


@app.route("/api/curseforge/files/<int:mod_id>")
def api_curseforge_files(mod_id):
    try:
        return jsonify({"ok": True, "files": pack_files(mod_id)})
    except ModpackError as exc:
        return failure(exc)


@app.route("/api/curseforge/key", methods=["GET", "POST"])
def api_curseforge_key():
    denied = require_owner()
    if denied:
        return denied
    if request.method == "POST":
        key = normalize_key((request.get_json(silent=True) or {}).get("key"))
        settings = load_settings()
        if key:
            if not KEY_PATTERN.match(key):
                return jsonify({"ok": False, "error": "That does not look like a CurseForge API key" + key_shape_hint(key)}), 400
            try:
                cf_request("GET", f"/games/{GAME_ID}", key=key)  # a quick call that fails with 403 on a wrong key
            except CurseForgeError as exc:
                hint = key_shape_hint(key) if "rejected" in str(exc) else ""
                return jsonify({"ok": False, "error": str(exc) + hint}), 400
            except ModpackError as exc:
                return failure(exc)
        settings.setdefault("curseforge", {})["api_key"] = key
        save_settings(settings)
    key = api_key()
    return jsonify({"ok": True, "configured": bool(key), "hint": f"...{key[-4:]}" if key else ""})
