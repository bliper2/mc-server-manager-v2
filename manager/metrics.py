"""Live CPU and memory numbers for running servers and the host."""

import os
import time

from .state import active_players, running_servers, started_at
from .store import is_running
from .util import psutil

_tracked: dict = {}  # pid -> psutil.Process, kept so cpu_percent has a previous sample to compare with


def collect() -> dict:
    """Snapshot for every running server. Without psutil only uptime is reported."""
    now = time.time()
    servers = {}
    for sid, process in list(running_servers.items()):
        if not is_running(sid):
            continue
        entry = {"uptime": int(now - started_at.get(sid, now)), "cpu": None, "ram_mb": None, "threads": None, "players": len(active_players.get(sid, []))}
        if psutil is not None:
            try:
                handle = _tracked.get(process.pid) or psutil.Process(process.pid)
                _tracked[process.pid] = handle
                with handle.oneshot():
                    entry["cpu"] = round(handle.cpu_percent(None) / (os.cpu_count() or 1), 1)
                    entry["ram_mb"] = round(handle.memory_info().rss / 1048576)
                    entry["threads"] = handle.num_threads()
            except psutil.Error:
                _tracked.pop(process.pid, None)
        servers[sid] = entry
    for pid in [pid for pid in _tracked if pid not in {p.pid for p in running_servers.values()}]:
        _tracked.pop(pid, None)
    host = {"cpu": None, "ram_percent": None, "ram_used_mb": None, "ram_total_mb": None}
    if psutil is not None:
        try:
            memory = psutil.virtual_memory()
            host = {
                "cpu": psutil.cpu_percent(None),
                "ram_percent": memory.percent,
                "ram_used_mb": round((memory.total - memory.available) / 1048576),
                "ram_total_mb": round(memory.total / 1048576),
            }
        except psutil.Error:
            pass
    return {"ok": True, "servers": servers, "host": host}
