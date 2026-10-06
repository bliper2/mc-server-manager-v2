"""Shared test setup. Importing this module points the manager at a throwaway data folder, so tests never
touch real servers, accounts or settings."""
import json
import os
import shutil
import sys
import tempfile
import unittest
from pathlib import Path

HOME = Path(tempfile.mkdtemp(prefix="mcm_tests_"))
os.environ["MC_MANAGER_HOME"] = str(HOME)
os.environ["MC_MANAGER_DEV"] = "0"
os.environ.pop("MC_MANAGER_TOKEN", None)
ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import manager  # noqa: E402
from manager import auth, state  # noqa: E402

OWNER = ("owner", "correct horse")


def reset_data():
    for name in ("staff.json", "audit.jsonl", "manager_settings.json", "restart_state.json", "update_state.json"):
        (HOME / name).unlink(missing_ok=True)
    for folder in ("servers", "backups", ".imports"):
        shutil.rmtree(HOME / folder, ignore_errors=True)
        (HOME / folder).mkdir(exist_ok=True)
    auth.login_attempts.clear()
    auth.last_seen_cache.clear()
    for name in ("running_servers", "console_logs", "console_dropped", "active_players", "playit_processes", "playit_logs",
                 "import_sessions", "backup_jobs", "update_cache", "map_cache", "started_at", "crash_times", "joined_at", "start_locks"):
        getattr(state, name).clear()
    state.restart_flags.update(pending=False, auto=False)


def make_server(sid="alpha_1", **meta):
    folder = HOME / "servers" / sid
    folder.mkdir(parents=True, exist_ok=True)
    data = {"name": sid, "type": "paper", "version": "1.21.1", "ram": 2048, "port": 25565, "created": "2026-01-01T00:00:00"}
    data.update(meta)
    (folder / "manager_meta.json").write_text(json.dumps(data), encoding="utf-8")
    (folder / "server.properties").write_text(f"server-port={data['port']}\n", encoding="utf-8")
    return folder


class AppTestCase(unittest.TestCase):
    def setUp(self):
        reset_data()
        self.app = manager.app

    def client(self):
        return self.app.test_client()

    def owner(self):
        client = self.client()
        reply = client.post("/api/auth/setup", json={"username": OWNER[0], "password": OWNER[1]})
        self.assertTrue(reply.get_json()["ok"], reply.get_json())
        return client

    def staff(self, owner, name="alice", password="alicepass1", permissions=None):
        body = {"username": name, "password": password}
        if permissions is not None:
            body["permissions"] = permissions
        reply = owner.post("/api/staff", json=body)
        self.assertEqual(reply.status_code, 200, reply.get_json())
        client = self.client()
        login = client.post("/api/auth/login", json={"username": name, "password": password})
        self.assertTrue(login.get_json()["ok"], login.get_json())
        return client
