import json
import os
import zipfile
from datetime import datetime
from types import SimpleNamespace
from unittest import mock

import requests

from manager import automation, backups, javatools, lifecycle, notify, procs, state, updater, util
from tests.support import HOME, AppTestCase, make_server


class PathAndInputSafety(AppTestCase):
    def test_sibling_prefix_cannot_be_reached(self):
        make_server("a_1")
        (make_server("a_17") / "secret.txt").write_text("sibling")
        self.assertIsNone(util.safe_path("a_1", "../a_17/secret.txt"))
        self.assertIsNotNone(util.safe_path("a_1", "server.properties"))
        self.assertEqual(self.owner().get("/api/server/a_1/fs/read?path=../a_17/secret.txt").status_code, 400)

    def test_dotted_server_ids_are_refused(self):
        owner = self.owner()
        for sid in ("..", ".", "..."):
            self.assertEqual(owner.post(f"/api/server/{sid}/delete").status_code, 404, sid)

    def test_install_only_from_modrinth_into_plugins_or_mods(self):
        make_server()
        owner = self.owner()
        good = {"url": "https://cdn.modrinth.com/data/x/y.jar", "filename": "y.jar", "target": "plugins"}
        for change in ({"url": "https://evil.example/y.jar"}, {"target": "../../etc"}, {"filename": "y.exe", "url": "https://cdn.modrinth.com/data/x/y.exe"}):
            self.assertEqual(owner.post("/api/server/alpha_1/install", json={**good, **change}).status_code, 400, change)
        self.assertEqual(owner.get("/api/server/alpha_1/files?folder=../alpha_1").get_json(), [])

    def test_line_breaks_cannot_smuggle_commands_or_properties(self):
        make_server()
        owner = self.owner()
        self.assertEqual(owner.post("/api/server/alpha_1/player-action", json={"action": "op", "player": "x\nop evil"}).status_code, 400)
        self.assertEqual(owner.post("/api/server/alpha_1/properties", json={"props": {"motd": "hi\nop-permission-level=4"}}).status_code, 400)

    def test_port_collision_is_refused(self):
        make_server("a_1", name="First", port=25570)
        reply = self.owner().post("/api/create", json={"name": "Second", "version": "1.21.1", "port": 25570, "accept_eula": True})
        self.assertEqual(reply.status_code, 409)
        self.assertIn("First", reply.get_json()["error"])


class ConsoleAndBackups(AppTestCase):
    def test_console_offsets_survive_trimming_and_restarts(self):
        make_server()
        owner = self.owner()
        state.console_logs["alpha_1"] = [f"l{i}" for i in range(10)]
        state.console_dropped["alpha_1"] = 1000
        reply = owner.get("/api/server/alpha_1/console?since=1005").get_json()
        self.assertEqual((reply["lines"], reply["total"], reply["reset"]), (["l5", "l6", "l7", "l8", "l9"], 1010, False))
        self.assertTrue(owner.get("/api/server/alpha_1/console?since=5000").get_json()["reset"])

    def test_restore_aborts_when_safety_copy_fails_and_never_prunes_its_source(self):
        folder = make_server()
        archive_dir = HOME / "backups" / "alpha_1"
        archive_dir.mkdir(parents=True)
        with zipfile.ZipFile(archive_dir / "backup_old.zip", "w") as bundle:
            bundle.writestr("server.properties", "server-port=1111\n")
        (folder / "keep.txt").write_text("keep me")
        with mock.patch.object(backups, "run_backup", return_value=None):
            self.assertFalse(backups.run_restore("alpha_1", "backup_old.zip", True, 10))
        self.assertTrue((folder / "keep.txt").exists(), "files must survive a failed safety copy")
        self.assertTrue(backups.run_restore("alpha_1", "backup_old.zip", True, 1))
        self.assertEqual((folder / "server.properties").read_text(), "server-port=1111\n")
        self.assertEqual(len(list(archive_dir.glob("*.zip"))), 2)


class JavaRules(AppTestCase):
    def test_required_java_by_minecraft_version(self):
        table = {"1.21.1": 21, "1.21": 21, "1.20.6": 21, "1.20.5": 21, "1.20.4": 17, "1.19.4": 17, "1.18.2": 17,
                 "1.17.1": 17, "1.16.5": 8, "26.1": 25, "snapshot": None, "": None, None: None}
        for version, expected in table.items():
            self.assertEqual(javatools.required_java_major(version), expected, version)

    def test_start_refuses_a_java_that_is_too_old(self):
        make_server(version="1.21.1")
        (HOME / "servers" / "alpha_1" / "server.jar").write_bytes(b"jar")
        with mock.patch.object(procs, "choose_java", return_value=("C:/java17/java.exe", 17)):
            ok, message = procs.start_server("alpha_1")
        self.assertFalse(ok)
        self.assertIn("Java 21", message)

    def test_start_explains_a_missing_java(self):
        make_server()
        (HOME / "servers" / "alpha_1" / "server.jar").write_bytes(b"jar")
        with mock.patch.object(procs, "choose_java", return_value=(None, None)):
            ok, message = procs.start_server("alpha_1")
        self.assertFalse(ok)
        self.assertIn("Java not found", message)

    def test_chooses_lowest_suitable_java(self):
        with mock.patch.object(javatools, "java_candidates", return_value=["a8", "b17", "c21", "d25"]), \
                mock.patch.object(javatools, "java_major", side_effect=lambda p: {"a8": 8, "b17": 17, "c21": 21, "d25": 25}[p]):
            self.assertEqual(javatools.choose_java(21), ("c21", 21))
            self.assertEqual(javatools.choose_java(None), ("d25", 25))
            self.assertEqual(javatools.choose_java(99), ("d25", 25))


class FakeProcess:
    def __init__(self, code, deliberate=False):
        self.code = code
        self.mcm_deliberate = deliberate

    def wait(self, timeout=None):
        return self.code


class CrashRecovery(AppTestCase):
    def run_exit(self, code, deliberate=False):
        seen = []
        with mock.patch.object(state, "exit_hooks", [lambda *args: seen.append(args)]), mock.patch.object(procs, "exit_hooks", [lambda *args: seen.append(args)]):
            procs.finish_process("alpha_1", FakeProcess(code, deliberate))
        return seen[0]

    def test_exit_classification(self):
        self.assertEqual(self.run_exit(1), ("alpha_1", 1, True, False))
        self.assertEqual(self.run_exit(0), ("alpha_1", 0, False, False))
        self.assertEqual(self.run_exit(1, deliberate=True), ("alpha_1", 1, False, True), "a stop from the panel is never a crash")

    def test_auto_restart_is_limited(self):
        make_server(automation={"auto_restart": {"enabled": True, "max_tries": 2, "window_minutes": 10}})
        started = []
        with mock.patch.object(automation, "_delayed_restart", lambda sid, attempt, limit: started.append(attempt)), \
                mock.patch.object(automation, "notify") as note:
            for _ in range(3):
                automation.handle_server_exit("alpha_1", 1, True, False)
        self.assertEqual(started, [1, 2], "third crash in the window must not restart")
        self.assertTrue(any("Gave up" in call.args[1] for call in note.call_args_list))

    def test_no_restart_when_disabled_or_deliberate(self):
        make_server()
        with mock.patch.object(automation, "_delayed_restart") as restart, mock.patch.object(automation, "notify"):
            automation.handle_server_exit("alpha_1", 1, True, False)
            automation.handle_server_exit("alpha_1", 1, False, True)
        restart.assert_not_called()


class Scheduling(AppTestCase):
    def setUp(self):
        super().setUp()
        automation._schedule_memory.clear()
        make_server(automation={"schedule_restart": {"enabled": True, "time": "04:00", "warn_minutes": 5}})
        state.running_servers["alpha_1"] = SimpleNamespace(poll=lambda: None, pid=1)

    def test_warns_then_restarts_once_a_day(self):
        with mock.patch.object(automation, "send_command") as say, mock.patch.object(automation, "restart_server") as restart, \
                mock.patch.object(automation.threading, "Thread", side_effect=lambda target, args, daemon: SimpleNamespace(start=lambda: target(*args))):
            automation.scheduler_tick(datetime(2026, 1, 1, 3, 56))
            automation.scheduler_tick(datetime(2026, 1, 1, 3, 59, 20))
            said = [call.args[1] for call in say.call_args_list]
            self.assertEqual(said, ["say Server restarting in 5 minutes. Finish up what you are doing.",
                                    "say Server restarting in 1 minute. Finish up what you are doing."])
            restart.assert_not_called()
            automation.scheduler_tick(datetime(2026, 1, 1, 4, 0, 10))
            automation.scheduler_tick(datetime(2026, 1, 1, 4, 0, 40))
            self.assertEqual(restart.call_count, 1)

    def test_manager_started_late_does_not_restart(self):
        with mock.patch.object(automation, "restart_server") as restart, mock.patch.object(automation, "send_command"):
            automation.scheduler_tick(datetime(2026, 1, 1, 15, 0))
        restart.assert_not_called()

    def test_settings_are_clamped(self):
        cfg = automation.automation_settings({"automation": {
            "auto_restart": {"enabled": True, "max_tries": 999, "window_minutes": -4},
            "schedule_restart": {"enabled": True, "time": "25:99", "warn_minutes": 0},
            "announcements": {"enabled": True, "interval_minutes": 1, "messages": ["ok", "bad\nline", "", "x" * 500]}}})
        self.assertEqual((cfg["auto_restart"]["max_tries"], cfg["auto_restart"]["window_minutes"]), (10, 1))
        self.assertEqual((cfg["schedule_restart"]["time"], cfg["schedule_restart"]["warn_minutes"]), ("04:00", 1))
        self.assertEqual(cfg["announcements"]["interval_minutes"], 5)
        self.assertEqual([len(m) for m in cfg["announcements"]["messages"]], [2, 200])

    def test_automation_route_validates_time(self):
        owner = self.owner()
        bad = owner.post("/api/server/alpha_1/automation", json={"schedule_restart": {"enabled": True, "time": "noon"}})
        self.assertEqual(bad.status_code, 400)
        good = owner.post("/api/server/alpha_1/automation", json={"auto_restart": {"enabled": True}})
        self.assertTrue(good.get_json()["automation"]["auto_restart"]["enabled"])


class Notifications(AppTestCase):
    HOOK = "https://discord.com/api/webhooks/123456789012345678/AbCdEfGhIjKlMnOpQrStUvWxYz-1234567890"

    def test_webhook_validation(self):
        self.assertTrue(notify.valid_webhook(self.HOOK))
        for bad in ("", "http://discord.com/api/webhooks/1/abcdefghij", "https://evil.example/api/webhooks/1/abcdefghij",
                    "https://discord.com/other/1/abcdefghij", "https://discord.com.evil.example/api/webhooks/1/abcdefghij"):
            self.assertFalse(notify.valid_webhook(bad), bad)

    def test_api_never_returns_the_webhook(self):
        owner = self.owner()
        saved = owner.post("/api/notifications", json={"webhook": self.HOOK, "events": {"player_join": True, "server_stop": False}})
        self.assertTrue(saved.get_json()["configured"])
        self.assertNotIn("AbCdEf", saved.get_data(as_text=True))
        self.assertNotIn("AbCdEf", owner.get("/api/notifications").get_data(as_text=True))
        events = {e["id"]: e["enabled"] for e in owner.get("/api/notifications").get_json()["events"]}
        self.assertEqual((events["player_join"], events["server_stop"], events["server_crash"]), (True, False, True))
        self.assertEqual(owner.post("/api/notifications", json={"webhook": "https://evil.example/x"}).status_code, 400)

    def test_nothing_is_sent_without_a_webhook_or_for_disabled_events(self):
        with mock.patch.object(notify.requests, "post", side_effect=AssertionError("must not send")):
            notify.notify("server_crash", "x")
            self.owner().post("/api/notifications", json={"webhook": self.HOOK, "events": {"server_crash": False}})
            notify.notify("server_crash", "x")

    def test_embed_contents(self):
        make_server(name="Survival SMP")
        embed = notify._embed("server_crash", "boom", "alpha_1", [("Exit", "1")])
        self.assertEqual(embed["title"], "Server crashed")
        self.assertEqual(embed["fields"][0], {"name": "Server", "value": "Survival SMP", "inline": True})


class Updates(AppTestCase):
    def fake_github(self, releases=True):
        def fake(path):
            if path.endswith("/releases/latest"):
                if not releases:
                    raise requests.HTTPError(response=SimpleNamespace(status_code=404))
                return {"tag_name": "v9.9.9", "name": "Nine", "html_url": "https://example/r", "body": "notes"}
            if "/commits/" in path:
                ref = path.rsplit("/", 1)[1]
                return {"sha": ("a" if ref == "v9.9.9" else "b") * 40, "commit": {"message": f"msg {ref}\nmore", "committer": {"date": "2026-01-01T00:00:00Z"}}}
            if "/compare/" in path:
                return {"commits": [{"commit": {"message": "one"}}], "ahead_by": 1,
                        "files": [{"filename": "manager/auth.py", "status": "modified", "additions": 3, "deletions": 1}]}
            raise AssertionError(path)
        return fake

    def test_release_channel_follows_the_newest_release(self):
        with mock.patch.object(updater, "github_json", self.fake_github()):
            info = updater.fetch_update_status({"installed": "old", "channel": "releases"})
        self.assertEqual((info["ref"], info["release"]["tag"], info["update_available"]), ("v9.9.9", "v9.9.9", True))
        self.assertEqual(info["files"], [{"name": "manager/auth.py", "status": "modified", "added": 3, "removed": 1}])

    def test_falls_back_to_the_branch_without_releases(self):
        with mock.patch.object(updater, "github_json", self.fake_github(releases=False)):
            info = updater.fetch_update_status({"installed": "old", "channel": "releases"})
        self.assertEqual((info["ref"], info["release"]), ("main", None))

    def test_unattended_install_only_follows_releases(self):
        base = {"auto_check": True, "auto_install": True, "last_check": None, "installed": "old", "latest": None}
        for channel, release, expect_install in (("releases", {"tag": "v1"}, True), ("main", None, False), ("releases", None, False)):
            latest = {"update_available": True, "release": release, "sha": "x", "short": "x", "ref": "main"}
            with mock.patch.object(updater, "load_update_state", return_value={**base, "channel": channel}), \
                    mock.patch.object(updater, "save_update_state"), mock.patch.object(updater, "fetch_update_status", return_value=latest), \
                    mock.patch.object(updater, "apply_manager_update", return_value={"ref": "v1", "short": "x"}) as apply, \
                    mock.patch.object(updater, "notify"):
                updater.check_manager_update()
            self.assertEqual(apply.called, expect_install, (channel, release))

    def test_user_data_is_never_overwritten(self):
        for name in ("servers/x/world", "backups/y.zip", "staff.json", ".secret_key", "audit.jsonl", "manager_settings.json", "restart_state.json", "update_state.json"):
            self.assertTrue(updater.is_protected(name), name)
        self.assertFalse(updater.is_protected("manager/auth.py"))


class RestartAndBoot(AppTestCase):
    def test_boot_is_public(self):
        reply = self.client().get("/api/boot").get_json()
        self.assertEqual(reply["boot"], lifecycle.BOOT_ID)
        self.assertIn("version", reply)

    def test_restart_needs_a_supervisor(self):
        owner = self.owner()
        with mock.patch.dict(os.environ, {"MCM_CHILD": "", "WERKZEUG_RUN_MAIN": ""}):
            reply = owner.post("/api/manager/restart", json={})
        self.assertEqual(reply.status_code, 409)

    def test_restart_asks_before_stopping_servers_and_remembers_them(self):
        make_server()
        state.running_servers["alpha_1"] = SimpleNamespace(poll=lambda: None, pid=1)
        owner = self.owner()
        with mock.patch.dict(os.environ, {"MCM_CHILD": "1"}), mock.patch.object(lifecycle, "_shutdown_and_exit") as shutdown:
            asked = owner.post("/api/manager/restart", json={})
            self.assertEqual(asked.status_code, 409)
            self.assertTrue(asked.get_json()["needs_confirm"])
            shutdown.assert_not_called()
            confirmed = owner.post("/api/manager/restart", json={"stop_servers": True})
            self.assertTrue(confirmed.get_json()["restarting"])
        self.assertEqual(json.loads((HOME / "restart_state.json").read_text())["resume"], ["alpha_1"])

    def test_servers_resume_after_restart_and_stale_state_is_ignored(self):
        make_server()
        (HOME / "restart_state.json").write_text(json.dumps({"resume": ["alpha_1"], "at": __import__("time").time()}))
        with mock.patch.object(lifecycle, "start_server", return_value=(True, "ok")) as start, mock.patch.object(lifecycle, "notify"):
            lifecycle.resume_servers()
        start.assert_called_once()
        self.assertFalse((HOME / "restart_state.json").exists(), "state file is consumed so a crash loop cannot repeat it")
        (HOME / "restart_state.json").write_text(json.dumps({"resume": ["alpha_1"], "at": 1}))
        with mock.patch.object(lifecycle, "start_server") as start:
            lifecycle.resume_servers()
        start.assert_not_called()


class Stats(AppTestCase):
    def test_stats_endpoint_shape(self):
        reply = self.owner().get("/api/stats").get_json()
        self.assertTrue(reply["ok"])
        self.assertEqual(reply["servers"], {})
        self.assertIn("host", reply)

    def test_activity_is_per_server(self):
        make_server("a_1")
        make_server("b_2")
        owner = self.owner()
        owner.post("/api/server/a_1/command", json={"command": "list"})
        entries = owner.get("/api/server/b_2/activity").get_json()["entries"]
        self.assertEqual(entries, [])
        self.assertTrue(owner.get("/api/server/a_1/activity").get_json()["entries"])
