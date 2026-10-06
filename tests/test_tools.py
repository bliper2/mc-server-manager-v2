import gzip
import json
import time
from types import SimpleNamespace
from unittest import mock

from manager import automation, lifecycle, procs, state, store, util
from tests.support import HOME, AppTestCase, make_server


class Transport(AppTestCase):
    def test_security_headers_and_csp(self):
        reply = self.client().get("/")
        self.assertEqual(reply.headers["X-Frame-Options"], "DENY")
        self.assertEqual(reply.headers["X-Content-Type-Options"], "nosniff")
        self.assertIn("frame-ancestors 'none'", reply.headers["Content-Security-Policy"])
        self.assertEqual(self.client().get("/api/boot").headers["Cache-Control"], "no-store")

    def test_gzip_for_large_text_only_when_asked(self):
        plain = self.client().get("/static/css/app.css")
        zipped = self.client().get("/static/css/app.css", headers={"Accept-Encoding": "gzip"})
        self.assertNotIn("Content-Encoding", plain.headers)
        self.assertEqual(zipped.headers["Content-Encoding"], "gzip")
        self.assertEqual(gzip.decompress(zipped.data), plain.data)
        self.assertIn("Accept-Encoding", zipped.headers["Vary"])

    def test_static_is_cached_forever_only_with_a_version(self):
        with mock.patch("manager.web.DEV_MODE", False):
            versioned = self.client().get("/static/css/app.css?v=abc")
            bare = self.client().get("/static/css/app.css")
        self.assertIn("immutable", versioned.headers["Cache-Control"])
        self.assertNotIn("immutable", bare.headers.get("Cache-Control", ""))

    def test_session_cookie_is_secure_behind_https(self):
        owner = self.client()
        reply = owner.post("/api/auth/setup", json={"username": "owner", "password": "correct horse"}, headers={"X-Forwarded-Proto": "https"})
        # X-Forwarded-For is absent, so setup is allowed; the cookie must carry Secure
        cookie = reply.headers.get("Set-Cookie", "")
        self.assertIn("Secure", cookie)
        self.assertIn("SameSite=Strict", cookie)
        self.assertIn("HttpOnly", cookie)

    def test_health_is_public_and_minimal(self):
        reply = self.client().get("/api/health").get_json()
        self.assertEqual(set(reply), {"ok", "version", "uptime"})


class Accounts(AppTestCase):
    def test_weak_passwords_are_refused(self):
        owner = self.owner()
        for weak in ("password123", "aaaaaaaaaa", "Minecraft"):
            reply = owner.post("/api/staff", json={"username": "weak", "password": weak})
            self.assertEqual(reply.status_code, 400, weak)

    def test_signout_everywhere_keeps_this_session_only(self):
        owner = self.owner()
        elsewhere = self.client()
        elsewhere.post("/api/auth/login", json={"username": "owner", "password": "correct horse"})
        self.assertEqual(elsewhere.get("/api/servers").status_code, 200)
        self.assertEqual(owner.post("/api/auth/signout-all").status_code, 200)
        self.assertEqual(owner.get("/api/servers").status_code, 200)
        self.assertEqual(elsewhere.get("/api/servers").status_code, 401)

    def test_last_seen_is_recorded(self):
        owner = self.owner()
        owner.get("/api/servers")
        accounts = owner.get("/api/staff").get_json()["accounts"]
        self.assertTrue(accounts[0]["last_seen"])

    def test_audit_csv_is_owner_only_and_formula_safe(self):
        owner = self.owner()
        alice = self.staff(owner)
        store.audit("alice", "=HYPERLINK(\"http://x\")", server="s1")
        csv_text = owner.get("/api/staff/audit.csv").get_data(as_text=True)
        self.assertTrue(csv_text.startswith("at,user,action"))
        self.assertIn("'=HYPERLINK", csv_text)
        self.assertEqual(alice.get("/api/staff/audit.csv").status_code, 403)

    def test_diagnostics_and_log_are_owner_only(self):
        owner = self.owner()
        alice = self.staff(owner)
        report = owner.get("/api/diagnostics").get_json()
        for key in ("python", "platform", "free_mb", "java", "update"):
            self.assertIn(key, report)
        self.assertEqual(alice.get("/api/diagnostics").status_code, 403)
        self.assertEqual(alice.get("/api/manager/log").status_code, 403)
        self.assertTrue(owner.get("/api/manager/log").get_json()["ok"])


class LaunchSettings(AppTestCase):
    def test_flag_validation(self):
        self.assertEqual(procs.parse_custom_flags("-XX:+UseZGC -Dfoo=bar")[1], None)
        for bad in ("-jar evil.jar", "-javaagent:x.jar", "-Xmx99G", "-XX:OnError=calc", "rm -rf /", "-cp x", "-D" + "a" * 200):
            self.assertIsNotNone(procs.parse_custom_flags(bad)[1], bad)
        self.assertIsNotNone(procs.parse_custom_flags(" ".join(["-Da"] * 41))[1])

    def test_command_line(self):
        meta = {"launch": {"flags": "optimized"}}
        cmd = procs.jvm_command("java", meta, "server.jar", 4096)
        self.assertEqual(cmd[:3], ["java", "-Xms4096M", "-Xmx4096M"])
        self.assertIn("-XX:+UseG1GC", cmd)
        self.assertEqual(cmd[-3:], ["-jar", "server.jar", "nogui"])
        plain = procs.jvm_command("java", {}, "server.jar", 4096)
        self.assertEqual(plain[:3], ["java", "-Xms2048M", "-Xmx4096M"])
        custom = procs.jvm_command("java", {"launch": {"flags": "custom", "custom_flags": "-XX:+UseZGC"}}, "s.jar", 2048)
        self.assertIn("-XX:+UseZGC", custom)

    def test_route_saves_and_rejects(self):
        make_server()
        owner = self.owner()
        ok = owner.post("/api/server/alpha_1/launch", json={"ram": 6144, "flags": "custom", "custom_flags": "-XX:+UseZGC"}).get_json()
        self.assertEqual((ok["ram"], ok["flags"], ok["custom_flags"]), (6144, "custom", "-XX:+UseZGC"))
        self.assertEqual(owner.post("/api/server/alpha_1/launch", json={"ram": 10, "flags": "default"}).status_code, 400)
        self.assertEqual(owner.post("/api/server/alpha_1/launch", json={"ram": 2048, "flags": "custom", "custom_flags": "-jar x"}).status_code, 400)
        self.assertEqual(owner.post("/api/server/alpha_1/launch", json={"ram": 2048, "flags": "weird"}).status_code, 400)


class Eula(AppTestCase):
    def test_creation_needs_consent(self):
        make_server("taken", port=25570)
        reply = self.owner().post("/api/create", json={"name": "X", "version": "1.21.1", "port": 25571})
        self.assertEqual(reply.status_code, 400)
        self.assertIn("EULA", reply.get_json()["error"])

    def test_start_refuses_when_explicitly_not_accepted(self):
        make_server(eula_accepted=False)
        (HOME / "servers" / "alpha_1" / "server.jar").write_bytes(b"jar")
        with mock.patch.object(procs, "choose_java", return_value=("java", 21)):
            ok, message = procs.start_server("alpha_1")
        self.assertFalse(ok)
        self.assertIn("EULA", message)

    def test_start_lock_prevents_double_launch(self):
        make_server()
        (HOME / "servers" / "alpha_1" / "server.jar").write_bytes(b"jar")
        launched = []

        def fake_popen(*args, **kwargs):
            launched.append(args)
            return SimpleNamespace(poll=lambda: None, pid=1, stdout=SimpleNamespace(readline=lambda: b""), stdin=None, wait=lambda timeout=None: 0)

        with mock.patch.object(procs, "choose_java", return_value=("java", 21)), mock.patch.object(procs.subprocess, "Popen", fake_popen), \
                mock.patch.object(procs.threading, "Thread", lambda **kw: SimpleNamespace(start=lambda: None)):
            first = procs.start_server("alpha_1")
            second = procs.start_server("alpha_1")
        self.assertTrue(first[0])
        self.assertEqual(second, (False, "Already running"))
        self.assertEqual(len(launched), 1)


class ServerTools(AppTestCase):
    def test_rename_validates(self):
        make_server()
        owner = self.owner()
        self.assertEqual(owner.post("/api/server/alpha_1/rename", json={"name": "  "}).status_code, 400)
        self.assertEqual(owner.post("/api/server/alpha_1/rename", json={"name": "x" * 41}).status_code, 400)
        self.assertEqual(owner.post("/api/server/alpha_1/rename", json={"name": "New name"}).get_json()["name"], "New name")
        self.assertEqual(store.load_meta("alpha_1")["name"], "New name")

    def test_clone_copies_world_but_not_secrets_and_picks_a_free_port(self):
        source = make_server(port=25565, playit_secret="TOPSECRET", rcon={"enabled": True, "port": 25575, "password": "pw"})
        (source / "world").mkdir()
        (source / "world" / "level.dat").write_text("world data")
        (source / "logs").mkdir()
        (source / "logs" / "latest.log").write_text("big log")
        owner = self.owner()
        reply = owner.post("/api/server/alpha_1/clone", json={"name": "Copy A"}).get_json()
        self.assertTrue(reply["ok"], reply)
        copy = HOME / "servers" / reply["id"]
        self.assertEqual((copy / "world" / "level.dat").read_text(), "world data")
        self.assertFalse((copy / "logs").exists())
        meta = json.loads((copy / "manager_meta.json").read_text())
        self.assertEqual((meta["name"], meta["port"]), ("Copy A", 25566))
        self.assertNotIn("playit_secret", meta)
        self.assertNotIn("rcon", meta)
        props = (copy / "server.properties").read_text()
        self.assertIn("server-port=25566", props)
        self.assertIn("enable-rcon=false", props)

    def test_clone_refused_while_running(self):
        make_server()
        state.running_servers["alpha_1"] = SimpleNamespace(poll=lambda: None, pid=1)
        self.assertEqual(self.owner().post("/api/server/alpha_1/clone", json={}).status_code, 409)

    def test_playtime_counts_finished_and_live_sessions(self):
        make_server()
        store.add_playtime("alpha_1", "Steve", 120)
        store.add_playtime("alpha_1", "Steve", 60)
        store.add_playtime("alpha_1", "Blip", 2)  # reconnect flap, ignored
        state.joined_at["alpha_1"] = {"Alex": time.time() - 30}
        players = self.owner().get("/api/server/alpha_1/playtime").get_json()["players"]
        self.assertEqual([p["name"] for p in players], ["Steve", "Alex"])
        self.assertEqual((players[0]["seconds"], players[0]["sessions"]), (180, 2))
        self.assertTrue(players[1]["online"])

    def test_join_and_leave_lines_build_playtime(self):
        make_server()
        with mock.patch.object(procs, "notify"):
            procs.update_active_players("alpha_1", "[10:00:00] [Server thread/INFO]: Steve joined the game")
            state.joined_at["alpha_1"]["Steve"] -= 90
            procs.update_active_players("alpha_1", "[10:01:30] [Server thread/INFO]: Steve left the game")
        self.assertEqual(store.load_playtime("alpha_1")["Steve"]["seconds"], 90)

    def test_recent_log_and_crash_reports(self):
        folder = make_server()
        (folder / "logs").mkdir()
        (folder / "logs" / "latest.log").write_text("\n".join(f"line {i}" for i in range(500)))
        (folder / "crash-reports").mkdir()
        (folder / "crash-reports" / "crash-1.txt").write_text("boom")
        (folder / "crash-reports" / "notes.md").write_text("ignore")
        owner = self.owner()
        lines = owner.get("/api/server/alpha_1/logs/latest?lines=50").get_json()["lines"]
        self.assertEqual((len(lines), lines[-1]), (50, "line 499"))
        reports = owner.get("/api/server/alpha_1/crash-reports").get_json()["reports"]
        self.assertEqual([r["name"] for r in reports], ["crash-1.txt"])

    def test_player_names_are_validated(self):
        make_server()
        owner = self.owner()
        for bad in ("@a", "x y", "a;b", "x" * 40):
            self.assertEqual(owner.post("/api/server/alpha_1/player-action", json={"action": "op", "player": bad}).status_code, 400, bad)
        self.assertNotEqual(owner.post("/api/server/alpha_1/player-action", json={"action": "op", "player": ".BedrockUser"}).status_code, 400)


class PluginsAndFiles(AppTestCase):
    def setUp(self):
        super().setUp()
        self.folder = make_server()
        (self.folder / "plugins").mkdir()
        (self.folder / "plugins" / "Essentials.jar").write_bytes(b"x")
        self.owner_client = self.owner()

    def test_disable_and_enable_round_trip(self):
        off = self.owner_client.post("/api/server/alpha_1/plugins/toggle", json={"folder": "plugins", "name": "Essentials.jar", "enabled": False}).get_json()
        self.assertEqual(off["name"], "Essentials.jar.disabled")
        listing = self.owner_client.get("/api/server/alpha_1/files?folder=plugins").get_json()
        self.assertEqual([(f["name"], f["enabled"]) for f in listing], [("Essentials.jar.disabled", False)])
        on = self.owner_client.post("/api/server/alpha_1/plugins/toggle", json={"folder": "plugins", "name": "Essentials.jar.disabled", "enabled": True}).get_json()
        self.assertEqual(on["name"], "Essentials.jar")

    def test_plugin_paths_are_confined(self):
        for body in ({"folder": "plugins", "name": "../manager_meta.json", "enabled": False}, {"folder": "world", "name": "Essentials.jar", "enabled": False},
                     {"folder": "plugins", "name": "notes.txt", "enabled": False}):
            self.assertEqual(self.owner_client.post("/api/server/alpha_1/plugins/toggle", json=body).status_code, 400, body)
            self.assertEqual(self.owner_client.post("/api/server/alpha_1/plugins/delete", json=body).status_code, 400, body)
        self.assertTrue((self.folder / "manager_meta.json").exists())

    def test_delete_plugin(self):
        self.assertTrue(self.owner_client.post("/api/server/alpha_1/plugins/delete", json={"folder": "plugins", "name": "Essentials.jar"}).get_json()["ok"])
        self.assertFalse((self.folder / "plugins" / "Essentials.jar").exists())

    def test_file_rename_rules(self):
        (self.folder / "old.txt").write_text("x")
        (self.folder / "taken.txt").write_text("y")
        post = lambda path, name: self.owner_client.post("/api/server/alpha_1/fs/rename", json={"path": path, "name": name})
        self.assertEqual(post("old.txt", "new.txt").status_code, 200)
        self.assertTrue((self.folder / "new.txt").exists())
        self.assertEqual(post("new.txt", "taken.txt").status_code, 409)
        for bad in ("../escape.txt", "a/b.txt", "", ".."):
            self.assertEqual(post("new.txt", bad).status_code, 400, bad)
        self.assertEqual(post("manager_meta.json", "meta.json").status_code, 403)
        make_server("other_2")
        (HOME / "servers" / "other_2" / "theirs.txt").write_text("z")
        self.assertEqual(post("../other_2/theirs.txt", "x.txt").status_code, 404)

    def test_oversized_edit_is_refused(self):
        big = "a" * (2 * 1024 * 1024 + 1)
        self.assertEqual(self.owner_client.post("/api/server/alpha_1/fs/write", json={"path": "big.txt", "content": big}).status_code, 413)


class Watchers(AppTestCase):
    def test_new_events_are_known_to_notifications(self):
        from manager import notify
        for event in ("resource_alert", "server_hung", "disk_low"):
            self.assertIn(event, notify.EVENTS)
            self.assertIn(event, notify.EVENT_LABELS)

    def test_graceful_shutdown_stops_running_servers(self):
        make_server()
        state.running_servers["alpha_1"] = SimpleNamespace(poll=lambda: None, pid=1)
        with mock.patch.object(lifecycle, "stop_server") as stop:
            lifecycle.shutdown_servers()
        stop.assert_called_once_with("alpha_1", False)

    def test_disk_endpoint(self):
        folder = make_server()
        (folder / "world.bin").write_bytes(b"0" * 2_000_000)
        util._size_cache.clear()
        reply = self.owner().get("/api/disk").get_json()
        self.assertGreaterEqual(reply["servers"]["alpha_1"], 1)
        self.assertIn("free_mb", reply)


T0 = 1_700_000_000  # a realistic clock: the cooldown compares against time since the epoch


class WatcherBehaviour(AppTestCase):
    def setUp(self):
        super().setUp()
        automation._watch["high"].clear()
        automation._watch["hung"].clear()
        automation._watch["alerted"].clear()
        make_server(ram=1000)

    def snapshot(self, cpu=5.0, ram=100, uptime=600):
        return lambda: {"servers": {"alpha_1": {"cpu": cpu, "ram_mb": ram, "uptime": uptime, "players": 0}}}

    def test_sustained_cpu_alerts_once_then_cools_down(self):
        with mock.patch.object(automation, "notify") as note:
            for tick in range(1, automation.ALERT_SAMPLES + 1):
                automation.run_watchers(tick, now=T0 + tick, collect=self.snapshot(cpu=97), ping=lambda p: 1, free_mb=lambda: {"free_mb": 10**6})
            self.assertEqual([c.args[0] for c in note.call_args_list], ["resource_alert"])
            automation.run_watchers(9, now=T0 + 100, collect=self.snapshot(cpu=97), ping=lambda p: 1, free_mb=lambda: {"free_mb": 10**6})
            self.assertEqual(note.call_count, 1, "cooldown prevents a second alert")

    def test_a_dip_resets_the_count(self):
        with mock.patch.object(automation, "notify") as note:
            for tick in range(1, 20):
                cpu = 5 if tick % 4 == 0 else 99
                automation.run_watchers(tick, now=T0 + tick, collect=self.snapshot(cpu=cpu), ping=lambda p: 1, free_mb=lambda: {"free_mb": 10**6})
        note.assert_not_called()

    def test_memory_is_measured_against_the_servers_own_limit(self):
        with mock.patch.object(automation, "notify") as note:
            for tick in range(1, automation.ALERT_SAMPLES + 1):
                automation.run_watchers(tick, now=T0 + tick, collect=self.snapshot(ram=950), ping=lambda p: 1, free_mb=lambda: {"free_mb": 10**6})
        self.assertIn("Memory", note.call_args.args[1])

    def test_hung_server_is_reported_after_repeated_silence_not_during_startup(self):
        with mock.patch.object(automation, "notify") as note:
            for tick in range(0, 4 * automation.HUNG_CHECKS * 2, 4):
                automation.run_watchers(tick, now=T0 + tick * 15, collect=self.snapshot(uptime=60), ping=lambda p: None, free_mb=lambda: {"free_mb": 10**6})
            note.assert_not_called()
            for tick in range(0, 4 * automation.HUNG_CHECKS * 2, 4):
                automation.run_watchers(tick, now=T0 + 10_000 + tick * 15, collect=self.snapshot(uptime=900), ping=lambda p: None, free_mb=lambda: {"free_mb": 10**6})
        self.assertEqual([c.args[0] for c in note.call_args_list], ["server_hung"])

    def test_low_disk_alerts_once_a_day(self):
        with mock.patch.object(automation, "notify") as note:
            automation.run_watchers(40, now=T0, collect=lambda: {"servers": {}}, free_mb=lambda: {"free_mb": 1024})
            automation.run_watchers(80, now=T0 + 900, collect=lambda: {"servers": {}}, free_mb=lambda: {"free_mb": 1024})
        self.assertEqual([c.args[0] for c in note.call_args_list], ["disk_low"])
