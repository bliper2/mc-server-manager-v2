"""Discord status board: one message per server, in a channel of your choice, that always says whether the server is on
or off, with the address people join on and how many are playing. The message is edited in place, never re-posted, so
the channel does not fill up. The webhook URL is kept in manager_settings.json and is never sent back to the browser."""

import logging
import re
import threading
import time
from datetime import datetime, timezone
from urllib.parse import parse_qsl, urlencode, urlparse

import requests
from flask import jsonify, request

from . import app
from .auth import current_account
from .config import HEADERS, VERSION
from .notify import AMBER, GREEN, RED, mask_webhook, valid_webhook
from .procs import ping_minecraft_server
from .state import active_players, playit_logs, started_at
from .store import audit, get_server_path, is_playit_running, is_running, load_meta, load_settings, save_settings

log = logging.getLogger("manager")

POLL_SECONDS = 15
RETRY_AFTER_FAILURE = 60
MIN_GAP = 3  # seconds between two posts for one server, so a restart does not post three messages
INTERVALS = (0, 5, 10, 30, 60)  # minutes between player-count refreshes; 0 = only when the state changes
STATES = {"online": ("🟢 Online", GREEN, "The server is up. Join with the address below."),
          "starting": ("🟡 Starting up", AMBER, "The server is starting. It will say Online here when players can join."),
          "offline": ("🔴 Offline", RED, "The server is not running right now.")}
PLAYIT_ADDRESS = re.compile(r"\b([a-z0-9][a-z0-9.-]*\.(?:joinmc\.link|ply\.gg)(?::\d{2,5})?)\b", re.I)
STATUS_EVENTS = {"server_start", "server_stop", "server_crash", "server_restart", "server_hung"}

_lock = threading.RLock()
_wake = threading.Event()
_pending = set()
_worker = None
_ip_cache = {"ip": None, "at": 0.0}


# ---------------------------------------------------------------- stored settings
def get_config(server_id: str) -> dict:
    return dict(load_settings().get("status", {}).get(server_id) or {})


def set_config(server_id: str, **fields) -> dict:
    with _lock:
        settings = load_settings()
        entry = settings.setdefault("status", {}).setdefault(server_id, {})
        entry.update(fields)
        save_settings(settings)
        return dict(entry)


def drop_config(server_id: str):
    with _lock:
        settings = load_settings()
        if settings.get("status", {}).pop(server_id, None) is not None:
            save_settings(settings)


# ---------------------------------------------------------------- what to say
def public_ip():
    """The host's public IPv4, looked up at most every 15 minutes (a failed lookup is retried after a minute)."""
    now = time.time()
    if now - _ip_cache["at"] < (900 if _ip_cache["ip"] else 60):
        return _ip_cache["ip"]
    ip = None
    try:
        text = requests.get("https://api.ipify.org", headers=HEADERS, timeout=3).text.strip()
        if re.fullmatch(r"\d{1,3}(\.\d{1,3}){3}", text):
            ip = text
    except requests.RequestException:
        pass
    _ip_cache.update(ip=ip, at=now)
    return ip


def resolve_address(server_id: str, cfg: dict):
    """(text, where it came from): what the owner typed, else the Playit tunnel address, else the public IP, else localhost."""
    port = load_meta(server_id).get("port", 25565)
    custom = str(cfg.get("address") or "").strip()
    if custom:
        return custom, "custom"
    if is_playit_running(server_id):
        for line in reversed(playit_logs.get(server_id, [])[-200:]):
            found = PLAYIT_ADDRESS.search(line)
            if found:
                return found.group(1), "playit"
    ip = public_ip()
    if ip:
        return f"{ip}:{port}", "public"
    return f"localhost:{port}", "local"


def max_players(server_id: str) -> str:
    try:
        for line in (get_server_path(server_id) / "server.properties").read_text(encoding="utf-8", errors="replace").splitlines():
            if line.startswith("max-players="):
                return line.split("=", 1)[1].strip() or "?"
    except OSError:
        pass
    return "?"


def current_state(server_id: str) -> str:
    if not is_running(server_id):
        return "offline"
    return "online" if ping_minecraft_server(load_meta(server_id).get("port", 25565)) is not None else "starting"


def build_embed(server_id: str, state: str, since: float, address: str) -> dict:
    meta = load_meta(server_id)
    label, colour, description = STATES[state]
    fields = [{"name": "Status", "value": label, "inline": True}, {"name": "Address", "value": f"`{address}`", "inline": True}]
    if state == "online":
        fields.append({"name": "Players", "value": f"{len(active_players.get(server_id, []))} / {max_players(server_id)}", "inline": True})
    if meta.get("version"):
        fields.append({"name": "Version", "value": f"{str(meta.get('type', '')).title()} {meta['version']}".strip(), "inline": True})
    if state == "online":
        fields.append({"name": "Up since", "value": f"<t:{int(started_at.get(server_id) or since)}:R>", "inline": True})
    else:
        fields.append({"name": "Since", "value": f"<t:{int(since)}:R>", "inline": True})
    return {"title": meta.get("name") or server_id, "description": description, "color": colour, "fields": fields,
            "timestamp": datetime.now(timezone.utc).isoformat(), "footer": {"text": f"MC Server Manager v{VERSION} - last updated"}}


# ---------------------------------------------------------------- talking to Discord
def _urls(webhook: str):
    parsed = urlparse(webhook)
    base = f"{parsed.scheme}://{parsed.netloc}{parsed.path}".rstrip("/")
    return base, dict(parse_qsl(parsed.query))


def _call(method: str, url: str, payload=None):
    """One request, honouring a Discord rate limit once. Returns (response, error text)."""
    for _ in range(2):
        try:
            response = requests.request(method, url, json=payload, headers=HEADERS, timeout=10)
        except requests.RequestException as exc:
            return None, f"Could not reach Discord: {type(exc).__name__}"
        if response.status_code == 429:
            try:
                time.sleep(min(float(response.json().get("retry_after", 1)), 5))
            except (ValueError, TypeError):
                time.sleep(1)
            continue
        return response, None
    return None, "Discord is rate limiting this webhook"


def _explain(response) -> str:
    if response.status_code in (401, 404):
        return "Discord says this webhook no longer exists. Create a new one in the channel settings."
    return f"Discord answered HTTP {response.status_code}"


def post_status(server_id: str, force_new: bool = False, now: float = None):
    """Writes the status message: edits the one it posted before, or posts it the first time. Returns an error text or None."""
    cfg = get_config(server_id)
    webhook = cfg.get("webhook")
    if not webhook:
        return "Save a webhook URL first"
    state = current_state(server_id)
    now = time.time() if now is None else now
    since = now if state != cfg.get("last_state") or not cfg.get("since") else cfg["since"]
    address, _ = resolve_address(server_id, cfg)
    payload = {"embeds": [build_embed(server_id, state, since, address)]}
    base, query = _urls(webhook)
    message_id = None if force_new else (cfg.get("message_id") or None)
    problem = None
    if message_id:
        response, problem = _call("PATCH", f"{base}/messages/{message_id}" + (f"?{urlencode(query)}" if query else ""), payload)
        if problem is None:
            if response.status_code == 200:
                set_config(server_id, last_state=state, since=since, updated=now, error="", last_try=now)
                return None
            if response.status_code == 404:
                message_id = None  # somebody deleted the message: post a new one
            else:
                problem = _explain(response)
    if message_id is None and problem is None:
        response, problem = _call("POST", f"{base}?{urlencode({**query, 'wait': 'true'})}", {"username": "MC Manager", **payload})
        if problem is None:
            if response.status_code in (200, 204):
                try:
                    new_id = str(response.json().get("id") or "")
                except ValueError:
                    new_id = ""
                set_config(server_id, message_id=new_id, last_state=state, since=since, updated=now, error="", last_try=now)
                return None
            problem = _explain(response)
    set_config(server_id, error=problem, last_try=now)
    return problem


def delete_message(cfg: dict):
    """Best effort: tidy the channel when a status board is removed."""
    if cfg.get("webhook") and cfg.get("message_id"):
        base, query = _urls(cfg["webhook"])
        _call("DELETE", f"{base}/messages/{cfg['message_id']}" + (f"?{urlencode(query)}" if query else ""))


# ---------------------------------------------------------------- the background loop
def tick(now: float = None):
    """One pass over every configured server: post when the state changed, or when the refresh interval is due."""
    now = time.time() if now is None else now
    with _lock:
        _pending.clear()  # the events that woke the loop only mean "look now"; the state itself decides what to post
    for server_id, cfg in list(load_settings().get("status", {}).items()):
        if not cfg.get("webhook") or not get_server_path(server_id).is_dir():
            continue
        since_try = now - cfg.get("last_try", 0)
        if since_try < MIN_GAP or (cfg.get("error") and since_try < RETRY_AFTER_FAILURE):
            continue
        state = current_state(server_id)
        interval = int(cfg.get("interval", 5))
        due = interval > 0 and state == "online" and now - cfg.get("updated", 0) >= interval * 60
        if state != cfg.get("last_state") or due:
            post_status(server_id, now=now)


def _loop():
    while True:
        _wake.wait(timeout=POLL_SECONDS)
        _wake.clear()
        try:
            tick()
        except Exception:  # noqa: BLE001 - the loop must survive anything a webhook or a server throws at it
            log.exception("Status board pass failed")


def ensure_worker():
    global _worker
    with _lock:
        if _worker is None or not _worker.is_alive():
            _worker = threading.Thread(target=_loop, daemon=True, name="status-board")
            _worker.start()


def refresh_soon(server_id: str):
    """Called when a server starts, stops or crashes, so the channel hears about it within a moment."""
    if get_config(server_id).get("webhook"):
        with _lock:
            _pending.add(server_id)
        ensure_worker()
        _wake.set()


def flush():
    """Posts any pending changes now, on this thread. Used while the manager shuts down, before the worker could."""
    try:
        tick()
    except Exception:  # noqa: BLE001
        log.exception("Status board flush failed")


# ---------------------------------------------------------------- routes
def public_view(server_id: str) -> dict:
    cfg = get_config(server_id)
    detected, source = resolve_address(server_id, cfg)
    updated = datetime.fromtimestamp(cfg["updated"]).isoformat(timespec="seconds") if cfg.get("updated") else None
    return {"configured": bool(cfg.get("webhook")), "hint": mask_webhook(cfg.get("webhook", "")), "address": cfg.get("address", ""), "interval": int(cfg.get("interval", 5)),
            "detected": detected, "source": source, "state": cfg.get("last_state"), "updated": updated, "error": cfg.get("error", "")}


def _check(server_id: str):
    if not get_server_path(server_id).is_dir():
        return jsonify({"ok": False, "error": "Server not found"}), 404
    return None


@app.route("/api/server/<sid>/status-hook", methods=["GET", "POST"])
def api_status_hook(sid):
    missing = _check(sid)
    if missing:
        return missing
    if request.method == "GET":
        return jsonify({"ok": True, **public_view(sid)})
    data = request.get_json(silent=True) or {}
    fields = {}
    webhook = data.get("webhook")
    if webhook is not None:
        webhook = str(webhook).strip()
        if webhook and not valid_webhook(webhook):
            return jsonify({"ok": False, "error": "That is not a Discord webhook URL (it should start with https://discord.com/api/webhooks/)"}), 400
    if data.get("address") is not None:
        address = re.sub(r"[`\r\n\t]", "", str(data["address"])).strip()
        if len(address) > 100:
            return jsonify({"ok": False, "error": "The address is too long"}), 400
        fields["address"] = address
    if data.get("interval") is not None:
        try:
            interval = int(data["interval"])
        except (TypeError, ValueError):
            interval = -1
        if interval not in INTERVALS:
            return jsonify({"ok": False, "error": "Choose one of the refresh intervals offered"}), 400
        fields["interval"] = interval
    user = (current_account() or {}).get("username", "?")
    if webhook == "":
        delete_message(get_config(sid))
        drop_config(sid)
        audit(user, "status board removed", server=sid)
        return jsonify({"ok": True, **public_view(sid)})
    if webhook:
        if webhook != get_config(sid).get("webhook"):
            fields.update(webhook=webhook, message_id="", last_state=None, since=0, error="", last_try=0)
            audit(user, "status board webhook set", server=sid)
    if fields:
        set_config(sid, **fields)
    if get_config(sid).get("webhook"):
        refresh_soon(sid)
    return jsonify({"ok": True, **public_view(sid)})


@app.route("/api/server/<sid>/status-hook/send", methods=["POST"])
def api_status_hook_send(sid):
    missing = _check(sid)
    if missing:
        return missing
    error = post_status(sid, force_new=bool((request.get_json(silent=True) or {}).get("new")))
    ensure_worker()
    return jsonify({"ok": error is None, "error": error or "", **public_view(sid)}), 200 if error is None else 502


if load_settings().get("status"):
    ensure_worker()
