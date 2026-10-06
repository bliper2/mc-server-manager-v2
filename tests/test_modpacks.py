import hashlib
import io
import json
import threading
import time
import zipfile
from types import SimpleNamespace
from unittest import mock

import requests

from manager import loaders, modpacks, packtools, state
from tests.support import HOME, AppTestCase, make_server

PACK_URL = "https://cdn.modrinth.com/data/pack1/versions/ver1/Pack-1.0.mrpack"
MOD_A = b"mod-a-bytes"
MOD_B = b"mod-b-bytes"


def sha1(data):
    return hashlib.sha1(data).hexdigest()


def build_pack(files=None, dependencies=None, extra=None, index_override=None):
    """A small valid Fabric .mrpack held in memory."""
    index = index_override or {
        "formatVersion": 1, "game": "minecraft", "versionId": "1.0", "name": "Test Pack",
        "dependencies": dependencies or {"minecraft": "1.20.1", "fabric-loader": "0.15.7"},
        "files": files if files is not None else [
            {"path": "mods/a.jar", "hashes": {"sha1": sha1(MOD_A)}, "downloads": ["https://cdn.modrinth.com/data/a/a.jar"], "env": {"client": "required", "server": "required"}},
            {"path": "mods/b.jar", "hashes": {"sha1": sha1(MOD_B)}, "downloads": ["https://cdn.modrinth.com/data/b/b.jar"]},
            {"path": "mods/minimap.jar", "hashes": {"sha1": sha1(b"x")}, "downloads": ["https://cdn.modrinth.com/data/c/c.jar"], "env": {"client": "required", "server": "unsupported"}},
        ],
    }
    out = io.BytesIO()
    with zipfile.ZipFile(out, "w") as bundle:
        bundle.writestr("modrinth.index.json", json.dumps(index))
        bundle.writestr("overrides/config/pack.toml", "from overrides")
        bundle.writestr("overrides/config/both.toml", "overrides version")
        bundle.writestr("server-overrides/config/both.toml", "server version")
        bundle.writestr("overrides/server.properties", "evil=true")
        bundle.writestr("overrides/../escape.txt", "escape")
        bundle.writestr("overrides/manager_meta.json", "{}")
        for name, data in (extra or {}).items():
            bundle.writestr(name, data)
    return out.getvalue()


def fake_internet(pack_bytes, mods=None, bad_hash=False):
    """Replacement for modpacks._stream serving the pack, the mods and Fabric's meta service from memory."""
    mods = mods or {"https://cdn.modrinth.com/data/a/a.jar": MOD_A, "https://cdn.modrinth.com/data/b/b.jar": MOD_B}

    def stream(url, limit):
        if url == PACK_URL:
            body = pack_bytes
        elif url.endswith("/versions/installer"):
            body = json.dumps([{"version": "1.0.1", "stable": False}, {"version": "0.11.2", "stable": True}]).encode()
        elif "/versions/loader/1.20.1/0.15.7/0.11.2/server/jar" in url:
            body = b"fabric-launcher"
        elif url in mods:
            body = b"tampered" if bad_hash and url.endswith("a.jar") else mods[url]
        else:
            raise requests.ConnectionError(url)
        yield body

    return stream


def version_info(pack_bytes):
    return {"id": "ver1", "project_id": "proj1", "version_number": "1.0",
            "files": [{"primary": True, "filename": "Pack-1.0.mrpack", "url": PACK_URL, "hashes": {"sha1": sha1(pack_bytes)}}]}


class InstallPipeline(AppTestCase):
    def run_install(self, pack, **net):
        job_id = "job1"
        state.modpack_jobs[job_id] = {"state": "running", "message": "", "progress": 0, "server_id": None, "updated": time.time()}
        modpacks._install_lock.acquire()
        with mock.patch.object(modpacks, "modrinth_version", return_value=version_info(pack)), mock.patch.object(packtools, "_stream", fake_internet(pack, **net)):
            modpacks.install_modpack(job_id, "ver1", "My Pack", 6144, 25570, "Friendly Title")
        return state.modpack_jobs[job_id]

    def test_installs_a_fabric_pack(self):
        job = self.run_install(build_pack())
        self.assertEqual(job["state"], "done", job["message"])
        folder = HOME / "servers" / job["server_id"]
        self.assertEqual((folder / "mods" / "a.jar").read_bytes(), MOD_A)
        self.assertEqual((folder / "mods" / "b.jar").read_bytes(), MOD_B)
        self.assertFalse((folder / "mods" / "minimap.jar").exists(), "client-only files are skipped")
        self.assertEqual((folder / loaders.LAUNCHER).read_bytes(), b"fabric-launcher")
        self.assertEqual((folder / "config" / "pack.toml").read_text(), "from overrides")
        self.assertEqual((folder / "config" / "both.toml").read_text(), "server version", "server-overrides win")
        meta = json.loads((folder / "manager_meta.json").read_text())
        self.assertEqual((meta["type"], meta["version"], meta["jar"], meta["ram"], meta["port"]), ("fabric", "1.20.1", loaders.LAUNCHER, 6144, 25570))
        self.assertEqual(meta["modpack"]["source"], "modrinth")
        self.assertEqual((meta["modpack"]["mods"], meta["modpack"]["name"]), (2, "Friendly Title"))
        self.assertTrue(meta["eula_accepted"])
        self.assertIn("server-port=25570", (folder / "server.properties").read_text())

    def test_pack_cannot_overwrite_manager_files_or_escape(self):
        job = self.run_install(build_pack())
        folder = HOME / "servers" / job["server_id"]
        self.assertNotIn("evil", (folder / "server.properties").read_text())
        self.assertNotEqual((folder / "manager_meta.json").read_text(), "{}")
        self.assertFalse((HOME / "servers" / "escape.txt").exists())
        self.assertFalse((HOME / "escape.txt").exists())

    def test_checksum_mismatch_aborts_and_cleans_up(self):
        job = self.run_install(build_pack(), bad_hash=True)
        self.assertEqual(job["state"], "error")
        self.assertIn("checksum", job["message"])
        self.assertEqual(list((HOME / "servers").iterdir()), [], "a failed install leaves no server behind")

    def test_quilt_is_refused_with_a_reason(self):
        job = self.run_install(build_pack(dependencies={"minecraft": "1.20.1", "quilt-loader": "0.20.0"}))
        self.assertEqual(job["state"], "error")
        self.assertIn("Quilt", job["message"])
        self.assertEqual(list((HOME / "servers").iterdir()), [])

    def test_a_pack_without_a_loader_is_refused(self):
        job = self.run_install(build_pack(dependencies={"minecraft": "1.20.1"}))
        self.assertIn("mod loader", job["message"])

    def test_path_traversal_in_the_index_is_refused(self):
        for bad in ("../outside.jar", "mods/../../outside.jar", "C:/windows/x.jar", "server.jar", "manager_meta.json"):
            files = [{"path": bad, "hashes": {"sha1": sha1(MOD_A)}, "downloads": ["https://cdn.modrinth.com/data/a/a.jar"]}]
            job = self.run_install(build_pack(files=files))
            self.assertEqual(job["state"], "error", bad)
            self.assertIn("not allowed", job["message"], bad)

    def test_downloads_only_from_allowed_hosts(self):
        files = [{"path": "mods/a.jar", "hashes": {"sha1": sha1(MOD_A)}, "downloads": ["https://evil.example/a.jar"]}]
        job = self.run_install(build_pack(files=files))
        self.assertEqual(job["state"], "error")
        self.assertIn("not an allowed download host", job["message"])
        self.assertFalse(modpacks.trusted_pack_url("http://cdn.modrinth.com/a.jar"))
        self.assertFalse(modpacks.trusted_pack_url("https://cdn.modrinth.com.evil.example/a.jar"))
        for host in modpacks.PACK_HOSTS:
            self.assertTrue(modpacks.trusted_pack_url(f"https://{host}/x"))

    def test_second_mirror_is_used_when_the_first_fails(self):
        files = [{"path": "mods/a.jar", "hashes": {"sha1": sha1(MOD_A)},
                  "downloads": ["https://cdn.modrinth.com/dead.jar", "https://cdn.modrinth.com/data/a/a.jar"]}]
        job = self.run_install(build_pack(files=files))
        self.assertEqual(job["state"], "done", job["message"])

    def test_malformed_packs(self):
        garbage = self.run_install(b"this is not a zip")
        self.assertEqual(garbage["state"], "error")
        wrong_game = self.run_install(build_pack(index_override={"formatVersion": 1, "game": "other", "dependencies": {}, "files": []}))
        self.assertIn("not a Minecraft", wrong_game["message"])
        too_many = self.run_install(build_pack(files=[{"path": f"mods/{i}.jar", "hashes": {"sha1": "0"}, "downloads": []} for i in range(modpacks.MAX_FILES + 1)]))
        self.assertIn("more than", too_many["message"])
        missing_hash = self.run_install(build_pack(files=[{"path": "mods/a.jar", "downloads": ["https://cdn.modrinth.com/data/a/a.jar"]}]))
        self.assertIn("checksum", missing_hash["message"])
        self.assertEqual(list((HOME / "servers").iterdir()), [])

    def test_pack_checksum_is_verified(self):
        pack = build_pack()
        info = version_info(pack)
        info["files"][0]["hashes"]["sha1"] = "0" * 40
        state.modpack_jobs["j"] = {"state": "running", "message": "", "progress": 0, "server_id": None, "updated": time.time()}
        modpacks._install_lock.acquire()
        with mock.patch.object(modpacks, "modrinth_version", return_value=info), mock.patch.object(packtools, "_stream", fake_internet(pack)):
            modpacks.install_modpack("j", "ver1", "X", 4096, 25571)
        self.assertIn("checksum", state.modpack_jobs["j"]["message"])

    def test_lock_is_released_even_when_the_install_fails(self):
        self.run_install(b"junk")
        self.assertTrue(modpacks._install_lock.acquire(blocking=False))
        modpacks._install_lock.release()


class InstallRoute(AppTestCase):
    BODY = {"version_id": "abc12345", "name": "Pack Server", "port": 25580, "ram": 4096, "accept_eula": True}

    def post(self, client, **changes):
        return client.post("/api/modpacks/install", json={**self.BODY, **changes})

    def run_inline(self):
        """Runs the install on the calling thread. Only the module's own `threading` name is replaced, because
        patching threading.Thread itself would also break the download pool."""
        inline = SimpleNamespace(Thread=lambda target, args, **kw: SimpleNamespace(start=lambda: target(*args)), Lock=threading.Lock)
        return mock.patch.object(modpacks, "threading", inline)

    def test_validation(self):
        owner = self.owner()
        self.assertEqual(self.post(owner, version_id="../x").status_code, 400)
        self.assertEqual(self.post(owner, name=" ").status_code, 400)
        self.assertEqual(self.post(owner, accept_eula=False).status_code, 400)
        self.assertEqual(self.post(owner, port=80).status_code, 400)
        self.assertEqual(self.post(owner, ram=8).status_code, 400)
        make_server("taken", port=25580, name="Taken")
        clash = self.post(owner, port=25580)
        self.assertEqual(clash.status_code, 409)
        self.assertIn("Taken", clash.get_json()["error"])

    def test_needs_the_manage_permission(self):
        owner = self.owner()
        helper = self.staff(owner, permissions=["control"])
        self.assertEqual(self.post(helper).status_code, 403)
        allowed = self.staff(owner, "bobby", "bobpass12", permissions=["manage"])
        pack = build_pack()
        with self.run_inline(), mock.patch.object(modpacks, "modrinth_version", return_value=version_info(pack)), mock.patch.object(packtools, "_stream", fake_internet(pack)):
            reply = self.post(allowed, name="By Bobby")
        self.assertTrue(reply.get_json()["ok"])

    def test_job_is_reported_and_unknown_jobs_404(self):
        owner = self.owner()
        pack = build_pack()
        with self.run_inline(), mock.patch.object(modpacks, "modrinth_version", return_value=version_info(pack)), mock.patch.object(packtools, "_stream", fake_internet(pack)):
            job = self.post(owner).get_json()["job"]
        status = owner.get(f"/api/modpacks/job/{job}").get_json()
        self.assertEqual((status["state"], status["progress"]), ("done", 100))
        self.assertTrue(status["server_id"])
        servers = {s["id"]: s for s in owner.get("/api/servers").get_json()}
        self.assertEqual(servers[status["server_id"]]["modpack"]["name"], "Test Pack")
        self.assertEqual(owner.get("/api/modpacks/job/nope").status_code, 404)

    def test_only_one_install_at_a_time(self):
        owner = self.owner()
        modpacks._install_lock.acquire()
        try:
            self.assertEqual(self.post(owner).status_code, 409)
        finally:
            modpacks._install_lock.release()

    def test_search_asks_for_server_ready_packs(self):
        from manager import providers
        seen = {}

        def fake_get(url, params=None, **kw):
            seen.update(params)
            return type("R", (), {"raise_for_status": lambda s: None, "json": lambda s: {"hits": []}})()

        with mock.patch.object(providers.requests, "get", fake_get):
            providers.modrinth_search("magic", "modpack")
        facets = json.loads(seen["facets"])
        self.assertIn(["project_type:modpack"], facets)
        self.assertIn(["categories:fabric", "categories:forge", "categories:neoforge"], facets)
        self.assertIn(["server_side:required", "server_side:optional"], facets)
