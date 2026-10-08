"""The "Let friends join" card (addresses, listening, firewall, warnings) and stops that arrive while a server is still starting."""
import socket
from collections import namedtuple
from unittest import mock

from manager import connect, procs, state
from tests.support import AppTestCase, make_server

Addr = namedtuple("Addr", "family address")
Stat = namedtuple("Stat", "isup")


def entry(address):
    return Addr(socket.AF_INET, address)


class ConnectHelp(AppTestCase):
    def test_adapters_are_sorted_into_home_vpn_and_virtual(self):
        self.assertEqual(connect.classify("Ethernet", "10.150.9.235"), ("lan", "Ethernet"))
        self.assertEqual(connect.classify("Radmin VPN", "26.250.192.20"), ("vpn", "Radmin VPN"))
        self.assertEqual(connect.classify("Ethernet 5", "26.1.2.3"), ("vpn", "Radmin VPN"), "recognised by range when the name says nothing")
        self.assertEqual(connect.classify("Unknown adapter Tailscale", "100.98.93.32"), ("vpn", "Tailscale"))
        self.assertEqual(connect.classify("VMware Network Adapter VMnet8", "192.168.158.1")[0], "virtual")

    def test_unusable_addresses_are_left_out_and_home_networks_come_first(self):
        adapters = {"Radmin VPN": [entry("26.250.192.20")], "Ethernet": [entry("10.150.9.235")], "Wi-Fi": [entry("192.168.8.50")],
                    "Loopback": [entry("127.0.0.1")], "Tailscale": [entry("169.254.83.107")], "VMware VMnet1": [entry("192.168.72.1")]}
        stats = {name: Stat(name != "Wi-Fi") for name in adapters}
        with mock.patch.object(connect.psutil, "net_if_addrs", return_value=adapters), mock.patch.object(connect.psutil, "net_if_stats", return_value=stats):
            found = connect.addresses()
        self.assertEqual([(a["address"], a["kind"]) for a in found], [("10.150.9.235", "lan"), ("26.250.192.20", "vpn"), ("192.168.72.1", "virtual")])

    def test_the_endpoint_reports_listening_firewall_and_warnings(self):
        folder = make_server(type="purpur", port=25566)
        (folder / "server.properties").write_text("server-port=25566\nonline-mode=false\nwhite-list=false\n", encoding="utf-8")
        owner = self.owner()
        lan = [{"address": "10.0.0.5", "adapter": "Ethernet", "kind": "lan", "label": "Ethernet", "private": True}]
        with mock.patch.object(connect, "addresses", return_value=lan), mock.patch.object(connect, "firewall_rule", return_value=False), \
                mock.patch.object(connect, "listening", return_value=True), mock.patch.object(connect, "is_running", return_value=True):
            body = owner.get("/api/server/alpha_1/connect").get_json()
        self.assertEqual((body["port"], body["running"], body["listening"], body["firewall"]), (25566, True, True, False))
        self.assertIn("-LocalPort 25566", body["firewall_command"])
        self.assertIn("whitelist are both off", body["warnings"][0])
        self.assertEqual(owner.get("/api/server/nope/connect").status_code, 404)

    def test_a_stopped_server_is_never_reported_as_listening(self):
        make_server(type="purpur")
        with mock.patch.object(connect, "listening", return_value=True), mock.patch.object(connect, "firewall_rule", return_value=None):
            body = self.owner().get("/api/server/alpha_1/connect").get_json()
        self.assertEqual((body["running"], body["listening"]), (False, False))

    def test_warnings_depend_on_online_mode_and_whitelist(self):
        self.assertEqual(connect.warnings({"online-mode": "true"}), [])
        self.assertEqual(connect.warnings({}), [])
        self.assertIn("whitelist are both off", connect.warnings({"online-mode": "false"})[0])
        self.assertIn("only matches names", connect.warnings({"online-mode": "false", "white-list": "true"})[0])

    def test_public_address_lookup_validates_the_answer(self):
        make_server(type="purpur")
        good = mock.Mock(text="37.39.168.235\n", raise_for_status=lambda: None)
        with mock.patch.object(connect.requests, "get", return_value=good):
            self.assertEqual(connect.public_address(), "37.39.168.235")
            self.assertEqual(self.owner().get("/api/server/alpha_1/connect/public").get_json()["address"], "37.39.168.235")
        junk = mock.Mock(text="<html>blocked</html>", raise_for_status=lambda: None)
        with mock.patch.object(connect.requests, "get", return_value=junk):
            self.assertIsNone(connect.public_address())
        with mock.patch.object(connect.requests, "get", side_effect=connect.requests.ConnectionError()):
            self.assertIsNone(connect.public_address())


class FakeProcess:
    """Enough of a Popen for stop_server: records what was written to its stdin."""

    def __init__(self, ready=False):
        self.mcm_ready = ready
        self.sent = []
        self.stdin = mock.Mock(write=self.sent.append, flush=lambda: None)

    def poll(self):
        return None

    def wait(self, timeout=None):
        return 0

    def kill(self):
        self.sent.append(b"KILL")


class StopDuringStartup(AppTestCase):
    """A stop sent before the Done line made Purpur throw a NullPointerException and keep running."""

    def stop(self, process, limit=180):
        state.running_servers["alpha_1"] = process
        with mock.patch.object(procs, "STARTUP_STOP_WAIT", limit), mock.patch.object(procs, "is_running", return_value=True), \
                mock.patch.object(procs, "notify"), mock.patch.object(procs, "is_playit_running", return_value=False):
            return procs.stop_server("alpha_1")

    def test_stop_waits_for_the_done_line(self):
        process = FakeProcess()
        steps = []

        def sleeper(seconds):
            steps.append(seconds)
            if len(steps) == 3:
                process.mcm_ready = True

        with mock.patch.object(procs.time, "sleep", sleeper):
            ok, _ = self.stop(process)
        self.assertTrue(ok)
        self.assertEqual(len(steps), 3, "it waited until the server was ready, then stopped")
        self.assertEqual(process.sent, [b"stop\n"])

    def test_a_server_that_never_finishes_is_still_stopped_after_the_limit(self):
        process = FakeProcess()
        with mock.patch.object(procs.time, "sleep") as nap:
            ok, _ = self.stop(process, limit=2)
        self.assertTrue(ok)
        self.assertEqual(nap.call_count, 4)
        self.assertEqual(process.sent, [b"stop\n"])

    def test_a_ready_server_stops_at_once(self):
        process = FakeProcess(ready=True)
        with mock.patch.object(procs.time, "sleep") as nap:
            self.stop(process)
        self.assertFalse(nap.called)

    def test_the_console_marks_the_server_ready(self):
        process = mock.Mock()
        process.mcm_ready = False
        process.stdout.readline.side_effect = [b'[18:39:04] [Server thread/INFO]: Done (18.070s)! For help, type "help"\n', b""]
        with mock.patch.object(procs, "update_active_players"), mock.patch.object(procs, "finish_process", create=True):
            try:
                procs.read_console("alpha_1", process)
            except Exception:  # noqa: BLE001 - the exit handling after the last line is not under test
                pass
        self.assertTrue(process.mcm_ready)
