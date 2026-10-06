"""Server jar downloads (Paper, Purpur, Mojang) and the Modrinth client."""

import hashlib
import json
import time
from pathlib import Path

import requests
from werkzeug.utils import secure_filename

from .compat import bytes_problem, loaders_for
from .config import HEADERS
from .state import update_cache
from .store import get_server_path, load_meta
from .util import DOWNLOAD_FOLDERS, is_trusted_download

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

def modrinth_search(query, project_type="plugin", limit=24, offset=0, game_version=None, loader=None, index="relevance"):
    try:
        facets = []
        if project_type == "plugin":
            facets.append(["project_type:plugin"])
            facets.append(["categories:bukkit", "categories:spigot", "categories:paper", "categories:purpur"])
        elif project_type == "mod":
            facets.append(["project_type:mod"])
            if loader:
                facets.append([f"categories:{loader}"])
        elif project_type == "modpack":
            # Only packs the manager can run: Fabric, Forge or NeoForge, and not client-only.
            facets.append(["project_type:modpack"])
            facets.append(["categories:fabric", "categories:forge", "categories:neoforge"])
            facets.append(["server_side:required", "server_side:optional"])
        if game_version:
            facets.append([f"versions:{game_version}"])
        params = {"query": query or "", "limit": limit, "offset": offset, "index": index}
        if facets:
            params["facets"] = json.dumps(facets)
        r = requests.get("https://api.modrinth.com/v2/search", params=params, headers=HEADERS, timeout=20)
        r.raise_for_status()
        return r.json()
    except Exception as e:
        return {"hits": [], "error": str(e)}

def modrinth_version(version_id):
    """One version of a project, including its files. Raises on network or HTTP errors."""
    response = requests.get(f"https://api.modrinth.com/v2/version/{version_id}", headers=HEADERS, timeout=20)
    response.raise_for_status()
    return response.json()

def modrinth_versions(project_id, game_version=None, loader=None):
    try:
        params = {}
        if game_version:
            params["game_versions"] = json.dumps([game_version])
        if loader:
            params["loaders"] = json.dumps(loader if isinstance(loader, list) else [loader])
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
    meta = load_meta(server_id)
    game_version = meta.get("version")
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
            # Only versions for a loader this server runs: Modrinth lists Fabric and NeoForge builds of the same project too.
            loaders = loaders_for(meta.get("type"), folder_name) or None
            candidates = [v for v in modrinth_versions(project_id, game_version=game_version, loader=loaders) if v.get("id") != current.get("id")]
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
    if folder_name not in DOWNLOAD_FOLDERS:
        return False, "Unknown folder"
    if not is_trusted_download(download_url):
        return False, "Downloads are only allowed from cdn.modrinth.com"
    folder = get_server_path(server_id) / folder_name
    target = folder / secure_filename(filename)
    if not target.exists():
        return False, "That plugin or mod file is no longer there"
    content = download_url_bytes(download_url)
    if not content:
        return False, "Could not download the new version"
    saved_name = secure_filename(new_filename or filename)
    problem = bytes_problem(content, saved_name, load_meta(server_id).get("type"), folder_name)
    if problem:
        return False, f"Not installed. {problem}"
    try:
        if target.name != saved_name:
            target.unlink(missing_ok=True)
        (folder / saved_name).write_bytes(content)
        return True, saved_name
    except OSError as exc:
        return False, f"Could not replace the file (is the server running?): {exc}"
