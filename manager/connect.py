"""Helping people join: which addresses a server can be reached on from this PC, who can use each one, whether anything is
listening on the port, whether Windows Firewall has a rule for it, and the settings that leave it open to anyone."""

import ipaddress
import os
import subprocess
import time

import psutil
import requests
from flask import jsonify

from . import app
from .store import get_server_path, is_running, load_meta
from .util import SERVER_ID_PATTERN

VIRTUAL_NAMES = ("vmware", "virtualbox", "vethernet", "hyper-v", "wsl", "docker", "vbox", "loopback", "bluetooth")
VPN_NAMES = (("radmin", "Radmin VPN"), ("hamachi", "Hamachi"), ("zerotier", "ZeroTier"), ("tailscale", "Tailscale"),
             ("nordlynx", "NordVPN"), ("nordvpn", "NordVPN"), ("openvpn", "OpenVPN"), ("wireguard", "WireGuard"), ("proton", "Proton VPN"))
VPN_RANGES = ((ipaddress.ip_network("26.0.0.0/8"), "Radmin VPN"), (ipaddress.ip_network("25.0.0.0/8"), "Hamachi"),
              (ipaddress.ip_network("100.64.0.0/10"), "Tailscale"))
FIREWALL_TTL = 60
_firewall_cache = {}


def classify(name: str, address: str):
    """('lan' | 'vpn' | 'virtual', label) for a network adapter."""
    lowered = name.lower()
    if any(word in lowered for word in VIRTUAL_NAMES):
        return "virtual", name
    for word, label in VPN_NAMES:
        if word in lowered:
            return "vpn", label
    ip = ipaddress.ip_address(address)
    for network, label in VPN_RANGES:
        if ip in network:
            return "vpn", label
    return "lan", name


def addresses() -> list:
    """Every usable IPv4 address of this PC, home networks first. Loopback and link-local addresses are left out."""
    stats = psutil.net_if_stats()
    found = []
    for name, entries in psutil.net_if_addrs().items():
        if name in stats and not stats[name].isup:
            continue
        for entry in entries:
            if entry.family.name != "AF_INET":
                continue
            ip = ipaddress.ip_address(entry.address)
            if ip.is_loopback or ip.is_link_local:
                continue
            kind, label = classify(name, entry.address)
            found.append({"address": entry.address, "adapter": name, "kind": kind, "label": label, "private": ip.is_private})
    order = {"lan": 0, "vpn": 1, "virtual": 2}
    return sorted(found, key=lambda item: (order[item["kind"]], item["address"]))


def listening(port: int) -> bool:
    """True when some process accepts TCP connections on `port`. If the OS hides the table, a connection attempt decides."""
    try:
        return any(conn.status == psutil.CONN_LISTEN and conn.laddr and conn.laddr.port == port for conn in psutil.net_connections(kind="tcp"))
    except (psutil.AccessDenied, OSError):
        import socket
        try:
            with socket.create_connection(("127.0.0.1", port), timeout=1):
                return True
        except OSError:
            return False


def firewall_rule(port: int):
    """True or False for 'an enabled inbound Allow rule for this TCP port exists' on Windows, None where it cannot be told."""
    if os.name != "nt":
        return None
    cached = _firewall_cache.get(port)
    if cached and time.time() - cached[0] < FIREWALL_TTL:
        return cached[1]
    script = (f"$f = Get-NetFirewallPortFilter -Protocol TCP | Where-Object {{ $_.LocalPort -contains '{int(port)}' }};"
              "if ($f) { ($f | Get-NetFirewallRule | Where-Object { $_.Enabled -eq 'True' -and $_.Direction -eq 'Inbound' -and $_.Action -eq 'Allow' } | Measure-Object).Count } else { 0 }")
    try:
        result = subprocess.run(["powershell", "-NoProfile", "-NonInteractive", "-Command", script], capture_output=True, text=True, timeout=15)
        answer = int(result.stdout.strip().splitlines()[-1]) > 0
    except (OSError, subprocess.SubprocessError, ValueError, IndexError):
        return None
    _firewall_cache[port] = (time.time(), answer)
    return answer


def read_properties(server_id: str) -> dict:
    values = {}
    try:
        for line in (get_server_path(server_id) / "server.properties").read_text(encoding="utf-8", errors="replace").splitlines():
            if "=" in line and not line.lstrip().startswith("#"):
                key, _, value = line.partition("=")
                values[key.strip()] = value.strip()
    except OSError:
        pass
    return values


def warnings(properties: dict) -> list:
    out = []
    if properties.get("online-mode", "true").lower() == "false" and properties.get("white-list", "false").lower() != "true":
        out.append("Online mode and the whitelist are both off, so anyone who finds the address can join with any name. Turn the whitelist on before sharing it outside your home.")
    elif properties.get("online-mode", "true").lower() == "false":
        out.append("Online mode is off: players are not checked against Minecraft accounts, so the whitelist only matches names. Add people carefully.")
    return out


def public_address():
    """This connection's public IP as seen from the internet, or None. A single outbound request, only made when the person asks."""
    try:
        reply = requests.get("https://api.ipify.org", timeout=5)
        reply.raise_for_status()
        ipaddress.ip_address(reply.text.strip())
        return reply.text.strip()
    except (requests.RequestException, ValueError):
        return None


@app.route("/api/server/<sid>/connect")
def api_connect(sid):
    if not SERVER_ID_PATTERN.fullmatch(sid) or not get_server_path(sid).is_dir():
        return jsonify({"ok": False, "error": "Server not found"}), 404
    properties = read_properties(sid)
    try:
        port = int(properties.get("server-port") or load_meta(sid).get("port") or 25565)
    except ValueError:
        port = 25565
    running = is_running(sid)
    return jsonify({"ok": True, "port": port, "running": running, "listening": listening(port) if running else False, "firewall": firewall_rule(port),
                    "addresses": addresses(), "warnings": warnings(properties),
                    "firewall_command": f'New-NetFirewallRule -DisplayName "Minecraft {port}" -Direction Inbound -Protocol TCP -LocalPort {port} -Action Allow'})


@app.route("/api/server/<sid>/connect/public")
def api_connect_public(sid):
    if not SERVER_ID_PATTERN.fullmatch(sid) or not get_server_path(sid).is_dir():
        return jsonify({"ok": False, "error": "Server not found"}), 404
    address = public_address()
    return jsonify({"ok": bool(address), "address": address or "", "error": "" if address else "Could not look up the public address (offline?)"})
