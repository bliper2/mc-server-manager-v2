"""Choosing which installed Java a server starts on (for example Java 26 for a plugin built with it)."""
from unittest import mock

from manager import javatools, procs, routes_files
from manager.store import load_meta, save_meta
from tests.support import AppTestCase, make_server

JAVAS = [("C:/java17/bin/java.exe", 17), ("C:/java25/bin/java.exe", 25), ("C:/java26/bin/java.exe", 26)]


class JavaChoice(AppTestCase):
    def test_automatic_picks_the_lowest_that_fits_and_a_choice_overrides_it(self):
        with mock.patch.object(javatools, "installed_javas", return_value=JAVAS):
            self.assertEqual(javatools.choose_java(25), ("C:/java25/bin/java.exe", 25))
            self.assertEqual(javatools.choose_java(25, 26), ("C:/java26/bin/java.exe", 26))
            self.assertEqual(javatools.choose_java(25, 17), ("C:/java25/bin/java.exe", 25), "a Java that is too old for the version is ignored")
            self.assertEqual(javatools.choose_java(25, 99), ("C:/java25/bin/java.exe", 25), "a Java that is not installed is ignored")
            self.assertEqual(javatools.choose_java(None, 17), ("C:/java17/bin/java.exe", 17))
        with mock.patch.object(javatools, "installed_javas", return_value=[]):
            self.assertEqual(javatools.choose_java(25, 26), (None, None))

    def test_the_servers_own_setting_is_read_from_its_meta(self):
        self.assertEqual(javatools.preferred_java({"launch": {"java": "26"}}), 26)
        for meta in ({}, {"launch": {}}, {"launch": {"java": ""}}, {"launch": {"java": "abc"}}, {"launch": "x"}):
            self.assertIsNone(javatools.preferred_java(meta))
        with mock.patch.object(javatools, "installed_javas", return_value=JAVAS):
            self.assertEqual(javatools.java_for({"version": "26.2", "launch": {"java": "26"}})[1], 26)
            self.assertEqual(javatools.java_for({"version": "26.2"})[1], 25)

    def test_the_launch_settings_offer_and_save_only_installed_javas(self):
        make_server(type="purpur", version="26.2")
        owner = self.owner()
        with mock.patch("manager.servertools.installed_javas", return_value=JAVAS):
            body = owner.get("/api/server/alpha_1/launch").get_json()
            self.assertEqual((body["javas"], body["java"], body["java_required"]), ([25, 26], "", 25), "Java 17 is too old for 26.2 so it is not offered")
            self.assertEqual(owner.post("/api/server/alpha_1/launch", json={"ram": 4096, "flags": "default", "java": "26"}).status_code, 200)
            self.assertEqual(owner.get("/api/server/alpha_1/launch").get_json()["java"], "26")
            for bad in ("17", "99", "abc"):
                reply = owner.post("/api/server/alpha_1/launch", json={"ram": 4096, "flags": "default", "java": bad})
                self.assertEqual(reply.status_code, 400, bad)
            self.assertEqual(owner.get("/api/server/alpha_1/launch").get_json()["java"], "26", "a refused save changes nothing")
            self.assertEqual(owner.post("/api/server/alpha_1/launch", json={"ram": 4096, "flags": "default", "java": ""}).status_code, 200)
            self.assertEqual(owner.get("/api/server/alpha_1/launch").get_json()["java"], "")

    def test_the_java_a_server_will_use_is_what_the_plugin_check_and_status_report(self):
        folder = make_server(type="purpur", version="26.2")
        (folder / "plugins").mkdir()
        owner = self.owner()
        with mock.patch.object(javatools, "installed_javas", return_value=JAVAS), mock.patch("manager.servertools.installed_javas", return_value=JAVAS),                 mock.patch.object(routes_files, "folder_problems", return_value={}) as problems:
            owner.get("/api/server/alpha_1/files?folder=plugins")
            self.assertEqual(problems.call_args.args[3], 25)
            owner.post("/api/server/alpha_1/launch", json={"ram": 4096, "flags": "default", "java": "26"})
            owner.get("/api/server/alpha_1/files?folder=plugins")
            self.assertEqual(problems.call_args.args[3], 26, "once Java 26 is chosen, a Java 26 plugin is no longer flagged")
            self.assertEqual(owner.get("/api/java?version=26.2&server=alpha_1").get_json()["major"], 26)
            self.assertEqual(owner.get("/api/java?version=26.2&server=../x").get_json()["major"], 25, "a bad server id is ignored")

    def test_start_uses_the_chosen_java(self):
        folder = make_server(type="purpur", version="26.2")
        (folder / "server.jar").write_bytes(b"x")
        meta = load_meta("alpha_1")
        meta["launch"] = {"flags": "default", "java": "26"}
        save_meta("alpha_1", meta)
        picked = []
        with mock.patch.object(procs, "choose_java", side_effect=lambda required, prefer=None: picked.append((required, prefer)) or (None, None)):
            ok, _ = procs.start_server("alpha_1")
        self.assertFalse(ok)
        self.assertEqual(picked, [(25, 26)])
