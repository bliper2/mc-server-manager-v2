"""Starting, stopping and talking to Minecraft and Playit processes."""

import re
import socket
import struct
import subprocess
import threading
import time
from datetime import datetime

from .javatools import choose_java, required_java_major
from .notify import notify
from .state import (active_players, console_dropped, console_logs, exit_hooks, playit_logs, playit_processes,
                    running_servers, started_at)
from .store import get_server_path, is_playit_running, is_running, load_meta

def encode_varint(value):
    output = bytearray()
    value &= 0xFFFFFFFF
    while True:
        byte = value & 0x7F
        value >>= 7
        output.append(byte | (0x80 if value else 0))
        if not value:
            return bytes(output)

def read_varint(stream):
    value = 0
    shift = 0
    while shift < 35:
        byte = stream.recv(1)
        if not byte:
            raise ConnectionError("Minecraft server closed the connection")
        current = byte[0]
        value |= (current & 0x7F) << shift
        if not current & 0x80:
            return value
        shift += 7
    raise ValueError("Invalid Minecraft packet length")

def send_packet(stream, payload):
    stream.sendall(encode_varint(len(payload)) + payload)

def ping_minecraft_server(port):
    started = time.perf_counter()
    try:
        with socket.create_connection(("127.0.0.1", int(port)), timeout=2) as stream:
            address = b"127.0.0.1"
            handshake = b"\x00" + encode_varint(760) + encode_varint(len(address)) + address + struct.pack(">H", int(port)) + b"\x01"
            send_packet(stream, handshake)
            send_packet(stream, b"\x00")
            packet_length = read_varint(stream)
            packet = stream.recv(packet_length)
            if not packet:
                raise ConnectionError("Empty status response")
            return round((time.perf_counter() - started) * 1000)
    except (OSError, ValueError, ConnectionError, struct.error):
        return None

def read_console(server_id, process):
    console_logs.setdefault(server_id, [])
    try:
        for line in iter(process.stdout.readline, b""):
            text = line.decode("utf-8", errors="replace").rstrip()
            lines = console_logs[server_id]
            lines.append(text)
            update_active_players(server_id, text)
            if len(lines) > 3000:
                # Count what was trimmed so the client's absolute offset stays valid.
                console_dropped[server_id] = console_dropped.get(server_id, 0) + 1000
                console_logs[server_id] = lines[1000:]
    except Exception:
        pass
    finally:
        # A restart registers a new process before this thread unwinds; only clear our own entry.
        if running_servers.get(server_id) is process:
            running_servers.pop(server_id, None)
        finish_process(server_id, process)

def finish_process(server_id, process):
    """Runs once per Minecraft process after its output closes and tells the exit hooks how it ended."""
    try:
        code = process.wait(timeout=15)
    except subprocess.TimeoutExpired:
        code = None
    deliberate = getattr(process, "mcm_deliberate", False)
    if running_servers.get(server_id) is None:
        started_at.pop(server_id, None)
    crashed = not deliberate and code not in (0, None)
    for hook in list(exit_hooks):
        try:
            hook(server_id, code, crashed, deliberate)
        except Exception as exc:
            print("exit hook failed:", exc)

def update_active_players(server_id, text):
    players = active_players.setdefault(server_id, [])
    joined = re.search(r":\s+([^:]+) joined the game\s*$", text)
    left = re.search(r":\s+([^:]+) left the game\s*$", text)
    listed = re.search(r"There are \d+ of a max of \d+ players online:\s*(.*)$", text)
    if listed:
        names = [name.strip() for name in listed.group(1).split(",") if name.strip()]
        active_players[server_id] = names
    elif joined:
        name = joined.group(1).strip()
        if name not in players:
            players.append(name)
            notify("player_join", f"**{name}** joined ({len(players)} online)", server_id)
    elif left:
        name = left.group(1).strip()
        if name in players:
            notify("player_leave", f"**{name}** left", server_id)
        active_players[server_id] = [player for player in players if player != name]

def start_server(server_id, ram_mb=2048, automatic=False):
    if is_running(server_id):
        return False, "Already running"
    path = get_server_path(server_id)
    meta = load_meta(server_id)
    jar_name = meta.get("jar", "server.jar")
    jar_path = path / jar_name
    if not jar_path.exists():
        return False, f"JAR missing: {jar_name}"
    required = required_java_major(meta.get("version"))
    java, major = choose_java(required)
    if not java:
        return False, "Java not found. Open Settings > Java and click Install Java, or install Java 21 from adoptium.net and restart the manager."
    if required and major and major < required:
        return False, (f"Minecraft {meta.get('version')} needs Java {required} or newer, but this PC only has Java {major}. "
                       f"Open Settings > Java and install Java {required}.")
    (path / "eula.txt").write_text("eula=true\n", encoding="utf-8")
    cmd = [java, f"-Xms{max(512, ram_mb // 2)}M", f"-Xmx{ram_mb}M", "-jar", str(jar_path), "nogui"]
    try:
        process = subprocess.Popen(cmd, cwd=str(path), stdout=subprocess.PIPE, stderr=subprocess.STDOUT, stdin=subprocess.PIPE)
    except FileNotFoundError:
        return False, "Java could not be launched. Reinstall Java and restart the manager."
    except Exception as e:
        return False, str(e)
    running_servers[server_id] = process
    started_at[server_id] = time.time()
    active_players[server_id] = []
    console_dropped[server_id] = 0
    console_logs[server_id] = [f"[{datetime.now().strftime('%H:%M:%S')}] Starting {jar_name} with Java {major}..."]
    threading.Thread(target=read_console, args=(server_id, process), daemon=True).start()
    if not automatic:
        notify("server_start", f"Started with {ram_mb} MB on port {meta.get('port', 25565)}", server_id)
    return True, "Server started"

def stop_server(server_id, announce=True):
    if not is_running(server_id):
        return False, "Not running"
    process = running_servers[server_id]
    process.mcm_deliberate = True  # per process, so a quick restart cannot confuse the old exit with a crash
    try:
        if process.stdin:
            process.stdin.write(b"stop\n")
            process.stdin.flush()
        process.wait(timeout=45)
    except Exception:
        process.kill()
    finally:
        running_servers.pop(server_id, None)
        active_players.pop(server_id, None)
        if is_playit_running(server_id):
            stop_playit(server_id)
    if announce:
        notify("server_stop", "Stopped from the panel", server_id)
    return True, "Server stopped"

def read_playit_output(server_id, process):
    playit_logs.setdefault(server_id, [])
    try:
        for line in iter(process.stdout.readline, b""):
            text = line.decode("utf-8", errors="replace").rstrip()
            playit_logs[server_id].append(text)
            if len(playit_logs[server_id]) > 500:
                playit_logs[server_id] = playit_logs[server_id][-300:]
    except Exception:
        pass
    finally:
        if playit_processes.get(server_id) is process:
            playit_processes.pop(server_id, None)

def start_playit(server_id):
    if is_playit_running(server_id):
        return False, "Playit is already running"
    meta = load_meta(server_id)
    secret = (meta.get("playit_secret") or "").strip()
    executable = (meta.get("playit_executable") or "playit").strip() or "playit"
    if not secret:
        return False, "Add a Playit secret first"
    try:
        process = subprocess.Popen(
            [executable, "--secret", secret],
            cwd=str(get_server_path(server_id)),
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
        )
        playit_processes[server_id] = process
        playit_logs[server_id] = ["Starting Playit agent..."]
        threading.Thread(target=read_playit_output, args=(server_id, process), daemon=True).start()
        return True, "Playit agent started"
    except FileNotFoundError:
        return False, f"Playit executable not found: {executable}"
    except Exception as exc:
        return False, str(exc)

def stop_playit(server_id):
    process = playit_processes.get(server_id)
    if not process or process.poll() is not None:
        playit_processes.pop(server_id, None)
        return False, "Playit is not running"
    process.terminate()
    try:
        process.wait(timeout=8)
    except subprocess.TimeoutExpired:
        process.kill()
    playit_processes.pop(server_id, None)
    return True, "Playit agent stopped"

def send_command(server_id, cmd):
    if not is_running(server_id):
        return False, "Server offline"
    process = running_servers[server_id]
    try:
        process.stdin.write((cmd.rstrip() + "\n").encode("utf-8"))
        process.stdin.flush()
        return True, "OK"
    except Exception as e:
        return False, str(e)
