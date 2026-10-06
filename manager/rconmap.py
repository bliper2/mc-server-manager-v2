"""RCON client, live player map and map markers."""

import json
import math
import re
import secrets
import socket
import struct
import time
import uuid
from pathlib import Path
from flask import jsonify, request

from . import app
from .auth import require_admin
from .config import MAP_CACHE_TTL, RCON_AUTH, RCON_COMMAND, RCON_TIMEOUT
from .state import map_cache
from .worldmap import DIMENSIONS
from .store import get_server_path, is_running, load_meta, save_meta

class RconError(Exception):
    pass

class Rcon:
    """Minimal Source RCON client. Minecraft splits long replies across packets,
    so reads keep draining until the socket goes quiet."""

    def __init__(self, host, port, password):
        self.address = (host, int(port))
        self.password = password
        self.sock = None
        self.request_id = 0

    def __enter__(self):
        self.sock = socket.create_connection(self.address, timeout=RCON_TIMEOUT)
        self.sock.settimeout(RCON_TIMEOUT)
        if self._send(RCON_AUTH, self.password)[0] == -1:
            raise RconError("RCON password rejected")
        return self

    def __exit__(self, *_):
        if self.sock:
            self.sock.close()
            self.sock = None

    def _send(self, kind, body):
        self.request_id += 1
        payload = struct.pack("<ii", self.request_id, kind) + body.encode("utf-8") + b"\x00\x00"
        self.sock.sendall(struct.pack("<i", len(payload)) + payload)
        return self._read()

    def _read(self):
        header = self._recv_exact(12)
        length, response_id, _ = struct.unpack("<iii", header)
        body = self._recv_exact(length - 8)
        return response_id, body[:-2].decode("utf-8", errors="replace")

    def _recv_exact(self, count):
        chunks = b""
        while len(chunks) < count:
            piece = self.sock.recv(count - len(chunks))
            if not piece:
                raise RconError("RCON connection closed")
            chunks += piece
        return chunks

    def command(self, text):
        return self._send(RCON_COMMAND, text)[1]

def rcon_settings(meta: dict) -> dict:
    stored = meta.get("rcon") if isinstance(meta.get("rcon"), dict) else {}
    return {
        "enabled": bool(stored.get("enabled")),
        "port": int(stored.get("port") or 25575),
        "password": stored.get("password") or ""
    }

def rcon_public(meta: dict) -> dict:
    """RCON settings without the password: it never needs to leave the server."""
    settings = rcon_settings(meta)
    return {"enabled": settings["enabled"], "port": settings["port"], "has_password": bool(settings["password"])}

def write_properties(path: Path, updates: dict):
    lines = path.read_text(encoding="utf-8").splitlines() if path.exists() else []
    seen = set()
    out = []
    for line in lines:
        key = line.split("=", 1)[0].strip()
        if key in updates:
            out.append(f"{key}={updates[key]}")
            seen.add(key)
        else:
            out.append(line)
    out.extend(f"{k}={v}" for k, v in updates.items() if k not in seen)
    path.write_text("\n".join(out) + "\n", encoding="utf-8")

POS_PATTERN = re.compile(r"\[([-\d.]+)d?,\s*([-\d.]+)d?,\s*([-\d.]+)d?\]")
ROT_PATTERN = re.compile(r"\[([-\d.]+)f?,\s*([-\d.]+)f?\]")
NUMBER_PATTERN = re.compile(r"([-\d.]+)[fdb]?\s*$")

def parse_entity_pos(reply: str):
    match = POS_PATTERN.search(reply or "")
    if not match:
        return None
    return [round(float(v), 2) for v in match.groups()]

def parse_entity_rotation(reply: str):
    match = ROT_PATTERN.search(reply or "")
    if not match:
        return None
    return round(float(match.group(1)) % 360, 1)

def parse_entity_number(reply: str):
    match = NUMBER_PATTERN.search((reply or "").strip())
    return round(float(match.group(1)), 1) if match else None

SLOW_TTL = 4.0  # health, facing and dimension change slowly; asking for them every second tripled the RCON traffic
_slow = {}  # (server id, player) -> {"at", "health", "dimension", "yaw"}


def parse_player_list(reply: str):
    """(names, max players) from the reply to `list`: "There are 2 of a max of 20 players online: A, B"."""
    reply = (reply or "").strip()
    names = []
    match = re.search(r"players online:\s*(.*)$", reply)
    if match:
        names = [n.strip() for n in match.group(1).split(",") if n.strip()]
    limit = re.search(r"max of (\d+)|/(\d+) players", reply)
    return names, int(next(g for g in limit.groups() if g)) if limit else None


def rcon_player_snapshot(server_id: str) -> dict:
    meta = load_meta(server_id)
    settings = rcon_settings(meta)
    if not settings["enabled"] or not settings["password"]:
        return {"ok": False, "error": "RCON is not configured for this server", "players": []}
    if not is_running(server_id):
        return {"ok": False, "error": "Server is offline", "players": []}
    try:
        now = time.time()
        with Rcon("127.0.0.1", settings["port"], settings["password"]) as rcon:
            names, limit = parse_player_list(rcon.command("list"))
            players = []
            for name in names[:40]:
                position = parse_entity_pos(rcon.command(f"data get entity {name} Pos"))
                if not position:
                    continue
                slow = _slow.get((server_id, name))
                if not slow or now - slow["at"] >= SLOW_TTL:
                    slow = {
                        "at": now,
                        "health": parse_entity_number(rcon.command(f"data get entity {name} Health")),
                        "dimension": (rcon.command(f"data get entity {name} Dimension") or "").split()[-1].strip('"'),
                        "yaw": parse_entity_rotation(rcon.command(f"data get entity {name} Rotation"))
                    }
                    _slow[(server_id, name)] = slow
                players.append({"name": name, "x": position[0], "y": position[1], "z": position[2],
                                "health": slow["health"], "dimension": slow["dimension"], "yaw": slow["yaw"]})
            for key in [k for k in _slow if k[0] == server_id and k[1] not in names]:
                _slow.pop(key, None)  # they left
        return {"ok": True, "players": players, "max": limit}
    except (OSError, RconError, struct.error, ValueError) as exc:
        return {"ok": False, "error": f"RCON: {exc}", "players": []}

def markers_file(server_id: str) -> Path:
    return get_server_path(server_id) / "map_markers.json"

MARKER_LIMIT = 500
KIND_PATTERN = re.compile(r"^[a-z0-9_-]{1,24}$")


def load_markers(server_id: str) -> list:
    path = markers_file(server_id)
    if not path.exists():
        return []
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return []
    markers = [m for m in data if isinstance(m, dict)] if isinstance(data, list) else []
    for marker in markers:
        marker.setdefault("dim", "overworld")  # markers made before dimensions were tracked belong to the overworld
    return markers


def read_marker_fields(data: dict, base: dict = None):
    """(marker fields, error). With `base`, only the fields present in `data` change; without it a name is required."""
    fields = dict(base or {})
    if base is None or "label" in data:
        label = str(data.get("label") or "").strip()[:60]
        if not label:
            return None, "Give the place a name"
        fields["label"] = label
    if "owner" in data or base is None:
        fields["owner"] = str(data.get("owner") or "").strip()[:40]
    if "kind" in data or base is None:
        kind = str(data.get("kind") or "base").strip().lower()
        fields["kind"] = kind if KIND_PATTERN.match(kind) else "other"
    if "dim" in data or base is None:
        dim = str(data.get("dim") or "overworld").replace("minecraft:", "")
        if dim not in DIMENSIONS:
            return None, "Unknown dimension"
        fields["dim"] = dim
    if "note" in data:
        fields["note"] = str(data.get("note") or "").strip()[:200]
    for axis in ("x", "z"):
        if axis in data or base is None:
            try:
                value = float(data.get(axis) or 0)
            except (TypeError, ValueError):
                return None, "Coordinates must be numbers"
            if not math.isfinite(value) or abs(value) > 30_000_000:
                return None, "Coordinates are outside the world"
            fields[axis] = value
    return fields, None

def save_markers(server_id: str, markers: list):
    markers_file(server_id).write_text(json.dumps(markers, indent=2), encoding="utf-8")

@app.route("/api/server/<sid>/rcon", methods=["GET", "POST"])
def api_rcon(sid):
    denied = require_admin()
    if denied:
        return denied
    if not get_server_path(sid).exists():
        return jsonify({"ok": False, "error": "Server not found"}), 404
    meta = load_meta(sid)
    if request.method == "POST":
        data = request.json or {}
        try:
            port = max(1024, min(65535, int(data.get("port") or 25575)))
        except (TypeError, ValueError):
            return jsonify({"ok": False, "error": "RCON port must be a number"}), 400
        enabled = bool(data.get("enabled"))
        password = (data.get("password") or rcon_settings(meta)["password"] or secrets.token_urlsafe(12)).strip()
        meta["rcon"] = {"enabled": enabled, "port": port, "password": password}
        save_meta(sid, meta)
        write_properties(get_server_path(sid) / "server.properties", {
            "enable-rcon": "true" if enabled else "false",
            "rcon.port": port,
            "rcon.password": password,
            "broadcast-rcon-to-ops": "false"
        })
        return jsonify({"ok": True, "settings": rcon_public(meta), "restart_required": is_running(sid)})
    return jsonify({"ok": True, "settings": rcon_public(meta), "running": is_running(sid)})

@app.route("/api/server/<sid>/map")
def api_map(sid):
    if not get_server_path(sid).exists():
        return jsonify({"ok": False, "error": "Server not found"}), 404
    cached = map_cache.get(sid)
    if cached and time.time() - cached["at"] < MAP_CACHE_TTL:
        snapshot = cached["snapshot"]
    else:
        snapshot = rcon_player_snapshot(sid)
        map_cache[sid] = {"at": time.time(), "snapshot": snapshot}
    return jsonify({
        "ok": True,
        "running": is_running(sid),
        "players": snapshot["players"],
        "max": snapshot.get("max"),
        "error": snapshot.get("error"),
        "markers": load_markers(sid)
    })

@app.route("/api/server/<sid>/map/markers", methods=["POST"])
def api_map_markers(sid):
    denied = require_admin()
    if denied:
        return denied
    if not get_server_path(sid).exists():
        return jsonify({"ok": False, "error": "Server not found"}), 404
    data = request.get_json(silent=True) or {}
    markers = load_markers(sid)
    if data.get("remove"):
        markers = [m for m in markers if m.get("id") != data["remove"]]
        save_markers(sid, markers)
        return jsonify({"ok": True, "markers": markers})
    if data.get("edit"):
        target = next((m for m in markers if m.get("id") == data["edit"]), None)
        if target is None:
            return jsonify({"ok": False, "error": "That place no longer exists"}), 404
        fields, error = read_marker_fields(data, target)
        if error:
            return jsonify({"ok": False, "error": error}), 400
        target.update(fields)
        save_markers(sid, markers)
        return jsonify({"ok": True, "markers": markers, "marker": target})
    if len(markers) >= MARKER_LIMIT:
        return jsonify({"ok": False, "error": f"A map holds up to {MARKER_LIMIT} places. Remove some first"}), 400
    fields, error = read_marker_fields(data)
    if error:
        return jsonify({"ok": False, "error": error}), 400
    marker = {"id": uuid.uuid4().hex[:8], **fields}
    markers.append(marker)
    save_markers(sid, markers)
    return jsonify({"ok": True, "markers": markers, "marker": marker})
