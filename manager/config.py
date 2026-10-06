"""Paths and constants shared by every module."""

import os
from pathlib import Path

VERSION = "2.5.2"
DEV_MODE = os.environ.get("MC_MANAGER_DEV", "1") == "1"
# Exit code that tells the supervisor (app.py or the Werkzeug reloader) to start the manager again.
RESTART_EXIT_CODE = 3
PORT = int(os.environ.get("MC_MANAGER_PORT", "5000"))
OWNER_WATERMARK = "MC-SERVER-MANAGER / Mrkraps aka orgeco"

# BASE_DIR holds the code, templates and static files. DATA_DIR holds everything the manager writes
# (servers, backups, accounts, settings); MC_MANAGER_HOME moves it, which the test-suite relies on.
BASE_DIR = Path(__file__).resolve().parent.parent
DATA_DIR = Path(os.environ.get("MC_MANAGER_HOME") or BASE_DIR).resolve()
DATA_DIR.mkdir(parents=True, exist_ok=True)
SERVERS_DIR = DATA_DIR / "servers"
SERVERS_DIR.mkdir(exist_ok=True)
BACKUPS_DIR = DATA_DIR / "backups"
BACKUPS_DIR.mkdir(exist_ok=True)
IMPORTS_DIR = DATA_DIR / ".imports"
IMPORTS_DIR.mkdir(exist_ok=True)
SECRET_FILE = DATA_DIR / ".secret_key"
STAFF_FILE = DATA_DIR / "staff.json"
AUDIT_FILE = DATA_DIR / "audit.jsonl"
SETTINGS_FILE = DATA_DIR / "manager_settings.json"
RESTART_STATE_FILE = DATA_DIR / "restart_state.json"
LOG_FILE = DATA_DIR / "manager.log"

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

RCON_AUTH = 3
RCON_COMMAND = 2
RCON_TIMEOUT = 4
MAP_CACHE_TTL = 0.9


def _asset_stamp() -> int:
    """Newest modification time (seconds) of the UI files, so a changed file always gets a new ?v= and is never served stale."""
    newest = 0
    for folder in ("static", "templates"):
        for path in (BASE_DIR / folder).rglob("*"):
            try:
                if path.is_file():
                    newest = max(newest, int(path.stat().st_mtime))
            except OSError:
                continue
    return newest


ASSET_VERSION = f"{VERSION}.{_asset_stamp()}"
