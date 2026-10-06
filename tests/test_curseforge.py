import hashlib
import io
import json
import threading
import time
import zipfile
from types import SimpleNamespace
from unittest import mock

from manager import curseforge, loaders, modpacks, packtools, state, store
from tests.support import HOME, AppTestCase, make_server

KEY = "$2a$10$abcdefghijklmnopqrstuvwxyz0123456789ABCDEFGH"
PACK_URL = "https://edge.forgecdn.net/files/1/10/Test-1.0.zip"
SERVER_PACK_URL = "https://mediafilez.forgecdn.net/files/1/11/Test-Server-1.0.zip"
MOD_1 = b"mod-one"
MOD_2 = b"mod-two"


def sha1(data):
    return hashlib.sha1(data).hexdigest()


def manifest(files=None, loader="forge-47.1.0", minecraft="1.20.1"):
    return {"manifestType": "minecraftModpack", "manifestVersion": 1, "name": "Test Pack", "version": "1.0", "overrides": "overrides",
            "minecraft": {"version": minecraft, "modLoaders": [{"id": loader, "primary": True}]},
            "files": files if files is not None else [{"projectID": 100, "fileID": 201, "required": True}, {"projectID": 101, "fileID": 202, "required": True},
                                                       {"projectID": 102, "fileID": 203, "required": False}]}


def pack_zip(man=None):
    out = io.BytesIO()
    with zipfile.ZipFile(out, "w") as bundle:
        bundle.writestr("manifest.json", json.dumps(man or manifest()))
        bundle.writestr("overrides/config/pack.toml", "client config")
        bundle.writestr("overrides/../escape.txt", "escape")
    return out.getvalue()


def server_pack_zip(wrapper="ServerFiles-1.0/"):
    out = io.BytesIO()
    with zipfile.ZipFile(out, "w") as bundle:
        bundle.writestr(wrapper + "mods/server-mod.jar", MOD_1)
        bundle.writestr(wrapper + "config/server.toml", "server config")
        bundle.writestr(wrapper + "start.bat", "echo hi")
        bundle.writestr(wrapper + "forge-installer.jar", "x")
        bundle.writestr(wrapper + "user_jvm_args.txt", "-Xmx4G")
        bundle.writestr(wrapper + "server.properties", "evil=true")
        bundle.writestr(wrapper + "libraries/big.jar", "skip")
    return out.getvalue()


def file_record(file_id, name, url, data=None, **extra):
    record = {"id": file_id, "modId": 1, "displayName": name, "fileName": name, "downloadUrl": url, "isAvailable": True,
              "hashes": [{"value": sha1(data), "algo": 1}] if data is not None else [], "gameVersions": ["1.20.1", "Forge"], "releaseType": 1}
    record.update(extra)
    return record


class FakeCurseForge:
    """Stands in for both the CurseForge API (cf_request) and the downloads (packtools._stream)."""

    def __init__(self, pack=None, server_pack=None, mods=None, main_extra=None):
        self.pack = pack or pack_zip()
        self.server_pack = server_pack
        self.mods = mods if mods is not None else {
            201: file_record(201, "one.jar", "https://edge.forgecdn.net/files/2/201/one.jar", MOD_1),
            202: file_record(202, "two.jar", "https://edge.forgecdn.net/files/2/202/two.jar", MOD_2),
            203: file_record(203, "optional.jar", "https://edge.forgecdn.net/files/2/203/optional.jar", b"opt"),
        }
        self.main = file_record(10, "Test-1.0.zip", PACK_URL, self.pack, **(main_extra or {}))
        if server_pack:
            self.main["serverPackFileId"] = 11
        self.urls = {PACK_URL: self.pack}
        if server_pack:
            self.urls[SERVER_PACK_URL] = server_pack
        for info in self.mods.values():
            if info.get("downloadUrl"):
                self.urls[info["downloadUrl"]] = {201: MOD_1, 202: MOD_2, 203: b"opt"}.get(info["id"], b"other")
        self.calls = []

    def cf(self, method, path, params=None, body=None, key=None):
        self.calls.append((method, path))
        if path == "/mods/1/files/10":
            return {"data": self.main}
        if path == "/mods/1/files/11":
            return {"data": file_record(11, "Test-Server-1.0.zip", SERVER_PACK_URL, self.server_pack, isServerPack=True)}
        if path == "/mods/files":
            return {"data": [self.mods[i] for i in body["fileIds"] if i in self.mods]}
        raise AssertionError(f"unexpected CurseForge call {method} {path}")

    def stream(self, url, limit, headers=None):
        if url in self.urls:
            yield self.urls[url]
        else:
            raise packtools.requests.ConnectionError(url)

    def patches(self):
        return [mock.patch.object(curseforge, "cf_request", self.cf), mock.patch.object(packtools, "_stream", self.stream),
                mock.patch.object(curseforge, "install_loader", lambda kind, mc, ver, folder, step: {"jar": loaders.LAUNCHER}),
                mock.patch.object(curseforge, "api_key", lambda: KEY)]


def run_pack(fake, **override):
    state.modpack_jobs["cf"] = {"state": "running", "message": "", "progress": 0, "server_id": None, "updated": time.time()}
    modpacks._install_lock.acquire()
    patches = fake.patches()
    for p in patches:
        p.start()
    try:
        curseforge.install_curseforge("cf", 1, 10, "CF Pack", 6144, 25570, "Friendly")
    finally:
        for p in patches:
            p.stop()
    return state.modpack_jobs["cf"]


class ManifestParsing(AppTestCase):
    def test_loader_from_the_primary_entry(self):
        self.assertEqual(curseforge.manifest_loader(manifest()), ("1.20.1", "forge", "47.1.0"))
        self.assertEqual(curseforge.manifest_loader(manifest(loader="neoforge-21.1.172", minecraft="1.21.1")), ("1.21.1", "neoforge", "21.1.172"))
        self.assertEqual(curseforge.manifest_loader(manifest(loader="fabric-0.15.7"))[1:], ("fabric", "0.15.7"))
        broken = manifest()
        broken["minecraft"]["modLoaders"] = []
        with self.assertRaises(packtools.ModpackError):
            curseforge.manifest_loader(broken)

    def test_manifest_validation(self):
        bundle = zipfile.ZipFile(io.BytesIO(pack_zip()))
        self.assertEqual(curseforge.parse_manifest(bundle)["name"], "Test Pack")
        empty = zipfile.ZipFile(io.BytesIO(b"PK\x05\x06" + b"\x00" * 18))
        with self.assertRaisesRegex(packtools.ModpackError, "manifest.json"):
            curseforge.parse_manifest(empty)
        many = zipfile.ZipFile(io.BytesIO(pack_zip(manifest(files=[{"projectID": 1, "fileID": i} for i in range(packtools.MAX_FILES + 1)]))))
        with self.assertRaisesRegex(packtools.ModpackError, "more than"):
            curseforge.parse_manifest(many)

    def test_file_classification(self):
        forge = curseforge.classify({"id": 1, "displayName": "A", "gameVersions": ["1.20.1", "Forge", "Java 17"], "releaseType": 1})
        self.assertEqual((forge["minecraft"], forge["loader"], forge["supported"]), ("1.20.1", "forge", True))
        neo = curseforge.classify({"id": 2, "displayName": "B", "gameVersions": ["1.21.1", "NeoForge"], "releaseType": 2})
        self.assertEqual((neo["loader"], neo["release"]), ("neoforge", "beta"))
        quilt = curseforge.classify({"id": 3, "displayName": "C", "gameVersions": ["1.20.1", "Quilt"], "serverPackFileId": 9})
        self.assertEqual((quilt["supported"], quilt["server_pack"]), (False, True))
        self.assertEqual(curseforge.classify({"id": 4, "displayName": "D", "gameVersions": []})["loader"], "")

    def test_pack_files_hide_server_pack_entries(self):
        data = {"data": [{"id": 1, "displayName": "main", "gameVersions": ["1.20.1", "Forge"]},
                         {"id": 2, "displayName": "server", "isServerPack": True, "gameVersions": ["1.20.1"]},
                         {"id": 3, "displayName": "gone", "isAvailable": False, "gameVersions": []}]}
        with mock.patch.object(curseforge, "cf_request", return_value=data):
            self.assertEqual([f["id"] for f in curseforge.pack_files(1)], [1])


class ResolvingMods(AppTestCase):
    def resolve(self, mods, files):
        fake = FakeCurseForge(mods=mods)
        with mock.patch.object(curseforge, "cf_request", fake.cf):
            return curseforge.resolve_manifest_files(manifest(files=files))

    def test_plan_includes_required_jars_with_checksums(self):
        entries = self.resolve(FakeCurseForge().mods, manifest()["files"])
        self.assertEqual([e[0] for e in entries], [["mods", "one.jar"], ["mods", "two.jar"]], "optional mods are left out")
        self.assertEqual(entries[0][2], sha1(MOD_1))

    def test_blocked_mods_are_named_and_nothing_is_planned(self):
        mods = FakeCurseForge().mods
        mods[201]["downloadUrl"] = None
        with mock.patch.object(curseforge, "cf_request", lambda method, path, params=None, body=None, key=None:
                               {"data": [mods[i] for i in body["fileIds"] if i in mods]} if path == "/mods/files" else (_ for _ in ()).throw(curseforge.CurseForgeError("no"))):
            with self.assertRaisesRegex(packtools.ModpackError, r"1 mod\(s\) cannot be downloaded.*one\.jar"):
                curseforge.resolve_manifest_files(manifest())

    def test_unexpected_hosts_count_as_blocked(self):
        mods = FakeCurseForge().mods
        mods[201]["downloadUrl"] = "https://evil.example/one.jar"
        fake = FakeCurseForge(mods=mods)
        with mock.patch.object(curseforge, "cf_request", fake.cf):
            with self.assertRaisesRegex(packtools.ModpackError, "one.jar"):
                curseforge.resolve_manifest_files(manifest())

    def test_non_jar_files_and_odd_names_are_handled(self):
        mods = {201: file_record(201, "shaders.zip", "https://edge.forgecdn.net/files/2/201/shaders.zip", b"s"),
                202: file_record(202, "../evil.jar", "https://edge.forgecdn.net/files/2/202/evil.jar", b"e")}
        fake = FakeCurseForge(mods=mods)
        with mock.patch.object(curseforge, "cf_request", fake.cf):
            with self.assertRaisesRegex(packtools.ModpackError, "evil.jar"):
                curseforge.resolve_manifest_files(manifest(files=[{"projectID": 1, "fileID": 201}, {"projectID": 1, "fileID": 202}]))


class ServerPackExtraction(AppTestCase):
    def test_wrapper_folder_is_stripped_and_scripts_skipped(self):
        target = HOME / "extract"
        target.mkdir()
        packtools.extract_server_pack(zipfile.ZipFile(io.BytesIO(server_pack_zip())), target)
        self.assertEqual((target / "mods" / "server-mod.jar").read_bytes(), MOD_1)
        self.assertEqual((target / "config" / "server.toml").read_text(), "server config")
        for skipped in ("start.bat", "forge-installer.jar", "user_jvm_args.txt", "server.properties", "libraries"):
            self.assertFalse((target / skipped).exists(), skipped)

    def test_unwrapped_pack_and_zip_slip(self):
        out = io.BytesIO()
        with zipfile.ZipFile(out, "w") as bundle:
            bundle.writestr("mods/a.jar", b"a")
            bundle.writestr("../outside.txt", "x")
            bundle.writestr("mods/../../outside2.txt", "x")
        target = HOME / "extract2"
        target.mkdir()
        packtools.extract_server_pack(zipfile.ZipFile(io.BytesIO(out.getvalue())), target)
        self.assertTrue((target / "mods" / "a.jar").exists())
        self.assertFalse((HOME / "outside.txt").exists() or (HOME / "outside2.txt").exists())


class InstallPipeline(AppTestCase):
    def test_client_pack_route_downloads_mods_and_applies_overrides(self):
        job = run_pack(FakeCurseForge())
        self.assertEqual(job["state"], "done", job["message"])
        self.assertIn("no server pack", job["message"])
        folder = HOME / "servers" / job["server_id"]
        self.assertEqual((folder / "mods" / "one.jar").read_bytes(), MOD_1)
        self.assertEqual((folder / "mods" / "two.jar").read_bytes(), MOD_2)
        self.assertFalse((folder / "mods" / "optional.jar").exists())
        self.assertEqual((folder / "config" / "pack.toml").read_text(), "client config")
        self.assertFalse((HOME / "servers" / "escape.txt").exists())
        meta = json.loads((folder / "manager_meta.json").read_text())
        self.assertEqual((meta["type"], meta["version"], meta["ram"], meta["port"]), ("forge", "1.20.1", 6144, 25570))
        self.assertEqual((meta["modpack"]["source"], meta["modpack"]["name"], meta["modpack"]["client_pack"], meta["modpack"]["mods"]), ("curseforge", "Friendly", True, 2))

    def test_server_pack_route_is_preferred(self):
        fake = FakeCurseForge(server_pack=server_pack_zip())
        job = run_pack(fake)
        self.assertEqual(job["state"], "done", job["message"])
        folder = HOME / "servers" / job["server_id"]
        self.assertEqual((folder / "mods" / "server-mod.jar").read_bytes(), MOD_1)
        self.assertFalse((folder / "mods" / "one.jar").exists(), "the pack's own file list is not downloaded when a server pack exists")
        self.assertNotIn(("POST", "/mods/files"), fake.calls)
        self.assertFalse((folder / "start.bat").exists())
        meta = json.loads((folder / "manager_meta.json").read_text())
        self.assertFalse(meta["modpack"]["client_pack"])
        self.assertNotIn("no server pack", job["message"])

    def test_checksum_failure_removes_the_server(self):
        fake = FakeCurseForge()
        fake.urls["https://edge.forgecdn.net/files/2/201/one.jar"] = b"tampered"
        job = run_pack(fake)
        self.assertEqual(job["state"], "error")
        self.assertIn("checksum", job["message"])
        self.assertEqual(list((HOME / "servers").iterdir()), [])

    def test_blocked_mods_stop_before_any_server_is_created(self):
        mods = FakeCurseForge().mods
        mods[202]["downloadUrl"] = None
        fake = FakeCurseForge(mods=mods)
        original = fake.cf

        def cf(method, path, params=None, body=None, key=None):
            if path.endswith("/download-url"):
                raise curseforge.CurseForgeError("not allowed")
            return original(method, path, params, body, key)

        fake.cf = cf
        job = run_pack(fake)
        self.assertEqual(job["state"], "error")
        self.assertIn("two.jar", job["message"])
        self.assertEqual(list((HOME / "servers").iterdir()), [])

    def test_unsupported_loader_is_refused_early(self):
        job = run_pack(FakeCurseForge(pack=pack_zip(manifest(loader="quilt-0.20.0"))))
        self.assertEqual(job["state"], "error")
        self.assertIn("Quilt", job["message"])
        self.assertEqual(list((HOME / "servers").iterdir()), [])

    def test_pack_download_must_come_from_curseforge(self):
        fake = FakeCurseForge()
        fake.main["downloadUrl"] = "https://evil.example/pack.zip"
        job = run_pack(fake)
        self.assertEqual(job["state"], "error")
        self.assertIn("does not allow", job["message"])

    def test_not_a_curseforge_pack(self):
        out = io.BytesIO()
        with zipfile.ZipFile(out, "w") as bundle:
            bundle.writestr("modrinth.index.json", "{}")
        job = run_pack(FakeCurseForge(pack=out.getvalue()))
        self.assertIn("manifest.json", job["message"])

    def test_file_must_belong_to_the_chosen_pack(self):
        fake = FakeCurseForge()
        fake.main["modId"] = 999
        job = run_pack(fake)
        self.assertIn("does not belong", job["message"])


class KeyAndRoutes(AppTestCase):
    def test_key_is_saved_validated_and_never_returned(self):
        owner = self.owner()
        with mock.patch.object(curseforge, "cf_request", return_value={"data": {}}) as check:
            saved = owner.post("/api/curseforge/key", json={"key": KEY}).get_json()
        self.assertEqual((saved["configured"], saved["hint"]), (True, "..." + KEY[-4:]))
        self.assertEqual(check.call_args.kwargs["key"], KEY, "the key is tested before it is stored")
        self.assertNotIn(KEY, owner.get("/api/curseforge/key").get_data(as_text=True))
        self.assertNotIn(KEY, owner.get("/api/curseforge/status").get_data(as_text=True))
        self.assertEqual(json.loads((HOME / "manager_settings.json").read_text())["curseforge"]["api_key"], KEY)
        removed = owner.post("/api/curseforge/key", json={"key": ""}).get_json()
        self.assertFalse(removed["configured"])

    def test_bad_keys_are_not_stored(self):
        owner = self.owner()
        self.assertEqual(owner.post("/api/curseforge/key", json={"key": "short"}).status_code, 400)
        with mock.patch.object(curseforge, "cf_request", side_effect=curseforge.CurseForgeError("CurseForge rejected the API key.")):
            self.assertEqual(owner.post("/api/curseforge/key", json={"key": KEY}).status_code, 400)
        self.assertFalse(owner.get("/api/curseforge/status").get_json()["configured"])

    def test_pasted_keys_are_cleaned_up(self):
        owner = self.owner()
        with mock.patch.object(curseforge, "cf_request", return_value={"data": {}}) as check:
            owner.post("/api/curseforge/key", json={"key": f'  "{KEY}"\n'})
        self.assertEqual(check.call_args.kwargs["key"], KEY)

    def test_a_rejected_key_explains_what_looks_wrong_without_echoing_it(self):
        owner = self.owner()
        truncated = KEY[:30]
        with mock.patch.object(curseforge, "cf_request", side_effect=curseforge.CurseForgeError("CurseForge rejected the API key (HTTP 403). Check it in Settings.")):
            reply = owner.post("/api/curseforge/key", json={"key": truncated})
        message = reply.get_json()["error"]
        self.assertEqual(reply.status_code, 400)
        self.assertIn("30 characters", message)
        self.assertNotIn(truncated, message)
        self.assertEqual(curseforge.key_shape_hint(KEY[:60].ljust(60, "x")), "", "a well-formed key gets no hint")
        self.assertIn("does not start with", curseforge.key_shape_hint("x" * 60))

    def test_key_is_owner_only(self):
        owner = self.owner()
        helper = self.staff(owner)
        self.assertEqual(helper.get("/api/curseforge/key").status_code, 403)
        self.assertEqual(helper.post("/api/curseforge/key", json={"key": KEY}).status_code, 403)
        self.assertTrue(helper.get("/api/curseforge/status").get_json()["ok"])

    def test_search_without_a_key_explains_what_to_do(self):
        reply = self.owner().get("/api/curseforge/search?q=magic")
        self.assertEqual(reply.status_code, 400)
        self.assertIn("API key", reply.get_json()["error"])

    def test_search_maps_the_request_and_the_results(self):
        owner = self.owner()
        seen = {}

        def fake(method, path, params=None, body=None, key=None):
            seen.update(params=params, path=path)
            return {"data": [{"id": 5, "name": "Pack", "summary": "s", "downloadCount": 9, "logo": {"thumbnailUrl": "https://media.forgecdn.net/x.png"},
                              "authors": [{"name": "me"}], "links": {"websiteUrl": "https://www.curseforge.com/minecraft/modpacks/pack"}}]}

        with mock.patch.object(curseforge, "cf_request", fake):
            hits = owner.get("/api/curseforge/search?q=magic&offset=30").get_json()["hits"]
        self.assertEqual(seen["path"], "/mods/search")
        self.assertEqual((seen["params"]["gameId"], seen["params"]["classId"], seen["params"]["index"], seen["params"]["searchFilter"]), (432, 4471, 30, "magic"))
        self.assertEqual((hits[0]["project_id"], hits[0]["title"], hits[0]["downloads"], hits[0]["source"]), ("5", "Pack", 9, "curseforge"))

    def test_api_errors_become_readable_messages(self):
        def response(status):
            return SimpleNamespace(status_code=status, json=lambda: {}, raise_for_status=lambda: None)

        with mock.patch.object(curseforge, "api_key", lambda: KEY):
            for status, fragment in ((403, "rejected"), (429, "rate limiting"), (404, "does not have")):
                with mock.patch.object(curseforge.requests, "request", return_value=response(status)):
                    with self.assertRaisesRegex(curseforge.CurseForgeError, fragment):
                        curseforge.cf_request("GET", "/x")

    def test_install_route_validation_for_curseforge(self):
        owner = self.owner()
        body = {"source": "curseforge", "project_id": "1", "file_id": "10", "name": "CF", "port": 25581, "ram": 6144, "accept_eula": True}
        self.assertEqual(owner.post("/api/modpacks/install", json={**body, "source": "other"}).status_code, 400)
        self.assertIn("API key", owner.post("/api/modpacks/install", json=body).get_json()["error"])
        with mock.patch.object(modpacks, "curseforge_configured", lambda: True):
            self.assertEqual(owner.post("/api/modpacks/install", json={**body, "file_id": "x"}).status_code, 400)
            self.assertEqual(owner.post("/api/modpacks/install", json={**body, "project_id": ""}).status_code, 400)
            fake = FakeCurseForge()
            inline = SimpleNamespace(Thread=lambda target, args, **kw: SimpleNamespace(start=lambda: target(*args)), Lock=threading.Lock)
            patches = fake.patches() + [mock.patch.object(modpacks, "threading", inline), mock.patch.object(modpacks, "install_curseforge", curseforge.install_curseforge)]
            for p in patches:
                p.start()
            try:
                reply = owner.post("/api/modpacks/install", json=body).get_json()
            finally:
                for p in patches:
                    p.stop()
        self.assertTrue(reply["ok"], reply)
        status = owner.get(f"/api/modpacks/job/{reply['job']}").get_json()
        self.assertEqual(status["state"], "done", status)
        servers = {s["id"]: s for s in owner.get("/api/servers").get_json()}
        self.assertEqual(servers[status["server_id"]]["modpack"]["source"], "curseforge")


class Housekeeping(AppTestCase):
    def test_settings_default_has_no_key(self):
        self.assertEqual(store.load_settings()["curseforge"]["api_key"], "")

    def test_update_never_touches_the_settings_file(self):
        from manager import updater
        self.assertTrue(updater.is_protected("manager_settings.json"))
        make_server()
