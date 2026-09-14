#!/usr/bin/env python3
"""Fake Minecraft RCON server with simulated players, for developing the map tab
without a running Minecraft server.

    python mock_rcon.py --port 25575 --password devpass
"""
import argparse
import math
import random
import socket
import socketserver
import struct
import threading
import time

AUTH = 3
COMMAND = 2
RESPONSE = 0
AUTH_RESPONSE = 2

NAMES = ["Mrkraps", "orgeco", "Steve", "Alex", "Notch_Fan", "RedstoneRita", "CreeperBait"]


class Player:
    def __init__(self, name, seed):
        self.name = name
        self.health = random.choice([20.0, 20.0, 18.0, 14.5, 9.0])
        self.seed = seed
        self.home = (random.uniform(-400, 400), random.uniform(-400, 400))
        self.speed = random.uniform(0.15, 0.55)
        self.radius = random.uniform(40, 220)

    def position(self, now):
        angle = now * self.speed + self.seed
        x = self.home[0] + math.cos(angle) * self.radius
        z = self.home[1] + math.sin(angle * 0.7) * self.radius
        y = 64 + math.sin(angle * 0.4) * 12
        return round(x, 2), round(y, 2), round(z, 2)


PLAYERS = [Player(name, i * 1.7) for i, name in enumerate(NAMES[:5])]
ISSUED = []


def handle_command(text):
    started = time.time()
    if text.strip() == "list":
        names = ", ".join(p.name for p in PLAYERS)
        return f"There are {len(PLAYERS)} of a max of 20 players online: {names}"
    if text.startswith("data get entity "):
        parts = text.split()
        name, field = parts[3], parts[4] if len(parts) > 4 else "Pos"
        player = next((p for p in PLAYERS if p.name == name), None)
        if not player:
            return "No entity was found"
        if field == "Pos":
            x, y, z = player.position(started)
            return f"{name} has the following entity data: [{x}d, {y}d, {z}d]"
        if field == "Health":
            return f"{name} has the following entity data: {player.health}f"
        if field == "Dimension":
            return f'{name} has the following entity data: "minecraft:overworld"'
    ISSUED.append(text)
    print(f"  command: {text}")
    return f"Ran: {text}"


class Handler(socketserver.BaseRequestHandler):
    def handle(self):
        authed = False
        while True:
            header = self.recv_exact(4)
            if not header:
                return
            (length,) = struct.unpack("<i", header)
            payload = self.recv_exact(length)
            if not payload:
                return
            request_id, kind = struct.unpack("<ii", payload[:8])
            body = payload[8:-2].decode("utf-8", errors="replace")
            if kind == AUTH:
                authed = body == self.server.password
                self.reply(request_id if authed else -1, AUTH_RESPONSE, "")
            elif kind == COMMAND and authed:
                self.reply(request_id, RESPONSE, handle_command(body))
            else:
                self.reply(-1, RESPONSE, "Unauthorized")

    def recv_exact(self, count):
        out = b""
        while len(out) < count:
            chunk = self.request.recv(count - len(out))
            if not chunk:
                return b""
            out += chunk
        return out

    def reply(self, request_id, kind, body):
        payload = struct.pack("<ii", request_id, kind) + body.encode("utf-8") + b"\x00\x00"
        self.request.sendall(struct.pack("<i", len(payload)) + payload)


class Server(socketserver.ThreadingTCPServer):
    allow_reuse_address = True
    daemon_threads = True

    def __init__(self, address, password):
        super().__init__(address, Handler)
        self.password = password


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--port", type=int, default=25575)
    parser.add_argument("--password", default="devpass")
    args = parser.parse_args()
    print(f"Mock RCON on 127.0.0.1:{args.port} (password: {args.password})")
    print(f"Simulating {len(PLAYERS)} players: {', '.join(p.name for p in PLAYERS)}")
    Server(("127.0.0.1", args.port), args.password).serve_forever()
