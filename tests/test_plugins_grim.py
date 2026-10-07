"""Plugins and mods tab (jar details, duplicates, uploads) and GrimAC support (detection, settings, punishments, alerts)."""
import io
import json
import os
import time
import zipfile
from unittest import mock

from manager import compat, grim, jarinfo, notify, procs, routes_files, state, yamlite
from tests.support import HOME, AppTestCase, make_server

HOOK = "https://discord.com/api/webhooks/123456789012345678/AbCdEfGhIjKlMnOpQrStUvWxYz-1234567890"


def jar(**files):
    out = io.BytesIO()
    with zipfile.ZipFile(out, "w") as bundle:
        for name, text in files.items():
            bundle.writestr(name.replace("__", "/"), text)
    return out.getvalue()


def plugin_jar(name, version="1.0", extra=""):
    return jar(**{"plugin.yml": f"name: {name}\nversion: {version}\nmain: x.Y\n{extra}"})


CONFIG = """# GrimAC main configuration
alerts:
    # In addition to broadcasting alerts to players, should they also be sent to the console?
    print-to-console: true
    proxy:
        # Should alerts be sent to other servers?
        send: false
        receive: false

verbose:
    print-to-console: false

check-for-updates: true

client-brand:
    ignored-clients:
        - "^vanilla$"
        - "^fabric$"
    disconnect-blacklisted-forge-versions: true

spectators:
    hide-regardless: false

max-transaction-time: 60   # seconds

NoSlow:
    threshold: 0.001
"""
DISCORD = """enabled: false
webhook: ""
embed-title: "**Grim Alert**"  # shown on top
include-timestamp: true
"""
DATABASE = """database:
  enabled: true
  routing:
    violation: sqlite
"""
PUNISHMENTS = """Punishments:
  Simulation:
    # After how many seconds should a violation be removed?
    remove-violations-after: 300
    checks:
      - "Simulation"
      - "Timer"
    commands:
      - "100:40 [alert]"
      - "1:1 [log]"
  Reach:
    remove-violations-after: 120
    checks:
      - "Reach"
    commands:
      - "1:1 [alert]"
      - "10:0 kick %player% reach"
  Empty:
    remove-violations-after: 60
"""


class YamlLite(AppTestCase):
    def test_values_are_read_and_replaced_without_disturbing_the_file(self):
        self.assertIs(yamlite.get_value(CONFIG, ["alerts", "print-to-console"]), True)
        self.assertIs(yamlite.get_value(CONFIG, ["alerts", "proxy", "send"]), False)
        self.assertEqual(yamlite.get_value(CONFIG, ["max-transaction-time"]), 60)
        self.assertEqual(yamlite.get_value(CONFIG, ["NoSlow", "threshold"]), 0.001)
        changed = yamlite.set_value(CONFIG, ["alerts", "print-to-console"], False)
        self.assertIn("    print-to-console: false\n", changed)
        self.assertEqual(changed.replace("    print-to-console: false\n", "    print-to-console: true\n", 1), CONFIG, "nothing else moved")
        both = yamlite.set_value(yamlite.set_value(CONFIG, ["max-transaction-time"], 90), ["verbose", "print-to-console"], True)
        self.assertIn("max-transaction-time: 90   # seconds\n", both, "the comment and its spacing stay")
        self.assertIn("print-to-console: true\n\nchec", both.replace("check-for-updates", "chec"))

    def test_strings_are_quoted_and_comments_inside_quotes_are_not_comments(self):
        text = 'name: "a # b"  # real comment\nother: plain\n'
        self.assertEqual(yamlite.get_value(text, ["name"]), "a # b")
        self.assertEqual(yamlite.set_value(text, ["name"], 'say "hi"'), 'name: "say \\"hi\\""  # real comment\nother: plain\n')
        self.assertEqual(yamlite.get_value('k: \'it\'\'s\'\n', ["k"]), "it's")

    def test_missing_keys_nested_maps_and_bad_values_are_refused(self):
        with self.assertRaises(KeyError):
            yamlite.set_value(CONFIG, ["alerts", "nope"], True)
        with self.assertRaises(KeyError):
            yamlite.get_value(CONFIG, ["proxy", "send"])
        with self.assertRaises(ValueError):
            yamlite.set_value(CONFIG, ["alerts"], True)
        with self.assertRaises(ValueError):
            yamlite.set_value(CONFIG, ["max-transaction-time"], "two\nlines")
        with self.assertRaises(KeyError):
            yamlite.get_value(CONFIG, ["client-brand", "ignored-clients", "^vanilla$"])  # list entries are not keys

    def test_windows_line_endings_survive(self):
        text = "a:\r\n  b: 1\r\n  c: 2\r\n"
        self.assertEqual(yamlite.set_value(text, ["a", "c"], 3), "a:\r\n  b: 1\r\n  c: 3\r\n")

    def test_plugin_yml_metadata(self):
        text = 'name: GrimAC\nversion: 2.3.74\ndescription: "Libre simulation anticheat for 26.3 with 1.8 support,\\\n  \\ powered by PacketEvents."\nauthor: GrimAC\nwebsite: https://grim.ac/\n'
        top = yamlite.top_level(text)
        self.assertEqual((top["name"], top["version"], top["website"]), ("GrimAC", "2.3.74", "https://grim.ac/"))
        self.assertEqual(top["description"], "Libre simulation anticheat for 26.3 with 1.8 support, powered by PacketEvents.")
        self.assertEqual(yamlite.top_level("d: >\n  folded\n  text\nx: 1\n"), {"d": "folded text", "x": "1"})
        self.assertEqual(yamlite.top_level("b: \"x\n  y\"\nc: z\n"), {"b": "x y", "c": "z"})
        self.assertEqual(yamlite.block_list("authors: [a, 'b c']\n", "authors"), ["a", "b c"])
        self.assertEqual(yamlite.block_list("authors:\n  - Bob\n  - 'Al'  # x\nnext: 1\n", "authors"), ["Bob", "Al"])
        self.assertEqual(yamlite.block_list("authors:\n- Bob\n- Al\n", "authors"), ["Bob", "Al"])
        self.assertEqual(yamlite.block_list("author: Solo\n", "authors"), [])


class JarDetails(AppTestCase):
    def info(self, name, data):
        path = HOME / name
        path.write_bytes(data)
        return jarinfo.plugin_info(path)

    def test_plugins_report_name_version_description_and_authors(self):
        data = plugin_jar("Chunky", "1.5.4", "description: Pre-generates chunks\nauthors: [pop4959, Foo]\napi-version: '1.20'\nwebsite: https://x.example\n")
        info = self.info("Chunky-1.5.4.jar", data)
        self.assertEqual((info["name"], info["version"], info["description"], info["authors"], info["api"]), ("Chunky", "1.5.4", "Pre-generates chunks", ["pop4959", "Foo"], "1.20"))

    def test_mods_for_every_loader(self):
        fabric = jar(**{"fabric.mod.json": json.dumps({"id": "voicechat", "name": "Simple Voice Chat", "version": "2.6", "description": "Talk", "authors": ["Max", {"name": "Co"}], "contact": {"homepage": "https://v.example"}})})
        info = self.info("vc.jar", fabric)
        self.assertEqual((info["name"], info["version"], info["authors"], info["website"]), ("Simple Voice Chat", "2.6", ["Max", "Co"], "https://v.example"))
        toml = "modLoader=\"javafml\"\n[[mods]]\nmodId=\"emote\"\nversion=\"${file.jarVersion}\"\ndisplayName=\"Emotecraft\"\nauthors=\"KosmX, dima\"\ndescription='''Play\nemotes'''\n[[dependencies.emote]]\nmodId=\"neoforge\"\n"
        info = self.info("emote.jar", jar(**{"META-INF__neoforge.mods.toml": toml, "META-INF__MANIFEST.MF": "Implementation-Version: 3.5.0\n"}))
        self.assertEqual((info["name"], info["version"], info["authors"], info["description"]), ("Emotecraft", "3.5.0", ["KosmX", "dima"], "Play emotes"))

    def test_odd_and_broken_files_still_get_a_name_and_version_guess(self):
        info = self.info("Cool-Plugin-2.4.1-SNAPSHOT.jar", b"not a zip")
        self.assertEqual((info["name"], info["version"]), ("Cool-Plugin", "2.4.1-SNAPSHOT"))
        info = self.info("noversion.jar.disabled", jar(**{"x.class": "y"}))
        self.assertEqual((info["name"], info["version"]), ("noversion", ""))

    def test_long_descriptions_are_cut(self):
        info = self.info("long.jar", plugin_jar("Long", "1", "description: " + "word " * 200 + "\n"))
        self.assertLessEqual(len(info["description"]), 240)


class PluginsTab(AppTestCase):
    def folder(self, **jars):
        server = make_server(type="purpur")
        plugins = server / "plugins"
        plugins.mkdir(exist_ok=True)
        for name, data in jars.items():
            (plugins / name).write_bytes(data)
        return plugins

    def test_the_file_list_carries_details_and_config_folders(self):
        plugins = self.folder(**{"TAB_v6.2.0.jar": plugin_jar("TAB", "6.2.0", "authors: [NEZNAMY]\n"), "off.jar.disabled": plugin_jar("Quiet", "0.1")})
        (plugins / "TAB").mkdir()
        files = {f["name"]: f for f in self.owner().get("/api/server/alpha_1/files?folder=plugins").get_json()}
        self.assertEqual(files["TAB_v6.2.0.jar"]["info"]["authors"], ["NEZNAMY"])
        self.assertTrue(files["TAB_v6.2.0.jar"]["has_config"])
        self.assertFalse(files["off.jar.disabled"]["has_config"])
        self.assertEqual(files["off.jar.disabled"]["info"]["name"], "Quiet")

    def test_two_jars_of_one_plugin_are_flagged_on_the_older_one(self):
        plugins = self.folder(**{"Vault-1.7.jar": plugin_jar("Vault", "1.7"), "Vault-1.8.jar": plugin_jar("Vault", "1.8"), "Other.jar": plugin_jar("Other")})
        os.utime(plugins / "Vault-1.7.jar", (1_000_000_000, 1_000_000_000))
        problems = compat.folder_problems(plugins, "purpur", "plugins")
        self.assertEqual(list(problems), ["Vault-1.7.jar"])
        self.assertIn("Vault-1.8.jar", problems["Vault-1.7.jar"])
        (plugins / "Vault-1.7.jar").rename(plugins / "Vault-1.7.jar.disabled")
        self.assertEqual(compat.folder_problems(plugins, "purpur", "plugins"), {}, "once the old one is disabled the problem is gone")

    def test_uploads_are_checked_before_they_are_written(self):
        plugins = self.folder()
        owner = self.owner()
        files = [(io.BytesIO(plugin_jar("Good", "1")), "Good-1.jar"), (io.BytesIO(jar(**{"META-INF__neoforge.mods.toml": "x"})), "Mod-NeoForge.jar"),
                 (io.BytesIO(b"hello"), "notes.txt"), (io.BytesIO(b"PK\x03\x04junk"), "cut-short.jar")]
        reply = owner.post("/api/server/alpha_1/plugins/upload", data={"folder": "plugins", "files": files}, content_type="multipart/form-data")
        body = reply.get_json()
        self.assertEqual(reply.status_code, 200, body)
        self.assertEqual(body["added"], ["Good-1.jar"])
        reasons = {s["name"]: s["reason"] for s in body["skipped"]}
        self.assertIn("NeoForge mod", reasons["Mod-NeoForge.jar"])
        self.assertIn("Only .jar", reasons["notes.txt"])
        self.assertIn("Not a valid .jar", reasons["cut-short.jar"])
        self.assertEqual(sorted(p.name for p in plugins.iterdir()), ["Good-1.jar"])

    def test_upload_limits_and_permissions(self):
        self.folder()
        owner = self.owner()
        viewer = self.staff(owner, "viewer", "viewpass12", permissions=["control"])
        editor = self.staff(owner, "editor", "editpass12", permissions=["files"])
        post = lambda client, **kw: client.post("/api/server/alpha_1/plugins/upload", data={"folder": kw.get("folder", "plugins"), "files": [(io.BytesIO(plugin_jar("X")), "x.jar")]}, content_type="multipart/form-data")
        self.assertEqual(post(viewer).status_code, 403)
        self.assertEqual(post(editor).status_code, 200)
        self.assertEqual(post(owner, folder="../../etc").status_code, 400)
        self.assertEqual(owner.post("/api/server/alpha_1/plugins/upload", data={"folder": "plugins"}, content_type="multipart/form-data").status_code, 400)
        self.assertEqual(owner.post("/api/server/nope/plugins/upload", data={"folder": "plugins"}, content_type="multipart/form-data").status_code, 404)
        with mock.patch.object(routes_files, "MAX_JAR_BYTES", 50):
            big = owner.post("/api/server/alpha_1/plugins/upload", data={"folder": "plugins", "files": [(io.BytesIO(plugin_jar("Big")), "big.jar")]}, content_type="multipart/form-data")
        self.assertEqual(big.status_code, 400)
        self.assertIn("256 MB", big.get_json()["error"])

    def test_a_fabric_server_takes_fabric_mods_in_its_mods_folder(self):
        server = make_server("fab_1", name="Fab", type="fabric", port=25610)
        owner = self.owner()
        good = (io.BytesIO(jar(**{"fabric.mod.json": "{}"})), "ok.jar")
        bad = (io.BytesIO(plugin_jar("Plug")), "plugin.jar")
        body = owner.post("/api/server/fab_1/plugins/upload", data={"folder": "mods", "files": [good, bad]}, content_type="multipart/form-data").get_json()
        self.assertEqual(body["added"], ["ok.jar"])
        self.assertIn("Bukkit plugin", body["skipped"][0]["reason"])
        self.assertTrue((server / "mods" / "ok.jar").exists())


class GrimDetection(AppTestCase):
    def server(self, server_type="purpur", **jars):
        folder = make_server(type=server_type)
        (folder / "plugins").mkdir(exist_ok=True)
        for name, data in jars.items():
            (folder / "plugins" / name).write_bytes(data)
        return folder

    def test_not_installed(self):
        self.server()
        status = grim.find_grim("alpha_1")
        self.assertEqual((status["installed"], status["supported"], status["file"]), (False, True, ""))

    def test_installed_with_its_version_and_files(self):
        folder = self.server(**{"grimac-bukkit-2.3.74.jar": plugin_jar("GrimAC", "2.3.74-abb95b6")})
        (folder / "plugins" / "GrimAC").mkdir()
        (folder / "plugins" / "GrimAC" / "config.yml").write_text(CONFIG, encoding="utf-8")
        status = grim.find_grim("alpha_1")
        self.assertEqual((status["installed"], status["enabled"], status["version"], status["file"]), (True, True, "2.3.74-abb95b6", "grimac-bukkit-2.3.74.jar"))
        self.assertEqual((status["folder"], status["files"]["config.yml"], status["files"]["discord.yml"]), (True, True, False))

    def test_a_disabled_jar_is_installed_but_not_enabled(self):
        self.server(**{"grimac.jar.disabled": plugin_jar("GrimAC", "2.3")})
        status = grim.find_grim("alpha_1")
        self.assertEqual((status["installed"], status["enabled"]), (True, False))

    def test_other_loaders_builds_and_other_anticheats_are_noted(self):
        self.server(**{"grimac-fabric-2.3.jar": jar(**{"fabric.mod.json": "{}"}), "Vulcan.jar": plugin_jar("Vulcan", "2.9"), "grimac.jar": plugin_jar("GrimAC", "2.3")})
        status = grim.find_grim("alpha_1")
        self.assertEqual(status["wrong_builds"], ["grimac-fabric-2.3.jar"])
        self.assertEqual(status["others"], ["Vulcan"])
        self.assertTrue(status["installed"])

    def test_servers_that_cannot_run_plugins_say_so(self):
        self.server("fabric")
        self.assertFalse(grim.find_grim("alpha_1")["supported"])


class GrimSettings(AppTestCase):
    def setUp(self):
        super().setUp()
        folder = make_server(type="purpur")
        self.dir = folder / "plugins" / "GrimAC"
        self.dir.mkdir(parents=True)
        for name, text in (("config.yml", CONFIG), ("discord.yml", DISCORD), ("database.yml", DATABASE), ("punishments.yml", PUNISHMENTS)):
            (self.dir / name).write_text(text, encoding="utf-8")

    def test_only_settings_present_in_the_files_are_offered_and_secrets_are_masked(self):
        (self.dir / "database.yml").unlink()
        by_id = {s["id"]: s for s in grim.read_settings("alpha_1")}
        self.assertNotIn("history_enabled", by_id)
        self.assertIs(by_id["alerts_console"]["value"], True)
        self.assertEqual(by_id["transaction_timeout"]["value"], 60)
        self.assertEqual((by_id["discord_enabled"]["value"], by_id["discord_title"]["value"]), (False, "**Grim Alert**"))
        self.assertEqual((by_id["discord_webhook"]["value"], by_id["discord_webhook"]["set"]), ("", False))
        (self.dir / "discord.yml").write_text(DISCORD.replace('webhook: ""', f'webhook: "{HOOK}"'), encoding="utf-8")
        secret = {s["id"]: s for s in grim.read_settings("alpha_1")}["discord_webhook"]
        self.assertTrue(secret["set"])
        self.assertEqual(secret["value"], "")
        self.assertNotIn("discord.com", json.dumps(secret))

    def test_changes_are_written_with_a_backup_and_nothing_else_moves(self):
        changed, error = grim.apply_settings("alpha_1", {"alerts_console": False, "transaction_timeout": 120, "discord_enabled": True, "discord_title": "Alerts"})
        self.assertIsNone(error)
        self.assertEqual(sorted(changed), ["alerts_console", "discord_enabled", "discord_title", "transaction_timeout"])
        config = (self.dir / "config.yml").read_text(encoding="utf-8")
        self.assertIn("    print-to-console: false\n", config)
        self.assertIn("max-transaction-time: 120   # seconds", config)
        self.assertEqual((self.dir / "config.yml.manager-backup").read_text(encoding="utf-8"), CONFIG)
        self.assertIn('embed-title: "Alerts"  # shown on top', (self.dir / "discord.yml").read_text(encoding="utf-8"))
        self.assertIn("enabled: true", (self.dir / "discord.yml").read_text(encoding="utf-8"))

    def test_bad_values_change_nothing(self):
        before = (self.dir / "config.yml").read_text(encoding="utf-8")
        for values in ({"transaction_timeout": 2}, {"transaction_timeout": "abc"}, {"alerts_console": "maybe"}, {"nope": 1}, {"discord_webhook": "https://evil.example/hook"},
                       {"discord_title": "two\nlines"}):
            changed, error = grim.apply_settings("alpha_1", {"alerts_console": False, **values})
            self.assertTrue(error, values)
            self.assertEqual(changed, [])
        self.assertEqual((self.dir / "config.yml").read_text(encoding="utf-8"), before, "one bad value stops the whole save")

    def test_the_webhook_is_validated_and_kept_when_not_sent(self):
        changed, error = grim.apply_settings("alpha_1", {"discord_webhook": HOOK})
        self.assertIsNone(error)
        self.assertIn(HOOK, (self.dir / "discord.yml").read_text(encoding="utf-8"))
        changed, error = grim.apply_settings("alpha_1", {"discord_webhook": None, "discord_enabled": True})
        self.assertIn(HOOK, (self.dir / "discord.yml").read_text(encoding="utf-8"), "a saved secret is left alone")
        grim.apply_settings("alpha_1", {"discord_webhook": ""})
        self.assertNotIn(HOOK, (self.dir / "discord.yml").read_text(encoding="utf-8"))

    def test_missing_files_and_keys_are_explained(self):
        (self.dir / "discord.yml").unlink()
        self.assertIn("does not exist yet", grim.apply_settings("alpha_1", {"discord_enabled": True})[1])
        (self.dir / "config.yml").write_text("alerts:\n  other: 1\n", encoding="utf-8")
        self.assertIn("not in this Grim version", grim.apply_settings("alpha_1", {"alerts_console": True})[1])

    def test_the_api_reads_and_writes_with_the_files_permission(self):
        owner = self.owner()
        viewer = self.staff(owner, "viewer", "viewpass12", permissions=["control"])
        editor = self.staff(owner, "editor", "editpass12", permissions=["files"])
        body = owner.get("/api/server/alpha_1/grim").get_json()
        self.assertTrue(body["ok"])
        self.assertEqual({g["name"] for g in body["punishments"]}, {"Simulation", "Reach", "Empty"})
        self.assertTrue(any(c["command"] == "grim reload" for c in body["commands"]))
        self.assertEqual(viewer.post("/api/server/alpha_1/grim/settings", json={"values": {"alerts_console": False}}).status_code, 403)
        ok = editor.post("/api/server/alpha_1/grim/settings", json={"values": {"alerts_console": False}})
        self.assertEqual(ok.status_code, 200, ok.get_json())
        self.assertIs({s["id"]: s for s in ok.get_json()["settings"]}["alerts_console"]["value"], False)
        self.assertEqual(editor.post("/api/server/alpha_1/grim/settings", json={"values": {"transaction_timeout": 1}}).status_code, 400)
        self.assertEqual(editor.post("/api/server/alpha_1/grim/settings", json={"values": {}}).status_code, 400)
        self.assertEqual(owner.get("/api/server/nope/grim").status_code, 404)

    def test_punishment_groups_are_summarised(self):
        groups = {g["name"]: g for g in grim.parse_punishments(PUNISHMENTS)}
        self.assertEqual(groups["Simulation"]["checks"], ["Simulation", "Timer"])
        self.assertEqual(groups["Simulation"]["remove_after"], 300)
        self.assertEqual(groups["Simulation"]["rules"][0], {"threshold": 100, "interval": 40, "action": "[alert]"})
        self.assertEqual(groups["Reach"]["rules"][1], {"threshold": 10, "interval": 0, "action": "kick %player% reach"})
        self.assertEqual((groups["Empty"]["checks"], groups["Empty"]["rules"]), ([], []))
        self.assertEqual(grim.parse_punishments("nothing: here\n"), [])


class GrimAlerts(AppTestCase):
    def setUp(self):
        super().setUp()
        self.folder = make_server(type="purpur")
        grim._last_relay.clear()
        grim._recent_relays.clear()

    def relay(self, on=True, threshold=5):
        (self.folder / "anti_cheat.json").write_text(json.dumps({"discord_relay": on, "threshold": threshold}), encoding="utf-8")

    def test_alert_lines_in_every_form(self):
        cases = {
            "[22:30:12 INFO]: Grim » Steve failed Reach (x3) distance=3.2": ("Steve", "Reach", 3, "distance=3.2", False),
            "[22:30:12] [Server thread/INFO]: Grim » Alex_99 failed Simulation (x12) offset=0.31": ("Alex_99", "Simulation", 12, "offset=0.31", False),
            "Grim » Steve failed Hitboxes* (x1)": ("Steve", "Hitboxes", 1, "", True),
            "\x1b[96mGrim\x1b[0m » Bob failed BadPacketsA (x40) ": ("Bob", "BadPacketsA", 40, "", False),
            "§bGrim §8» §fKai §bfailed §fNoSlow §f(x§c2§f) §7threshold": ("Kai", "NoSlow", 2, "threshold", False),
        }
        for line, (player, check, vl, info, exp) in cases.items():
            alert = grim.parse_alert(line)
            self.assertEqual((alert["player"], alert["check"], alert["vl"], alert["info"], alert["exp"]), (player, check, vl, info, exp), line)
        for line in ("Steve failed to join the game", "Grim » no violations", "<Steve> I failed (x2) my test", "Steve failed (x2)"):
            self.assertIsNone(grim.parse_alert(line), line)

    def test_the_feed_keeps_alerts_and_summarises_them(self):
        for line in ("Grim » Steve failed Reach (x1)", "Grim » Steve failed Reach (x4)", "Grim » Alex failed Timer (x2)", "random console noise"):
            grim.watch_line("alpha_1", line)
        owner = self.owner()
        body = owner.get("/api/server/alpha_1/grim/alerts").get_json()
        self.assertEqual([a["player"] for a in body["alerts"]], ["Alex", "Steve", "Steve"], "newest first")
        self.assertEqual(body["total"], 3)
        self.assertEqual(body["players"][0], {"name": "Steve", "alerts": 2, "worst": 4})
        self.assertEqual(body["checks"][0], {"name": "Reach", "alerts": 2})
        only = owner.get("/api/server/alpha_1/grim/alerts?player=alex&limit=5").get_json()
        self.assertEqual([a["check"] for a in only["alerts"]], ["Timer"])
        self.assertEqual(owner.get("/api/server/nope/grim/alerts").status_code, 404)

    def test_the_feed_is_refilled_from_latest_log_after_a_restart(self):
        (self.folder / "logs").mkdir()
        (self.folder / "logs" / "latest.log").write_text("[22:30:12] [Server thread/INFO]: Grim » Steve failed Reach (x3) a=1\n[22:31:00] [Server thread/INFO]: unrelated line\n"
                                                         "[22:32:10] [Server thread/INFO]: Grim » Alex failed Timer (x7)\n", encoding="utf-8")
        body = self.owner().get("/api/server/alpha_1/grim/alerts").get_json()
        self.assertEqual([(a["player"], a["vl"]) for a in body["alerts"]], [("Alex", 7), ("Steve", 3)])
        self.assertTrue(body["alerts"][0]["time"].endswith("22:32:10"))

    def test_serious_alerts_reach_discord_once_per_cooldown(self):
        self.relay(threshold=5)
        with mock.patch.object(grim, "notify") as send:
            grim.watch_line("alpha_1", "Grim » Steve failed Reach (x4)")
            self.assertFalse(send.called, "below the threshold")
            grim.watch_line("alpha_1", "Grim » Steve failed Reach (x5)")
            grim.watch_line("alpha_1", "Grim » Steve failed Reach (x9)")
            self.assertEqual(send.call_count, 1, "the same player and check wait out the cooldown")
            grim.watch_line("alpha_1", "Grim » Steve failed Timer (x6)")
            grim.watch_line("alpha_1", "Grim » Alex failed Reach (x6)")
            self.assertEqual(send.call_count, 3)
            event, text, sid, fields = send.call_args_list[0].args
            self.assertEqual((event, sid), ("anticheat_alert", "alpha_1"))
            self.assertIn("Steve", text)
            self.assertIn(("Violations", 5), fields)

    def test_nothing_is_sent_when_the_relay_is_off_or_floods(self):
        with mock.patch.object(grim, "notify") as send:
            grim.watch_line("alpha_1", "Grim » Steve failed Reach (x50)")
            self.assertFalse(send.called, "off by default")
            self.relay(threshold=1)
            for number in range(40):
                grim.watch_line("alpha_1", f"Grim » P{number:02d} failed Reach (x3)")
            self.assertEqual(send.call_count, grim.RELAY_PER_MINUTE, "a burst of flags cannot flood the channel")

    def test_the_event_exists_and_the_console_reader_feeds_the_watcher(self):
        self.assertIn("anticheat_alert", notify.EVENTS)
        self.assertIn("anticheat_alert", notify.EVENT_LABELS)
        process = mock.Mock()
        process.stdout.readline.side_effect = [b"Grim \xc2\xbb Steve failed Reach (x2)\n", b""]
        with mock.patch.object(procs, "update_active_players"), mock.patch.object(procs, "finish_process", create=True):
            try:
                procs.read_console("alpha_1", process)
            except Exception:  # noqa: BLE001 - the exit handling after the last line is not under test here
                pass
        self.assertEqual([a["player"] for a in state.grim_alerts["alpha_1"]], ["Steve"])

    def test_the_relay_switch_is_saved_with_the_old_profile(self):
        owner = self.owner()
        reply = owner.post("/api/server/alpha_1/anticheat", json={"threshold": 8, "discord_relay": True, "alerts": True})
        self.assertEqual(reply.get_json()["config"]["discord_relay"], True)
        self.assertEqual(grim.relay_config("alpha_1"), {"discord_relay": True, "threshold": 8})
        self.assertTrue(owner.get("/api/server/alpha_1/anticheat").get_json()["config"]["discord_relay"])


class ServerDecidesTheKind(AppTestCase):
    """A Purpur server must never be offered or sent a NeoForge build, whatever the type picker says."""

    def setUp(self):
        super().setUp()
        make_server("purp_1", type="purpur", port=25601)
        make_server("neo_1", type="neoforge", port=25602)
        make_server("van_1", type="vanilla", port=25603)
        self.owner_client = self.owner()

    def test_a_plugin_server_asking_for_mods_still_gets_plugin_builds(self):
        with mock.patch.object(routes_files, "modrinth_versions", return_value=[]) as lookup:
            self.owner_client.get("/api/modrinth/versions/abc?server=purp_1&type=mod")
            self.owner_client.get("/api/modrinth/versions/abc?server=neo_1&type=plugin")
            self.assertEqual(self.owner_client.get("/api/modrinth/versions/abc?server=van_1&type=plugin").get_json(), [])
        self.assertEqual(lookup.call_args_list[0].args[2], ["purpur", "paper", "spigot", "bukkit"])
        self.assertEqual(lookup.call_args_list[1].args[2], ["neoforge"])
        self.assertEqual(lookup.call_count, 2, "vanilla does not even ask Modrinth")

    def test_searches_follow_the_target_server(self):
        with mock.patch.object(routes_files, "modrinth_search", return_value={"hits": []}) as search:
            self.owner_client.get("/api/modrinth/search?q=map&type=mod&server=purp_1")
            self.owner_client.get("/api/modrinth/featured?type=plugin&server=neo_1")
            self.owner_client.get("/api/modrinth/search?q=map&type=mod")
        self.assertEqual(search.call_args_list[0].args[1], "plugin")
        self.assertEqual((search.call_args_list[1].args[1], search.call_args_list[1].kwargs["loader"]), ("mod", "neoforge"))
        self.assertEqual((search.call_args_list[2].args[1], search.call_args_list[2].kwargs["loader"]), ("mod", None), "no target, no change")

    def test_installs_go_only_to_the_folder_the_server_loads(self):
        body = {"url": "https://cdn.modrinth.com/data/x/y.jar", "filename": "x.jar"}
        with mock.patch.object(routes_files, "download_url_bytes", return_value=plugin_jar("X")) as fetch:
            wrong = self.owner_client.post("/api/server/purp_1/install", json={**body, "target": "mods"})
            vanilla = self.owner_client.post("/api/server/van_1/install", json={**body, "target": "plugins"})
        self.assertEqual((wrong.status_code, vanilla.status_code), (400, 400))
        self.assertIn("loads plugins", wrong.get_json()["error"])
        self.assertIn("cannot load plugins or mods", vanilla.get_json()["error"])
        self.assertFalse(fetch.called, "refused before anything is downloaded")
