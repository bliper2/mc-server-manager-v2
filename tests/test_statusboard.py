"""The Discord status board: one message per server that says whether it is on or off, with its address."""
import json
from unittest import mock

import requests

from manager import notify, state, statusboard
from tests.support import HOME, AppTestCase, make_server

HOOK = "https://discord.com/api/webhooks/123456789012345678/AbCdEfGhIjKlMnOpQrStUvWxYz-1234567890"
OTHER_HOOK = "https://discord.com/api/webhooks/987654321098765432/ZyXwVuTsRqPoNmLkJiHgFeDcBa-0987654321"


class FakeResponse:
    def __init__(self, status=200, body=None):
        self.status_code, self._body = status, body or {}

    def json(self):
        return self._body


class FakeDiscord:
    """Stands in for requests.request: records every call and answers like Discord does."""

    def __init__(self):
        self.calls, self.next_id, self.messages, self.fail = [], 1000, {}, None

    def __call__(self, method, url, json=None, headers=None, timeout=None):
        self.calls.append((method, url, json))
        if self.fail:
            return self.fail(method, url) if callable(self.fail) else self.fail
        if method == "POST":
            self.next_id += 1
            self.messages[str(self.next_id)] = json
            return FakeResponse(200, {"id": str(self.next_id)})
        message = url.split("/messages/")[1].split("?")[0]
        if method == "PATCH":
            if message not in self.messages:
                return FakeResponse(404)
            self.messages[message] = json
            return FakeResponse(200, {"id": message})
        if method == "DELETE":
            self.messages.pop(message, None)
            return FakeResponse(204)
        raise AssertionError(method)

    def methods(self):
        return [call[0] for call in self.calls]


class StatusBoardCase(AppTestCase):
    def setUp(self):
        super().setUp()
        self.discord = FakeDiscord()
        statusboard._ip_cache.update(ip="203.0.113.7", at=10 ** 12)  # a known public IP, no network
        statusboard._pending.clear()
        patches = [mock.patch.object(statusboard.requests, "request", self.discord), mock.patch.object(statusboard, "ensure_worker"),
                   mock.patch.object(statusboard, "ping_minecraft_server", return_value=12), mock.patch.object(statusboard.time, "sleep")]
        for patch in patches:
            patch.start()
            self.addCleanup(patch.stop)
        self.folder = make_server(name="Survival", type="purpur", version="1.21.1", port=25566)
        (self.folder / "server.properties").write_text("server-port=25566\nmax-players=30\n", encoding="utf-8")

    def configure(self, **extra):
        owner = self.owner()
        reply = owner.post("/api/server/alpha_1/status-hook", json={"webhook": HOOK, **extra})
        self.assertEqual(reply.status_code, 200, reply.get_json())
        return owner

    def run_server(self, running=True):
        if running:
            process = mock.Mock()
            process.poll.return_value = None
            state.running_servers["alpha_1"] = process
        else:
            state.running_servers.pop("alpha_1", None)


class Settings(StatusBoardCase):
    def test_the_webhook_is_stored_but_never_sent_back(self):
        owner = self.configure(address="play.example.com", interval=10)
        body = owner.get("/api/server/alpha_1/status-hook").get_json()
        self.assertTrue(body["configured"])
        self.assertEqual((body["address"], body["interval"]), ("play.example.com", 10))
        self.assertEqual(body["hint"], "…567890")
        self.assertNotIn("discord.com", json.dumps(body))
        self.assertNotIn("discord.com", json.dumps(owner.get("/api/servers").get_json()), "the server list must not carry it either")
        stored = json.loads((HOME / "manager_settings.json").read_text())
        self.assertEqual(stored["status"]["alpha_1"]["webhook"], HOOK)

    def test_only_discord_webhook_urls_and_listed_intervals_are_accepted(self):
        owner = self.owner()
        for bad in ("https://evil.example/api/webhooks/123456789012345678/abcdefghijklmnop", "http://discord.com/api/webhooks/123456789012345678/abcdefghij", "not a url", "https://discord.com/other"):
            self.assertEqual(owner.post("/api/server/alpha_1/status-hook", json={"webhook": bad}).status_code, 400, bad)
        self.assertEqual(owner.post("/api/server/alpha_1/status-hook", json={"interval": 7}).status_code, 400)
        self.assertEqual(owner.post("/api/server/alpha_1/status-hook", json={"address": "x" * 101}).status_code, 400)
        self.assertEqual(owner.post("/api/server/nope/status-hook", json={}).status_code, 404)
        self.assertEqual(self.discord.calls, [])

    def test_blank_fields_keep_the_saved_webhook_and_blank_webhook_removes_it(self):
        owner = self.configure()
        owner.post("/api/server/alpha_1/status-hook", json={"address": "mc.example.org", "interval": 0})
        self.assertEqual(statusboard.get_config("alpha_1")["webhook"], HOOK, "saving other fields keeps the URL")
        self.discord.messages["1001"] = {}
        statusboard.set_config("alpha_1", message_id="1001")
        reply = owner.post("/api/server/alpha_1/status-hook", json={"webhook": ""})
        self.assertFalse(reply.get_json()["configured"])
        self.assertEqual(statusboard.get_config("alpha_1"), {})
        self.assertIn("DELETE", self.discord.methods(), "the message is taken out of the channel")

    def test_the_address_is_cleaned_of_markdown_and_line_breaks(self):
        owner = self.configure(address="`play.example.com`\r\n@everyone")
        self.assertEqual(statusboard.get_config("alpha_1")["address"], "play.example.com@everyone")

    def test_only_accounts_that_may_manage_servers_can_change_it(self):
        owner = self.owner()
        files = self.staff(owner, "files", "filespass1", permissions=["files", "control"])
        maker = self.staff(owner, "maker", "makerpass1", permissions=["manage"])
        self.assertEqual(files.post("/api/server/alpha_1/status-hook", json={"webhook": HOOK}).status_code, 403)
        self.assertEqual(files.post("/api/server/alpha_1/status-hook/send").status_code, 403)
        self.assertEqual(maker.post("/api/server/alpha_1/status-hook", json={"webhook": HOOK}).status_code, 200)
        self.assertNotIn("discord.com", json.dumps(files.get("/api/server/alpha_1/status-hook").get_json()))

    def test_secrets_in_the_server_list_are_stripped(self):
        make_server("beta_1", name="Beta", port=25570, playit_secret="topsecret", rcon={"enabled": True, "port": 25575, "password": "hunter2"})
        listed = {s["id"]: s for s in self.owner().get("/api/servers").get_json()}["beta_1"]
        self.assertNotIn("playit_secret", listed)
        self.assertEqual(listed["rcon"], {"enabled": True, "port": 25575})


class Posting(StatusBoardCase):
    def test_the_first_post_creates_the_message_and_remembers_it(self):
        self.configure(address="play.example.com")
        self.run_server()
        self.assertIsNone(statusboard.post_status("alpha_1"))
        method, url, payload = self.discord.calls[0]
        self.assertEqual(method, "POST")
        self.assertIn("wait=true", url, "Discord only returns the message id with wait=true")
        embed = payload["embeds"][0]
        fields = {f["name"]: f["value"] for f in embed["fields"]}
        self.assertEqual(embed["title"], "Survival")
        self.assertEqual(fields["Status"], "🟢 Online")
        self.assertEqual(fields["Address"], "`play.example.com`")
        self.assertEqual(fields["Players"], "0 / 30")
        self.assertEqual(fields["Version"], "Purpur 1.21.1")
        self.assertEqual(statusboard.get_config("alpha_1")["message_id"], "1001")

    def test_later_posts_edit_the_same_message(self):
        self.configure()
        self.run_server()
        statusboard.post_status("alpha_1")
        self.run_server(False)
        statusboard.post_status("alpha_1")
        self.assertEqual(self.discord.methods(), ["POST", "PATCH"])
        self.assertTrue(self.discord.calls[1][1].endswith("/messages/1001"))
        fields = {f["name"]: f["value"] for f in self.discord.calls[1][2]["embeds"][0]["fields"]}
        self.assertEqual(fields["Status"], "🔴 Offline")
        self.assertNotIn("Players", fields)
        self.assertEqual(self.discord.calls[1][2]["embeds"][0]["color"], statusboard.RED)

    def test_a_deleted_message_is_replaced(self):
        self.configure()
        self.run_server()
        statusboard.post_status("alpha_1")
        self.discord.messages.clear()
        self.assertIsNone(statusboard.post_status("alpha_1"))
        self.assertEqual(self.discord.methods(), ["POST", "PATCH", "POST"])
        self.assertEqual(statusboard.get_config("alpha_1")["message_id"], "1002")

    def test_a_dead_webhook_is_reported_plainly_and_nothing_secret_leaks(self):
        self.configure()
        self.discord.fail = FakeResponse(404)
        error = statusboard.post_status("alpha_1")
        self.assertIn("no longer exists", error)
        self.assertEqual(statusboard.get_config("alpha_1")["error"], error)
        self.assertNotIn(HOOK, json.dumps(statusboard.public_view("alpha_1")))

    def test_network_failures_and_rate_limits(self):
        self.configure()
        self.discord.fail = lambda method, url: (_ for _ in ()).throw(requests.ConnectionError(HOOK))
        error = statusboard.post_status("alpha_1")
        self.assertEqual(error, "Could not reach Discord: ConnectionError")
        self.assertNotIn("discord.com", error)
        answers = [FakeResponse(429, {"retry_after": 0.5}), FakeResponse(200, {"id": "77"})]
        self.discord.fail = lambda method, url: answers.pop(0)
        self.assertIsNone(statusboard.post_status("alpha_1"))
        self.assertEqual(statusboard.get_config("alpha_1")["message_id"], "77", "one retry after the limit")
        self.discord.fail = FakeResponse(429, {"retry_after": 1})
        self.assertIn("rate limiting", statusboard.post_status("alpha_1", force_new=True))

    def test_a_webhook_for_a_thread_keeps_its_thread_id(self):
        self.configure()
        statusboard.set_config("alpha_1", webhook=HOOK + "?thread_id=555")
        statusboard.post_status("alpha_1")
        self.assertIn("thread_id=555", self.discord.calls[0][1])
        statusboard.post_status("alpha_1")
        self.assertIn("thread_id=555", self.discord.calls[1][1])
        self.assertIn("/messages/1001", self.discord.calls[1][1])

    def test_the_send_endpoint_posts_now_and_surfaces_errors(self):
        owner = self.configure()
        ok = owner.post("/api/server/alpha_1/status-hook/send", json={})
        self.assertEqual(ok.status_code, 200)
        self.assertTrue(ok.get_json()["configured"])
        self.discord.fail = FakeResponse(401)
        bad = owner.post("/api/server/alpha_1/status-hook/send", json={"new": True})
        self.assertEqual(bad.status_code, 502)
        self.assertIn("no longer exists", bad.get_json()["error"])

    def test_posting_without_a_webhook_says_so(self):
        self.assertEqual(statusboard.post_status("alpha_1"), "Save a webhook URL first")


class WhatItSays(StatusBoardCase):
    def test_the_state_is_online_starting_or_offline(self):
        self.assertEqual(statusboard.current_state("alpha_1"), "offline")
        self.run_server()
        self.assertEqual(statusboard.current_state("alpha_1"), "online")
        with mock.patch.object(statusboard, "ping_minecraft_server", return_value=None):
            self.assertEqual(statusboard.current_state("alpha_1"), "starting")

    def test_the_player_count_comes_from_the_live_list(self):
        self.configure()
        self.run_server()
        state.active_players["alpha_1"] = ["Steve", "Alex"]
        statusboard.post_status("alpha_1")
        fields = {f["name"]: f["value"] for f in self.discord.calls[0][2]["embeds"][0]["fields"]}
        self.assertEqual(fields["Players"], "2 / 30")

    def test_the_address_comes_from_the_owner_then_playit_then_the_public_ip_then_localhost(self):
        cfg = {}
        self.assertEqual(statusboard.resolve_address("alpha_1", cfg), ("203.0.113.7:25566", "public"))
        state.playit_logs["alpha_1"] = ["agent started", "tunnel ready: bright-owl-42.gl.joinmc.link:31337 -> 127.0.0.1:25566"]
        with mock.patch.object(statusboard, "is_playit_running", return_value=True):
            self.assertEqual(statusboard.resolve_address("alpha_1", cfg), ("bright-owl-42.gl.joinmc.link:31337", "playit"))
            self.assertEqual(statusboard.resolve_address("alpha_1", {"address": "mc.example.org"}), ("mc.example.org", "custom"))
        statusboard._ip_cache.update(ip=None, at=10 ** 12)
        self.assertEqual(statusboard.resolve_address("alpha_1", cfg), ("localhost:25566", "local"))

    def test_the_public_ip_lookup_is_cached_and_validated(self):
        statusboard._ip_cache.update(ip=None, at=0.0)
        with mock.patch.object(statusboard.requests, "get", return_value=mock.Mock(text="198.51.100.9\n")) as lookup:
            self.assertEqual(statusboard.public_ip(), "198.51.100.9")
            self.assertEqual(statusboard.public_ip(), "198.51.100.9")
        self.assertEqual(lookup.call_count, 1)
        statusboard._ip_cache.update(ip=None, at=0.0)
        with mock.patch.object(statusboard.requests, "get", return_value=mock.Mock(text="<html>blocked</html>")):
            self.assertIsNone(statusboard.public_ip())
        statusboard._ip_cache.update(ip=None, at=0.0)
        with mock.patch.object(statusboard.requests, "get", side_effect=requests.Timeout()):
            self.assertIsNone(statusboard.public_ip())


class TheLoop(StatusBoardCase):
    def test_a_change_of_state_posts_once_and_a_quiet_server_posts_nothing(self):
        self.configure(interval=0)
        self.run_server()
        statusboard.tick(1000.0)
        self.assertEqual(self.discord.methods(), ["POST"])
        statusboard.tick(1100.0)
        statusboard.tick(1200.0)
        self.assertEqual(self.discord.methods(), ["POST"], "nothing changed, nothing posted")
        self.run_server(False)
        statusboard.tick(1300.0 + 10 ** 9)
        self.assertEqual(self.discord.methods(), ["POST", "PATCH"])

    def test_the_player_count_refreshes_on_its_interval_only_while_online(self):
        self.configure(interval=5)
        self.run_server()
        now = 5000.0
        with mock.patch.object(statusboard.time, "time", return_value=now):
            statusboard.tick(now)
        self.assertEqual(len(self.discord.calls), 1)
        for later, expected in ((now + 100, 1), (now + 301, 2)):
            with mock.patch.object(statusboard.time, "time", return_value=later):
                statusboard.tick(later)
            self.assertEqual(len(self.discord.calls), expected, later)

    def test_a_failing_webhook_is_not_hammered(self):
        self.configure()
        self.discord.fail = FakeResponse(500)
        statusboard.tick(100.0 + 10 ** 9)
        count = len(self.discord.calls)
        self.assertGreaterEqual(count, 1)
        statusboard.tick(110.0 + 10 ** 9)
        self.assertEqual(len(self.discord.calls), count, "it waits a minute after a failure")

    def test_starting_then_online_edits_the_same_message(self):
        self.configure()
        self.run_server()
        with mock.patch.object(statusboard, "ping_minecraft_server", return_value=None):
            statusboard.tick(10 ** 9)
        self.assertEqual(statusboard.get_config("alpha_1")["last_state"], "starting")
        statusboard.tick(10 ** 9 + 70)
        self.assertEqual(self.discord.methods(), ["POST", "PATCH"])
        self.assertEqual(statusboard.get_config("alpha_1")["last_state"], "online")

    def test_servers_without_a_webhook_or_folder_are_skipped(self):
        statusboard.set_config("ghost_9", webhook=HOOK)
        make_server("quiet_1", name="Quiet", port=25580)
        statusboard.tick(10 ** 9)
        self.assertEqual(self.discord.calls, [])

    def test_a_server_event_wakes_the_loop_and_a_shutdown_flush_posts_offline(self):
        self.configure()
        self.run_server()
        statusboard.tick(10 ** 9)
        self.run_server(False)
        with mock.patch.object(statusboard, "_wake") as wake:
            notify.notify("server_stop", "Stopped from the panel", "alpha_1")
        wake.set.assert_called()
        self.assertIn("alpha_1", statusboard._pending)
        statusboard.flush()
        fields = {f["name"]: f["value"] for f in self.discord.calls[-1][2]["embeds"][0]["fields"]}
        self.assertEqual(fields["Status"], "🔴 Offline")

    def test_unrelated_events_and_unconfigured_servers_do_not_wake_it(self):
        self.configure()
        statusboard._pending.clear()
        notify.notify("backup_done", "saved", "alpha_1")
        notify.notify("player_join", "x joined", "alpha_1")
        make_server("beta_1", name="Beta", port=25581)
        notify.notify("server_start", "go", "beta_1")
        self.assertEqual(statusboard._pending, set())

    def test_deleting_a_server_removes_its_status_message(self):
        owner = self.configure()
        self.run_server()
        statusboard.post_status("alpha_1")
        self.run_server(False)
        reply = owner.post("/api/server/alpha_1/delete")
        self.assertEqual(reply.status_code, 200, reply.get_json())
        self.assertIn("DELETE", self.discord.methods())
        self.assertEqual(statusboard.get_config("alpha_1"), {})
