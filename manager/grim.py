"""GrimAC support for the Anti-Cheat tab: finding the plugin, changing its settings without disturbing its files, a feed of
its alerts read from the server console, a summary of its punishment rules, and an optional relay of serious alerts to Discord."""

import re
import shutil
import time
from collections import Counter, deque
from datetime import datetime
from pathlib import Path

from flask import jsonify, request

from . import app, yamlite
from .auth import current_account
from .compat import PLUGIN_LOADERS, jar_facts
from .jarinfo import plugin_info
from .notify import mask_webhook, notify, valid_webhook
from .state import grim_alerts
from .store import audit, get_server_path, is_running, load_meta

GRIM_FOLDER = "GrimAC"
FILES = ("config.yml", "messages.yml", "punishments.yml", "discord.yml", "database.yml")
RING_SIZE = 500
RELAY_COOLDOWN = 300  # seconds before the same player and check can reach Discord again
RELAY_PER_MINUTE = 12
OTHER_ANTICHEATS = ("matrix", "nocheatplus", "spartan", "vulcan", "themis", "intave", "godseye", "lightanticheat")
ANSI = re.compile(r"\x1b\[[0-9;]*[A-Za-z]|§.")
ALERT = re.compile(r"(?:^|[\]\s:»>])(?P<player>[A-Za-z0-9_.]{2,16}) failed (?P<check>[A-Za-z0-9_]+(?: [A-Za-z0-9_]+)*?)(?P<exp>\*)? \(x(?P<vl>\d+)\)\s*(?P<info>.*)$")
LOG_TIME = re.compile(r"^\[(\d\d):(\d\d):(\d\d)")

# Settings the panel can change, each tied to one value in one of Grim's files. A setting that is not in the installed
# version's file is left out of the form instead of being invented.
SETTINGS = [
    {"id": "alerts_console", "file": "config.yml", "path": ["alerts", "print-to-console"], "type": "bool", "group": "Alerts", "label": "Print alerts to the console",
     "help": "Alerts also appear in the server console. The feed below reads them from there, so keep this on."},
    {"id": "alerts_proxy_send", "file": "config.yml", "path": ["alerts", "proxy", "send"], "type": "bool", "group": "Alerts", "label": "Share alerts with other servers on your proxy",
     "help": "Only for BungeeCord or Velocity networks."},
    {"id": "alerts_proxy_receive", "file": "config.yml", "path": ["alerts", "proxy", "receive"], "type": "bool", "group": "Alerts", "label": "Show alerts from other servers on your proxy",
     "help": "Only for BungeeCord or Velocity networks."},
    {"id": "verbose_console", "file": "config.yml", "path": ["verbose", "print-to-console"], "type": "bool", "group": "Alerts", "label": "Print verbose flags to the console",
     "help": "Every single flag, with no buffering. Noisy: for diagnosing false positives."},
    {"id": "update_check", "file": "config.yml", "path": ["check-for-updates"], "type": "bool", "group": "Grim", "label": "Tell operators when a new Grim version is out", "help": ""},
    {"id": "spectators_hide", "file": "config.yml", "path": ["spectators", "hide-regardless"], "type": "bool", "group": "Grim", "label": "Hide everyone with the spectator permission",
     "help": "Even when they are not actually spectating."},
    {"id": "forge_blacklist", "file": "config.yml", "path": ["client-brand", "disconnect-blacklisted-forge-versions"], "type": "bool", "group": "Grim",
     "label": "Disconnect Forge versions with built-in reach hacks", "help": "Forge 1.18.2 to 1.19.3. Turning this off is at your own risk."},
    {"id": "transaction_timeout", "file": "config.yml", "path": ["max-transaction-time"], "type": "int", "min": 5, "max": 600, "group": "Grim",
     "label": "Kick players who stop answering for (seconds)", "help": "Grim's timeout for a player that stops responding. Default 60."},
    {"id": "discord_enabled", "file": "discord.yml", "path": ["enabled"], "type": "bool", "group": "Grim's own Discord webhook", "label": "Send alerts to Discord from Grim itself",
     "help": "Separate from the manager's relay below. Which flags are sent is set in punishments.yml with [webhook]."},
    {"id": "discord_webhook", "file": "discord.yml", "path": ["webhook"], "type": "secret", "group": "Grim's own Discord webhook", "label": "Discord webhook URL",
     "help": "Never shown again after saving."},
    {"id": "discord_title", "file": "discord.yml", "path": ["embed-title"], "type": "string", "group": "Grim's own Discord webhook", "label": "Message title", "help": ""},
    {"id": "history_enabled", "file": "database.yml", "path": ["database", "enabled"], "type": "bool", "group": "History", "label": "Keep each player's violation history",
     "help": "Needed for /grim history. Checks keep running when this is off."},
]
COMMANDS = [
    {"command": "grim reload", "label": "Reload Grim's configuration", "player": False, "game_only": False},
    {"command": "grim version", "label": "Show the installed Grim version", "player": False, "game_only": False},
    {"command": "grim perf", "label": "Milliseconds per prediction (performance)", "player": False, "game_only": False},
    {"command": "grim history {player}", "label": "Violation history of a player", "player": True, "game_only": False},
    {"command": "grim profile {player}", "label": "A player's ping, version, client and sensitivity", "player": True, "game_only": False},
    {"command": "grim debug {player}", "label": "Developer prediction output for a player", "player": True, "game_only": False},
    {"command": "grim sendalert {message}", "label": "Send a message to everyone who receives alerts", "player": False, "message": True, "game_only": False},
    {"command": "grim alerts", "label": "Toggle alerts for yourself", "player": False, "game_only": True},
    {"command": "grim verbose", "label": "Show every flag to yourself", "player": False, "game_only": True},
    {"command": "grim brands", "label": "Toggle client brand messages for yourself", "player": False, "game_only": True},
    {"command": "grim spectate {player}", "label": "Spectate a player", "player": True, "game_only": True},
    {"command": "grim log", "label": "Upload a debug log of prediction flags", "player": False, "game_only": True},
]
_last_relay = {}
_recent_relays = deque(maxlen=64)


def grim_dir(server_id: str) -> Path:
    return get_server_path(server_id) / "plugins" / GRIM_FOLDER


def plugin_files(server_id: str):
    folder = get_server_path(server_id) / "plugins"
    return sorted(p for p in folder.iterdir() if p.is_file() and p.name.lower().endswith((".jar", ".jar.disabled"))) if folder.is_dir() else []


def find_grim(server_id: str) -> dict:
    """What the plugins folder says about Grim: the jar that would load, a disabled one, builds for other loaders, other anti-cheats."""
    meta = load_meta(server_id)
    server_type = str(meta.get("type") or "").lower()
    status = {"supported": server_type in PLUGIN_LOADERS, "installed": False, "enabled": False, "file": "", "version": "", "wrong_builds": [], "others": [],
              "folder": grim_dir(server_id).is_dir(), "files": {name: (grim_dir(server_id) / name).is_file() for name in FILES}, "running": is_running(server_id), "type": server_type}
    candidates = []
    for jar in plugin_files(server_id):
        kinds, plugin_name, _, _ = jar_facts(jar)
        lowered = (plugin_name or jar.name).lower()
        if plugin_name.lower() == "grimac" and "plugin" in kinds:
            candidates.append(jar)
        elif "grim" in jar.name.lower() and not kinds & {"plugin"} and kinds:
            status["wrong_builds"].append(jar.name)
        elif any(word in lowered for word in OTHER_ANTICHEATS) and jar.name.lower().endswith(".jar"):
            status["others"].append(plugin_name or jar.name)
    best = next((j for j in candidates if j.name.lower().endswith(".jar")), candidates[0] if candidates else None)
    if best:
        status.update(installed=True, enabled=best.name.lower().endswith(".jar"), file=best.name, version=plugin_info(best)["version"])
    return status


# ---------------------------------------------------------------- settings
def _read(server_id: str, name: str):
    path = grim_dir(server_id) / name
    try:
        return path.read_text(encoding="utf-8") if path.is_file() and path.stat().st_size < 1024 * 1024 else None
    except (OSError, UnicodeDecodeError):
        return None


def read_settings(server_id: str) -> list:
    texts, result = {}, []
    for definition in SETTINGS:
        text = texts.setdefault(definition["file"], _read(server_id, definition["file"]))
        if text is None:
            continue
        try:
            value = yamlite.get_value(text, definition["path"])
        except (KeyError, ValueError):
            continue
        item = {key: definition[key] for key in ("id", "type", "group", "label", "help") if key in definition}
        item.update({k: definition[k] for k in ("min", "max") if k in definition})
        if definition["type"] == "secret":
            item.update(value="", set=bool(value), hint=mask_webhook(str(value or "")))
        else:
            item["value"] = value
        result.append(item)
    return result


def _coerce(definition: dict, raw):
    kind = definition["type"]
    if kind == "bool":
        if isinstance(raw, bool):
            return raw
        if str(raw).lower() in ("true", "false"):
            return str(raw).lower() == "true"
        raise ValueError(f"{definition['label']} must be on or off")
    if kind == "int":
        try:
            number = int(raw)
        except (TypeError, ValueError):
            raise ValueError(f"{definition['label']} must be a number") from None
        if not definition["min"] <= number <= definition["max"]:
            raise ValueError(f"{definition['label']} must be between {definition['min']} and {definition['max']}")
        return number
    text = str(raw if raw is not None else "").strip()
    if "\n" in text or "\r" in text or len(text) > 300:
        raise ValueError(f"{definition['label']} must be one short line")
    if kind == "secret" and text and not valid_webhook(text):
        raise ValueError("That is not a Discord webhook URL (it should start with https://discord.com/api/webhooks/)")
    return text


def apply_settings(server_id: str, values: dict):
    """Writes the given settings into Grim's files, keeping every comment and the layout. Returns (changed ids, error text)."""
    definitions = {d["id"]: d for d in SETTINGS}
    pending = {}
    for key, raw in values.items():
        definition = definitions.get(key)
        if definition is None:
            return [], f"Unknown setting: {key}"
        if definition["type"] == "secret" and raw is None:
            continue  # the form leaves a saved secret alone unless a new one is typed
        try:
            pending.setdefault(definition["file"], []).append((definition, _coerce(definition, raw)))
        except ValueError as exc:
            return [], str(exc)
    texts = {}
    for name, items in pending.items():
        text = _read(server_id, name)
        if text is None:
            return [], f"{name} does not exist yet. Start the server once so Grim creates it"
        for definition, value in items:
            try:
                text = yamlite.set_value(text, definition["path"], value)
            except (KeyError, ValueError):
                return [], f"{definition['label']} is not in this Grim version's {name}"
        texts[name] = text
    changed = []
    for name, text in texts.items():
        path = grim_dir(server_id) / name
        try:
            shutil.copyfile(path, path.with_name(path.name + ".manager-backup"))
            path.write_text(text, encoding="utf-8")
        except OSError as exc:
            return changed, f"Could not write {name} (is the file open elsewhere?): {exc}"
        changed.extend(definition["id"] for definition, _ in pending[name])
    return changed, None


# ---------------------------------------------------------------- punishments
def parse_punishments(text: str) -> list:
    """The groups in punishments.yml: [{name, remove_after, checks, rules: [{threshold, interval, action}]}]."""
    groups, group, section = [], None, None
    in_block, group_indent = False, None
    for raw in text.splitlines():
        line = raw.rstrip()
        stripped = line.strip()
        if not stripped or stripped.startswith("#"):
            continue
        indent = len(line) - len(line.lstrip())
        if re.match(r"^Punishments\s*:", line):
            in_block = True
            continue
        if not in_block:
            continue
        if indent == 0:
            in_block = False
            continue
        if group_indent is None:
            group_indent = indent
        if indent == group_indent and not stripped.startswith("-"):
            match = re.match(r"^([A-Za-z0-9_\-]+)\s*:\s*$", stripped)
            if match:
                group = {"name": match.group(1), "remove_after": None, "checks": [], "rules": []}
                groups.append(group)
                section = None
            continue
        if group is None:
            continue
        if stripped.startswith("- "):
            item = yamlite.unquote(yamlite.split_comment(stripped[2:])[0])
            if section == "checks":
                group["checks"].append(item)
            elif section == "commands":
                rule = re.match(r"^(\d+):(\d+)\s+(.*)$", item)
                if rule:
                    group["rules"].append({"threshold": int(rule.group(1)), "interval": int(rule.group(2)), "action": rule.group(3)})
            continue
        key = re.match(r"^([A-Za-z0-9_\-]+)\s*:\s*(.*)$", stripped)
        if key:
            name, value = key.group(1), yamlite.split_comment(key.group(2))[0].strip()
            if name == "remove-violations-after" and value.isdigit():
                group["remove_after"] = int(value)
            section = name if name in ("checks", "commands") else None
    return groups


# ---------------------------------------------------------------- alerts
def parse_alert(line: str):
    match = ALERT.search(ANSI.sub("", line).strip())
    if not match:
        return None
    return {"player": match.group("player"), "check": match.group("check"), "vl": int(match.group("vl")), "info": match.group("info").strip()[:160], "exp": bool(match.group("exp"))}


def relay_config(server_id: str) -> dict:
    import json
    config = {"discord_relay": False, "threshold": 5}
    try:
        stored = json.loads((get_server_path(server_id) / "anti_cheat.json").read_text(encoding="utf-8"))
        if isinstance(stored, dict):
            config["discord_relay"] = bool(stored.get("discord_relay"))
            config["threshold"] = max(1, min(1000, int(stored.get("threshold") or 5)))
    except (OSError, ValueError):
        pass
    return config


def watch_line(server_id: str, text: str):
    """Called for every console line of every server: notes Grim alerts and relays serious ones to Discord."""
    if " failed " not in text or "(x" not in text:
        return
    alert = parse_alert(text)
    if alert is None:
        return
    alert["at"] = time.time()
    grim_alerts.setdefault(server_id, deque(maxlen=RING_SIZE)).append(alert)
    config = relay_config(server_id)
    if not config["discord_relay"] or alert["vl"] < config["threshold"]:
        return
    now = time.time()
    key = (server_id, alert["player"], alert["check"])
    while _recent_relays and now - _recent_relays[0] > 60:
        _recent_relays.popleft()
    if now - _last_relay.get(key, 0) < RELAY_COOLDOWN or len(_recent_relays) >= RELAY_PER_MINUTE:
        return
    _last_relay[key] = now
    _recent_relays.append(now)
    notify("anticheat_alert", f"**{alert['player']}** failed **{alert['check']}** (x{alert['vl']})", server_id,
           [("Player", alert["player"]), ("Check", alert["check"]), ("Violations", alert["vl"]), ("Detail", alert["info"] or "-")])


def seed_from_log(server_id: str):
    """After a restart of the manager the in-memory feed is empty: refill it from the end of the server's latest.log."""
    log = get_server_path(server_id) / "logs" / "latest.log"
    try:
        size = log.stat().st_size
        with log.open("rb") as handle:
            handle.seek(max(0, size - 600_000))
            lines = handle.read().decode("utf-8", "replace").splitlines()
        day = datetime.fromtimestamp(log.stat().st_mtime).replace(hour=0, minute=0, second=0, microsecond=0)
    except OSError:
        return
    ring = grim_alerts.setdefault(server_id, deque(maxlen=RING_SIZE))
    for line in lines:
        if " failed " not in line:
            continue
        alert = parse_alert(line)
        stamp = LOG_TIME.match(line)
        if alert and stamp:
            alert["at"] = day.replace(hour=int(stamp.group(1)), minute=int(stamp.group(2)), second=int(stamp.group(3))).timestamp()
            ring.append(alert)


def recent_alerts(server_id: str, limit: int = 100, player: str = "") -> dict:
    if not grim_alerts.get(server_id):
        seed_from_log(server_id)
    alerts = list(grim_alerts.get(server_id, []))
    if player:
        alerts = [a for a in alerts if a["player"].lower() == player.lower()]
    alerts = alerts[-limit:][::-1]
    everything = list(grim_alerts.get(server_id, []))
    by_player = Counter(a["player"] for a in everything)
    by_check = Counter(a["check"] for a in everything)
    worst = {}
    for a in everything:
        worst[a["player"]] = max(worst.get(a["player"], 0), a["vl"])
    return {
        "alerts": [{**a, "time": datetime.fromtimestamp(a["at"]).isoformat(timespec="seconds")} for a in alerts],
        "players": [{"name": name, "alerts": count, "worst": worst[name]} for name, count in by_player.most_common(8)],
        "checks": [{"name": name, "alerts": count} for name, count in by_check.most_common(8)],
        "total": len(everything),
    }


# ---------------------------------------------------------------- routes
@app.route("/api/server/<sid>/grim")
def api_grim(sid):
    if not get_server_path(sid).is_dir():
        return jsonify({"ok": False, "error": "Server not found"}), 404
    status = find_grim(sid)
    punishments = _read(sid, "punishments.yml")
    return jsonify({"ok": True, "status": status, "settings": read_settings(sid), "punishments": parse_punishments(punishments) if punishments else [],
                    "relay": relay_config(sid), "commands": COMMANDS, "files": list(FILES)})


@app.route("/api/server/<sid>/grim/settings", methods=["POST"])
def api_grim_settings(sid):
    if not get_server_path(sid).is_dir():
        return jsonify({"ok": False, "error": "Server not found"}), 404
    values = (request.get_json(silent=True) or {}).get("values")
    if not isinstance(values, dict) or not values:
        return jsonify({"ok": False, "error": "Nothing to save"}), 400
    changed, error = apply_settings(sid, values)
    if error:
        return jsonify({"ok": False, "error": error, "changed": changed}), 400
    audit((current_account() or {}).get("username", "?"), "grim settings changed", server=sid, settings=",".join(changed))
    return jsonify({"ok": True, "changed": changed, "settings": read_settings(sid)})


@app.route("/api/server/<sid>/grim/alerts")
def api_grim_alerts(sid):
    if not get_server_path(sid).is_dir():
        return jsonify({"ok": False, "error": "Server not found"}), 404
    try:
        limit = max(1, min(300, int(request.args.get("limit", 100))))
    except ValueError:
        limit = 100
    return jsonify({"ok": True, **recent_alerts(sid, limit, request.args.get("player", "").strip())})
