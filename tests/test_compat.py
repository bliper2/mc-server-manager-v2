"""Plugins and mods that a server cannot load, loader-aware installs, and writes that survive Windows file locking."""
import io
import json
import shutil
import threading
import zipfile
from unittest import mock

from manager import auth, compat, providers, routes_files, store
from tests.support import HOME, AppTestCase, make_server


def jar(**files):
    out = io.BytesIO()
    with zipfile.ZipFile(out, "w") as bundle:
        for name, text in files.items():
            bundle.writestr(name.replace("__", "/"), text)
    return out.getvalue()


PLUGIN = jar(**{"plugin.yml": "name: Helper\nversion: 1\nmain: x.Y\n"})
NEOFORGE = jar(**{"META-INF__neoforge.mods.toml": "modLoader='javafml'"})
FORGE = jar(**{"META-INF__mods.toml": "modLoader='javafml'"})
FABRIC = jar(**{"fabric.mod.json": "{}"})
FOLIA = jar(**{"paper-plugin.yml": "name: LagFixer\nversion: 1\n"})
LIBRARY = jar(**{"some/library.class": "x"})


class Recognising(AppTestCase):
    def setUp(self):
        super().setUp()
        shutil.rmtree(HOME / "scratch", ignore_errors=True)

    def folder(self, **jars):
        folder = HOME / "scratch"
        folder.mkdir(exist_ok=True)
        for name, data in jars.items():
            (folder / name).write_bytes(data)
        return folder

    def test_mods_in_a_plugins_folder_are_named_with_the_loader_they_are_for(self):
        folder = self.folder(**{"Chunky-NeoForge-1.5.4.jar": NEOFORGE, "grimac-fabric-2.3.jar": FABRIC, "oldmod.jar": FORGE, "Good.jar": PLUGIN})
        problems = compat.folder_problems(folder, "purpur", "plugins")
        self.assertEqual(sorted(problems), ["Chunky-NeoForge-1.5.4.jar", "grimac-fabric-2.3.jar", "oldmod.jar"])
        self.assertIn("NeoForge mod, not a plugin", problems["Chunky-NeoForge-1.5.4.jar"])
        self.assertIn("Fabric mod", problems["grimac-fabric-2.3.jar"])
        self.assertIn("Purpur", problems["oldmod.jar"])

    def test_folia_only_builds_and_broken_files_are_flagged_on_paper(self):
        folder = self.folder(**{"LagFixer-1.7.1-folia.jar": FOLIA, "LagFixer-1.7.1-bukkit.jar": jar(**{"plugin.yml": "name: LagFixer2"}), "cut-short.jar": b"PK\x03\x04junk",
                                 "empty.jar": LIBRARY})
        problems = compat.folder_problems(folder, "paper", "plugins")
        self.assertIn("Folia-only", problems["LagFixer-1.7.1-folia.jar"])
        self.assertNotIn("LagFixer-1.7.1-bukkit.jar", problems)
        self.assertIn("Not a valid .jar", problems["cut-short.jar"])
        self.assertIn("no plugin.yml", problems["empty.jar"])

    def test_a_missing_hard_dependency_is_named_but_a_soft_one_is_not(self):
        needs = jar(**{"plugin.yml": "name: Orebfuscator\ndepend: [ProtocolLib]\nsoftdepend: [Other]\n"})
        block = jar(**{"plugin.yml": "name: Blocky\ndepend:\n  - Vault\n  - 'ProtocolLib'   # needed\nversion: 2\n"})
        folder = self.folder(**{"Orebfuscator.jar": needs, "Blocky.jar": block})
        problems = compat.folder_problems(folder, "purpur", "plugins")
        self.assertIn("Needs ProtocolLib", problems["Orebfuscator.jar"])
        self.assertIn("Needs Vault, ProtocolLib", problems["Blocky.jar"])
        self.folder(**{"ProtocolLib.jar": jar(**{"plugin.yml": "name: ProtocolLib\n"}), "Vault.jar": jar(**{"plugin.yml": "name: vault\n"})})
        self.assertEqual(compat.folder_problems(folder, "purpur", "plugins"), {}, "installed (any case) satisfies it")

    def test_disabled_files_are_not_judged_and_other_server_types_are_left_alone(self):
        folder = self.folder(**{"off.jar.disabled": NEOFORGE, "NeoForge-thing.jar": NEOFORGE})
        self.assertEqual(sorted(compat.folder_problems(folder, "paper", "plugins")), ["NeoForge-thing.jar"])
        self.assertEqual(compat.folder_problems(folder, "vanilla", "plugins"), {}, "a vanilla server has no opinion")
        self.assertEqual(compat.folder_problems(folder, "paper", "mods"), {})

    def test_mods_must_match_the_servers_loader(self):
        folder = self.folder(**{"a-fabric.jar": FABRIC, "b-forge.jar": FORGE, "c-neoforge.jar": NEOFORGE, "d-lib.jar": LIBRARY, "e-plugin.jar": PLUGIN})
        fabric = compat.folder_problems(folder, "fabric", "mods")
        self.assertEqual(sorted(fabric), ["b-forge.jar", "c-neoforge.jar", "e-plugin.jar"])
        self.assertEqual(sorted(compat.folder_problems(folder, "forge", "mods")), ["a-fabric.jar", "c-neoforge.jar", "e-plugin.jar"])
        self.assertEqual(sorted(compat.folder_problems(folder, "neoforge", "mods")), ["a-fabric.jar", "e-plugin.jar"], "NeoForge still takes Forge mods")

    def test_loaders_for_each_server_type(self):
        self.assertEqual(compat.loaders_for("purpur", "plugins"), ["purpur", "paper", "spigot", "bukkit"])
        self.assertEqual(compat.loaders_for("Paper", "plugins"), ["paper", "spigot", "bukkit"])
        self.assertNotIn("folia", compat.loaders_for("purpur", "plugins"))
        self.assertEqual(compat.loaders_for("fabric", "mods"), ["fabric"])
        self.assertEqual(compat.loaders_for("neoforge", "mods"), ["neoforge"])
        self.assertEqual(compat.loaders_for("vanilla", "plugins"), [])
        self.assertEqual(compat.loaders_for(None, "mods"), [])


class Installing(AppTestCase):
    def test_the_file_list_carries_the_reason(self):
        folder = make_server(type="purpur")
        (folder / "plugins").mkdir()
        (folder / "plugins" / "Chunky-NeoForge.jar").write_bytes(NEOFORGE)
        (folder / "plugins" / "Good.jar").write_bytes(PLUGIN)
        (folder / "plugins" / "off.jar.disabled").write_bytes(NEOFORGE)
        files = {f["name"]: f for f in self.owner().get("/api/server/alpha_1/files?folder=plugins").get_json()}
        self.assertIn("NeoForge mod", files["Chunky-NeoForge.jar"]["problem"])
        self.assertEqual(files["Good.jar"]["problem"], "")
        self.assertEqual(files["off.jar.disabled"]["problem"], "")

    def test_a_wrong_loader_download_is_refused_and_nothing_is_written(self):
        folder = make_server(type="purpur")
        owner = self.owner()
        body = {"url": "https://cdn.modrinth.com/data/x/y.jar", "filename": "Chunky-NeoForge.jar", "target": "plugins"}
        with mock.patch.object(routes_files, "download_url_bytes", return_value=NEOFORGE):
            reply = owner.post("/api/server/alpha_1/install", json=body)
        self.assertEqual(reply.status_code, 400)
        self.assertIn("NeoForge mod", reply.get_json()["error"])
        self.assertFalse((folder / "plugins" / "Chunky-NeoForge.jar").exists())
        with mock.patch.object(routes_files, "download_url_bytes", return_value=PLUGIN):
            ok = owner.post("/api/server/alpha_1/install", json={**body, "filename": "Chunky.jar"})
        self.assertEqual(ok.status_code, 200, ok.get_json())
        self.assertTrue((folder / "plugins" / "Chunky.jar").exists())

    def test_the_version_lookup_filters_by_the_servers_loaders(self):
        make_server("purp_1", type="purpur", port=25601)
        make_server("fab_1", type="fabric", port=25602)
        owner = self.owner()
        with mock.patch.object(routes_files, "modrinth_versions", return_value=[]) as lookup:
            owner.get("/api/modrinth/versions/abc?server=purp_1&type=plugin&version=1.21.1")
            owner.get("/api/modrinth/versions/abc?server=fab_1&type=mod")
            owner.get("/api/modrinth/versions/abc?server=nope&loader=paper")
            owner.get("/api/modrinth/versions/abc?server=..%2F..&loader=forge")
        calls = [call.args for call in lookup.call_args_list]
        self.assertEqual(calls[0], ("abc", "1.21.1", ["purpur", "paper", "spigot", "bukkit"]))
        self.assertEqual(calls[1], ("abc", None, ["fabric"]))
        self.assertEqual(calls[2], ("abc", None, "paper"), "an unknown server falls back to the loader passed in")
        self.assertEqual(calls[3][2], "forge")

    def test_update_checks_only_offer_builds_for_the_servers_loaders(self):
        folder = make_server(type="purpur", version="1.21.1")
        (folder / "plugins").mkdir()
        (folder / "plugins" / "Chunky.jar").write_bytes(PLUGIN)
        with mock.patch.object(providers, "modrinth_version_by_hash", return_value={"project_id": "p1", "id": "v1", "version_number": "1.0"}), \
             mock.patch.object(providers, "modrinth_project_title", return_value="Chunky"), \
             mock.patch.object(providers, "modrinth_versions", return_value=[]) as lookup:
            providers.scan_plugin_updates("alpha_1")
        self.assertEqual(lookup.call_args.kwargs["loader"], ["purpur", "paper", "spigot", "bukkit"])

    def test_applying_an_update_refuses_a_wrong_loader_file_and_keeps_the_old_one(self):
        folder = make_server(type="paper")
        (folder / "plugins").mkdir()
        (folder / "plugins" / "Chunky.jar").write_bytes(PLUGIN)
        with mock.patch.object(providers, "download_url_bytes", return_value=NEOFORGE):
            ok, message = providers.apply_plugin_update("alpha_1", "plugins", "Chunky.jar", "https://cdn.modrinth.com/data/a/b.jar", "Chunky-NeoForge.jar")
        self.assertFalse(ok)
        self.assertIn("NeoForge mod", message)
        self.assertEqual((folder / "plugins" / "Chunky.jar").read_bytes(), PLUGIN)
        self.assertFalse((folder / "plugins" / "Chunky-NeoForge.jar").exists())


class SafeWrites(AppTestCase):
    def test_a_brief_access_denied_is_retried(self):
        target = HOME / "retry.json"
        real = store.os.replace
        attempts = []

        def flaky(source, destination):
            attempts.append(1)
            if len(attempts) < 3:
                raise PermissionError(5, "Access is denied")
            return real(source, destination)

        with mock.patch.object(store.os, "replace", flaky), mock.patch.object(store.time, "sleep"):
            store.write_json_atomic(target, {"a": 1})
        self.assertEqual(json.loads(target.read_text()), {"a": 1})
        self.assertEqual(len(attempts), 3)
        self.assertEqual(list(HOME.glob("retry.json.*.tmp")), [], "no temporary file is left behind")

    def test_a_permanent_failure_is_raised_and_leaves_no_temporary_file(self):
        target = HOME / "stuck.json"
        target.write_text('{"old": true}')
        with mock.patch.object(store.os, "replace", side_effect=PermissionError(5, "Access is denied")), mock.patch.object(store.time, "sleep"):
            with self.assertRaises(PermissionError):
                store.write_json_atomic(target, {"new": True})
        self.assertEqual(json.loads(target.read_text()), {"old": True}, "the old file is untouched")
        self.assertEqual(list(HOME.glob("stuck.json.*.tmp")), [])

    def test_threads_reading_and_saving_the_accounts_never_see_a_broken_file(self):
        self.owner()
        failures = []

        def hammer(index):
            try:
                for round_number in range(40):
                    accounts = auth.load_accounts()
                    assert accounts and accounts[0]["username"] == "owner", accounts
                    accounts[0]["last_seen"] = f"{index}-{round_number}"
                    auth.save_accounts(accounts)
            except Exception as exc:  # noqa: BLE001 - the test reports whatever went wrong
                failures.append(repr(exc))

        threads = [threading.Thread(target=hammer, args=(n,)) for n in range(6)]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join()
        self.assertEqual(failures, [])
        self.assertEqual(list(HOME.glob("staff.json.*.tmp")), [])

    def test_a_locked_accounts_file_does_not_log_people_out(self):
        owner = self.owner()
        real = type(auth.STAFF_FILE).read_text
        calls = []

        def locked_once(self, *args, **kwargs):
            if self.name == "staff.json" and not calls:
                calls.append(1)
                raise PermissionError(5, "Access is denied")
            return real(self, *args, **kwargs)

        with mock.patch.object(type(auth.STAFF_FILE), "read_text", locked_once), mock.patch.object(auth.time, "sleep"):
            reply = owner.get("/api/servers")
        self.assertEqual(reply.status_code, 200, "the retry hides a one-off lock; the session survives")
        self.assertEqual(calls, [1])
