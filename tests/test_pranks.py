import json
from unittest import mock

from manager import pranks, state
from tests.support import AppTestCase, make_server
from types import SimpleNamespace


class PrankTimelines(AppTestCase):
    def test_every_prank_builds_commands_without_line_breaks(self):
        for item in pranks.PRANKS:
            steps = pranks.build_timeline(item["id"], ["Steve"], {"target": "Alex"})
            self.assertTrue(steps, item["id"])
            for when, command in steps:
                self.assertNotIn("\n", command)
                self.assertLessEqual(when, pranks.MAX_TIMELINE_SECONDS)

    def test_text_cannot_break_out_of_the_json(self):
        steps = pranks.build_timeline("fake_chat", ["Steve"], {"from": 'a"b', "message": 'x"}] kill @a'})
        payload = steps[0][1].split(" ", 2)[2]
        self.assertEqual(json.loads(payload), '<a"b> x"}] kill @a')

    def test_several_players_get_their_own_commands_and_broadcasts_run_once(self):
        self.assertEqual(len(pranks.build_timeline("blindness", ["A", "B"], {})), 2)
        broadcast = pranks.build_timeline("herobrine", ["A", "B"], {})
        self.assertTrue(all(" @a" in command for _, command in broadcast if command.startswith("tellraw")))

    def test_teleport_needs_a_valid_name(self):
        with self.assertRaises(ValueError):
            pranks.build_timeline("tp_to", ["A"], {"target": "x y"})


class PrankRoute(AppTestCase):
    def test_needs_running_server_and_valid_players(self):
        make_server()
        owner = self.owner()
        self.assertEqual(owner.post("/api/server/alpha_1/prank", json={"prank": "creeper", "players": []}).status_code, 400)
        self.assertEqual(owner.post("/api/server/alpha_1/prank", json={"prank": "creeper", "players": ["a\nb"]}).status_code, 400)
        self.assertEqual(owner.post("/api/server/alpha_1/prank", json={"prank": "creeper", "players": ["Steve"]}).status_code, 409)

    def test_sends_commands_when_running(self):
        make_server()
        state.running_servers["alpha_1"] = SimpleNamespace(poll=lambda: None, pid=1)
        sent = []
        owner = self.owner()
        with mock.patch.object(pranks, "send_command", side_effect=lambda sid, cmd: sent.append(cmd) or (True, "OK")):
            reply = owner.post("/api/server/alpha_1/prank", json={"prank": "creeper", "players": ["Steve"]})
            self.assertTrue(reply.get_json()["ok"])
            for _ in range(50):
                if sent:
                    break
                __import__("time").sleep(0.05)
        self.assertTrue(any("creeper.primed" in command for command in sent))
        self.assertEqual(owner.post("/api/server/alpha_1/prank", json={"prank": "nope", "players": ["Steve"]}).status_code, 400)
