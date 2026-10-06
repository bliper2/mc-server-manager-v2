"""On-disk server metadata and the audit trail."""

import json
import os
import time
import uuid
from datetime import datetime
from pathlib import Path

from .config import AUDIT_FILE, SERVERS_DIR, SETTINGS_FILE
from .state import audit_lock, playit_processes, running_servers

def get_server_path(server_id: str) -> Path:
    return SERVERS_DIR / server_id

def replace_with_retry(temp: Path, target: Path):
    """os.replace that rides out Windows' brief "Access is denied" while a reader (or a virus scanner) has the target open."""
    for attempt in range(8):
        try:
            os.replace(temp, target)
            return
        except PermissionError:
            if attempt == 7:
                raise
            time.sleep(0.05 * (attempt + 1))

def write_json_atomic(path: Path, data, indent=None):
    """Writes through a uniquely named temporary file, so two writers never share one and a crash never leaves half a file."""
    temp = path.with_name(f"{path.name}.{uuid.uuid4().hex[:8]}.tmp")
    try:
        temp.write_text(json.dumps(data, indent=indent), encoding="utf-8")
        replace_with_retry(temp, path)
    finally:
        temp.unlink(missing_ok=True)

def load_meta(server_id: str) -> dict:
    f = get_server_path(server_id) / "manager_meta.json"
    if f.exists():
        return json.loads(f.read_text(encoding="utf-8"))
    return {}

def save_meta(server_id: str, data: dict):
    f = get_server_path(server_id) / "manager_meta.json"
    write_json_atomic(f, data, indent=2)

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
            try:
                m = load_meta(d.name)
            except (OSError, json.JSONDecodeError):
                continue  # one corrupt manager_meta.json must not hide every other server
            m["id"] = d.name
            m["running"] = is_running(d.name)
            m["playit_running"] = is_playit_running(d.name)
            out.append(m)
    return sorted(out, key=lambda x: x.get("created", ""), reverse=True)

def audit(user: str, action: str, **detail):
    entry = {"at": datetime.now().isoformat(timespec="seconds"), "user": user, "action": action, **detail}
    try:
        with audit_lock:
            if AUDIT_FILE.exists() and AUDIT_FILE.stat().st_size > 1_000_000:
                tail = AUDIT_FILE.read_text(encoding="utf-8", errors="replace").splitlines()[-500:]
                AUDIT_FILE.write_text("\n".join(tail) + "\n", encoding="utf-8")
            with open(AUDIT_FILE, "a", encoding="utf-8") as handle:
                handle.write(json.dumps(entry) + "\n")
    except OSError:
        pass

def read_audit(limit: int = 80, server: str | None = None) -> list:
    """Newest first. With `server`, only entries recorded against that server."""
    try:
        lines = AUDIT_FILE.read_text(encoding="utf-8", errors="replace").splitlines()
    except OSError:
        return []
    entries = []
    for line in reversed(lines):
        try:
            entry = json.loads(line)
        except json.JSONDecodeError:
            continue
        if server is not None and entry.get("server") != server:
            continue
        entries.append(entry)
        if len(entries) >= limit:
            break
    return entries

DEFAULT_SETTINGS = {
    "notifications": {
        "webhook": "",
        "events": {}
    },
    "curseforge": {
        "api_key": ""
    },
    "status": {}  # per server: the Discord status board (webhook, address, refresh interval, message id)
}

def load_settings() -> dict:
    """Manager-wide settings (not tied to one server). Missing or corrupt files fall back to defaults."""
    try:
        stored = json.loads(SETTINGS_FILE.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        stored = {}
    merged = json.loads(json.dumps(DEFAULT_SETTINGS))
    if isinstance(stored, dict):
        for key, value in stored.items():
            if isinstance(value, dict) and isinstance(merged.get(key), dict):
                merged[key].update(value)
            else:
                merged[key] = value
    return merged

def save_settings(settings: dict):
    with audit_lock:
        write_json_atomic(SETTINGS_FILE, settings, indent=2)

def playtime_file(server_id: str) -> Path:
    return get_server_path(server_id) / "manager_playtime.json"

def load_playtime(server_id: str) -> dict:
    try:
        data = json.loads(playtime_file(server_id).read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else {}
    except (OSError, json.JSONDecodeError):
        return {}

def add_playtime(server_id: str, player: str, seconds: float):
    """Adds one finished session to a player's total. Sessions under five seconds (reconnect flaps) are ignored."""
    if seconds < 5 or not get_server_path(server_id).is_dir():
        return
    with audit_lock:
        data = load_playtime(server_id)
        entry = data.get(player) or {"seconds": 0, "sessions": 0}
        entry["seconds"] = int(entry["seconds"] + seconds)
        entry["sessions"] = int(entry["sessions"]) + 1
        entry["last_seen"] = datetime.now().isoformat(timespec="seconds")
        data[player] = entry
        write_json_atomic(playtime_file(server_id), data)
