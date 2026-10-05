"""Small pure helpers: path safety, validation, host memory."""

import os
import re
import time
from pathlib import Path
from urllib.parse import urlparse

from .config import MAX_RAM_MB, MIN_RAM_MB
from .store import get_server_path

try:
    import psutil
except ImportError:
    psutil = None

def safe_path(server_id: str, rel: str):
    base = get_server_path(server_id).resolve()
    target = (base / rel).resolve()
    # A bare startswith() lets "servers/a" reach its sibling "servers/a_1700".
    if target != base and base not in target.parents:
        return None
    return target

SERVER_ID_PATTERN = re.compile(r"[\w\-]{1,80}")
DOWNLOAD_FOLDERS = ("plugins", "mods")
TRUSTED_DOWNLOAD_HOSTS = {"cdn.modrinth.com"}

def is_trusted_download(url) -> bool:
    try:
        parsed = urlparse(str(url))
    except ValueError:
        return False
    return parsed.scheme == "https" and (parsed.hostname or "") in TRUSTED_DOWNLOAD_HOSTS

def has_line_break(*values) -> bool:
    return any("\n" in str(v) or "\r" in str(v) for v in values if v is not None)

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
