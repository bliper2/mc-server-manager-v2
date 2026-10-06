"""Discord webhook notifications. Posting happens on a worker thread so a slow Discord never blocks the panel."""

import queue
import threading
import time
from datetime import datetime, timezone
from urllib.parse import urlparse

import requests

from .config import HEADERS, VERSION
from .store import load_meta, load_settings, save_settings

GREEN, GREY, RED, AMBER, BLUE = 0x4ADE80, 0x9CA3AF, 0xF87171, 0xFBBF24, 0x60A5FA

# event id -> (title, colour, on by default)
EVENTS = {
    "server_start": ("Server started", GREEN, True),
    "server_stop": ("Server stopped", GREY, True),
    "server_crash": ("Server crashed", RED, True),
    "server_restart": ("Server restarted", AMBER, True),
    "backup_done": ("Backup finished", GREEN, True),
    "backup_failed": ("Backup failed", RED, True),
    "player_join": ("Player joined", BLUE, False),
    "player_leave": ("Player left", GREY, False),
    "manager_update": ("Manager updated", BLUE, True),
    "security": ("Security alert", RED, True),
    "staff_change": ("Staff change", AMBER, True),
    "resource_alert": ("High resource use", AMBER, True),
    "server_hung": ("Server not responding", RED, True),
    "disk_low": ("Disk space low", RED, True),
}
EVENT_LABELS = {
    "server_start": "Server starts",
    "server_stop": "Server stops",
    "server_crash": "Server crashes",
    "server_restart": "Automatic restarts",
    "backup_done": "Backup finished",
    "backup_failed": "Backup failed",
    "player_join": "Player joins",
    "player_leave": "Player leaves",
    "manager_update": "Manager updated",
    "security": "Lockouts and failed sign-ins",
    "staff_change": "Staff accounts changed",
    "resource_alert": "Sustained high CPU or memory",
    "server_hung": "Server running but not answering",
    "disk_low": "Low disk space",
}
WEBHOOK_HOSTS = {"discord.com", "discordapp.com", "ptb.discord.com", "canary.discord.com"}
MIN_INTERVAL = 1.2
MAX_QUEUE = 50

_jobs: "queue.Queue[dict]" = queue.Queue(maxsize=MAX_QUEUE)
_worker_lock = threading.Lock()
_worker = None


def valid_webhook(url: str) -> bool:
    try:
        parsed = urlparse(str(url).strip())
    except ValueError:
        return False
    return (parsed.scheme == "https" and (parsed.hostname or "") in WEBHOOK_HOSTS
            and parsed.path.startswith("/api/webhooks/") and len(parsed.path) > len("/api/webhooks/") + 8)


def mask_webhook(url: str) -> str:
    return f"…{url[-6:]}" if url else ""


def event_enabled(settings: dict, event: str) -> bool:
    if event not in EVENTS:
        return False
    return bool(settings["notifications"]["events"].get(event, EVENTS[event][2]))


def public_settings() -> dict:
    """What the API may return: never the webhook itself."""
    cfg = load_settings()["notifications"]
    return {
        "configured": bool(cfg.get("webhook")),
        "hint": mask_webhook(cfg.get("webhook", "")),
        "events": [{"id": key, "label": EVENT_LABELS[key], "enabled": event_enabled(load_settings(), key)} for key in EVENTS]
    }


def update_settings(webhook=None, events=None) -> str | None:
    """Returns an error message, or None. webhook=None keeps the stored URL; "" removes it."""
    settings = load_settings()
    cfg = settings["notifications"]
    if webhook is not None:
        webhook = str(webhook).strip()
        if webhook and not valid_webhook(webhook):
            return "That is not a Discord webhook URL (it should start with https://discord.com/api/webhooks/)"
        cfg["webhook"] = webhook
    if isinstance(events, dict):
        for key, value in events.items():
            if key in EVENTS:
                cfg["events"][key] = bool(value)
    save_settings(settings)
    return None


def _embed(event: str, detail: str, server_id, fields) -> dict:
    title, colour, _ = EVENTS.get(event, (event, BLUE, True))
    embed = {
        "title": title,
        "description": str(detail)[:1800],
        "color": colour,
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "footer": {"text": f"MC Server Manager v{VERSION}"},
    }
    items = list(fields or [])
    if server_id:
        items.insert(0, ("Server", load_meta(server_id).get("name") or server_id))
    if items:
        embed["fields"] = [{"name": str(k)[:60], "value": str(v)[:300] or "-", "inline": True} for k, v in items[:8]]
    return embed


def _post(webhook: str, embed: dict):
    """One delivery attempt, honouring Discord's rate limit once. Returns an error string or None."""
    for _ in range(2):
        try:
            response = requests.post(webhook, json={"username": "MC Manager", "embeds": [embed]}, headers=HEADERS, timeout=10)
        except requests.RequestException as exc:
            return f"Could not reach Discord: {type(exc).__name__}"
        if response.status_code == 429:
            try:
                time.sleep(min(float(response.json().get("retry_after", 1)), 5))
            except (ValueError, TypeError):
                time.sleep(1)
            continue
        if response.status_code in (200, 204):
            return None
        if response.status_code in (401, 404):
            return "Discord says this webhook no longer exists. Create a new one."
        return f"Discord answered HTTP {response.status_code}"
    return "Discord is rate limiting this webhook"


def _run():
    last = 0.0
    while True:
        job = _jobs.get()
        wait = MIN_INTERVAL - (time.time() - last)
        if wait > 0:
            time.sleep(wait)
        _post(job["webhook"], job["embed"])
        last = time.time()


def notify(event: str, detail: str = "", server_id: str | None = None, fields=None):
    """Queue a notification. Silent no-op when no webhook is set or the event is switched off."""
    global _worker
    settings = load_settings()
    webhook = settings["notifications"].get("webhook", "")
    if not webhook or not event_enabled(settings, event):
        return
    with _worker_lock:
        if _worker is None:
            _worker = threading.Thread(target=_run, daemon=True, name="discord-notify")
            _worker.start()
    try:
        _jobs.put_nowait({"webhook": webhook, "embed": _embed(event, detail, server_id, fields)})
    except queue.Full:
        pass


def send_test() -> str | None:
    webhook = load_settings()["notifications"].get("webhook", "")
    if not webhook:
        return "Save a webhook URL first"
    embed = _embed("manager_update", "Test message from the panel. Notifications are working.", None, [("Sent", datetime.now().strftime("%H:%M:%S"))])
    embed["title"] = "Test notification"
    return _post(webhook, embed)
