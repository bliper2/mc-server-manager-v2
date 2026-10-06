"""Installing the mod loader a modpack needs: Fabric, Forge or NeoForge. Quilt is refused with a clear message.

Fabric is a single launcher jar. Forge and NeoForge ship an installer that is run once with `--installServer`; it downloads
the libraries and writes either a start-up arguments file (Minecraft 1.17 and newer) or a ready-to-run jar (older)."""

import hashlib
import json
import os
import re
import subprocess
from pathlib import Path
from urllib.parse import quote

import requests

from .javatools import choose_java, required_java_major
from .packtools import ModpackError, fetch_bytes, download_verified

FABRIC_META = "https://meta.fabricmc.net/v2"
FORGE_MAVEN = "https://maven.minecraftforge.net/net/minecraftforge/forge"
NEOFORGE_MAVEN = "https://maven.neoforged.net/releases/net/neoforged/neoforge"
INSTALLER_HOSTS = {"maven.minecraftforge.net", "maven.neoforged.net"}
LAUNCHER = "fabric-server-launch.jar"
SAFE_VERSION = re.compile(r"^[0-9A-Za-z.\-+_]{1,40}$")
LOADER_NAMES = {"fabric": "Fabric", "forge": "Forge", "neoforge": "NeoForge", "quilt": "Quilt"}
SUPPORTED_LOADERS = ("fabric", "forge", "neoforge")
INSTALL_TIMEOUT = 900


def check_supported(kind: str):
    if kind not in SUPPORTED_LOADERS:
        raise ModpackError(f"This pack needs {LOADER_NAMES.get(kind, kind)}, which the manager cannot install yet. "
                           "It supports Fabric, Forge and NeoForge packs.")


def args_filename() -> str:
    return "win_args.txt" if os.name == "nt" else "unix_args.txt"


def install_fabric(minecraft: str, loader: str, server_dir: Path) -> dict:
    try:
        installers = json.loads(fetch_bytes(f"{FABRIC_META}/versions/installer", 2 * 1024 * 1024))
        installer = next((item["version"] for item in installers if item.get("stable")), installers[0]["version"])
    except (requests.RequestException, ValueError, KeyError, IndexError):
        raise ModpackError("Could not reach the Fabric download service")
    url = f"{FABRIC_META}/versions/loader/{quote(minecraft)}/{quote(loader)}/{quote(installer)}/server/jar"
    try:
        (server_dir / LAUNCHER).write_bytes(fetch_bytes(url, 60 * 1024 * 1024))
    except requests.RequestException:
        raise ModpackError(f"Fabric has no server for Minecraft {minecraft} with loader {loader}")
    return {"jar": LAUNCHER}


def installer_url(kind: str, minecraft: str, version: str) -> str:
    if kind == "forge":
        full = f"{minecraft}-{version}"
        return f"{FORGE_MAVEN}/{quote(full)}/forge-{quote(full)}-installer.jar"
    if minecraft == "1.20.1":
        raise ModpackError("NeoForge for Minecraft 1.20.1 uses an older layout the manager cannot install. Use a Forge or Fabric pack.")
    return f"{NEOFORGE_MAVEN}/{quote(version)}/neoforge-{quote(version)}-installer.jar"


def find_entry(kind: str, minecraft: str, server_dir: Path) -> dict:
    """What to launch after the installer ran: an arguments file (new layout) or a jar (old layout)."""
    group = "minecraftforge/forge" if kind == "forge" else "neoforged/neoforge"
    for candidate in sorted((server_dir / "libraries" / "net").glob(f"{group}/*/{args_filename()}")):
        relative = candidate.parent.relative_to(server_dir).as_posix()
        return {"jar": f"{relative}/{args_filename()}", "entry": {"kind": "args", "dir": relative}}
    jars = [jar for jar in sorted(server_dir.glob("forge-*.jar")) if "installer" not in jar.name]
    if jars:
        return {"jar": min(jars, key=lambda jar: len(jar.name)).name}
    raise ModpackError(f"The {LOADER_NAMES[kind]} installer finished but produced nothing the manager can start")


def install_forge_like(kind: str, minecraft: str, version: str, server_dir: Path, step=lambda message: None) -> dict:
    required = required_java_major(minecraft)
    java, major = choose_java(required)
    if not java or (required and major and major < required):
        raise ModpackError(f"Installing {LOADER_NAMES[kind]} for Minecraft {minecraft} needs Java {required or 'newer'}. "
                           "Install it in Settings > Java, then try again.")
    url = installer_url(kind, minecraft, version)
    installer = server_dir / "installer.jar"
    step(f"Downloading the {LOADER_NAMES[kind]} installer")
    try:
        download_verified([url], installer, INSTALLER_HOSTS, 80 * 1024 * 1024)
        expected = None
        try:
            expected = fetch_bytes(url + ".sha1", 1024).decode("ascii", "ignore").strip().split()[0]
        except requests.HTTPError as missing:
            if missing.response is None or missing.response.status_code != 404:
                raise  # a repository without a checksum file is fine; any other failure is not
        except IndexError:
            pass
    except requests.RequestException as exc:
        installer.unlink(missing_ok=True)
        raise ModpackError(f"{LOADER_NAMES[kind]} {version} for Minecraft {minecraft} could not be downloaded ({type(exc).__name__})")
    if expected and hashlib.sha1(installer.read_bytes()).hexdigest() != expected.lower():
        installer.unlink(missing_ok=True)
        raise ModpackError(f"The {LOADER_NAMES[kind]} installer failed its checksum")
    step(f"Installing {LOADER_NAMES[kind]} (this takes a few minutes)")
    try:
        result = subprocess.run([java, "-jar", installer.name, "--installServer"], cwd=str(server_dir), capture_output=True, text=True, timeout=INSTALL_TIMEOUT)
    except subprocess.TimeoutExpired:
        raise ModpackError(f"The {LOADER_NAMES[kind]} installer did not finish within {INSTALL_TIMEOUT // 60} minutes")
    finally:
        installer.unlink(missing_ok=True)
        (server_dir / "installer.jar.log").unlink(missing_ok=True)
    if result.returncode != 0:
        tail = " ".join((result.stdout + result.stderr).strip().splitlines()[-3:])[:240]
        raise ModpackError(f"The {LOADER_NAMES[kind]} installer failed: {tail or 'no output'}")
    return find_entry(kind, minecraft, server_dir)


def install_loader(kind: str, minecraft: str, version: str, server_dir: Path, step=lambda message: None) -> dict:
    """Returns the meta fields that tell the manager how to start the server ({"jar": ...} and maybe {"entry": ...})."""
    check_supported(kind)
    if not SAFE_VERSION.match(minecraft) or not SAFE_VERSION.match(version):
        raise ModpackError("The pack names an invalid Minecraft or loader version")
    if kind == "fabric":
        step(f"Installing Fabric for Minecraft {minecraft}")
        return install_fabric(minecraft, version, server_dir)
    return install_forge_like(kind, minecraft, version, server_dir, step)
