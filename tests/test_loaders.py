import hashlib
import os
import subprocess
from pathlib import Path
from unittest import mock

import requests

from manager import loaders, packtools, procs
from tests.support import HOME, AppTestCase, make_server

INSTALLER = b"installer-bytes"
INSTALLER_SHA1 = hashlib.sha1(INSTALLER).hexdigest()


def fake_download(content=INSTALLER):
    def download(urls, destination, hosts, limit=0, sha1=None, sha512=None):
        assert all(host.startswith("maven.") for host in hosts)
        Path(destination).write_bytes(content)
    return download


def fake_sha1(value=INSTALLER_SHA1):
    def fetch(url, limit):
        if value is None:
            raise requests.HTTPError(response=type("R", (), {"status_code": 404})())
        return value.encode() + b"\n"
    return fetch


def fake_run(kind, new_layout=True, returncode=0):
    def run(command, cwd, **kwargs):
        folder = Path(cwd)
        assert command[1:] == ["-jar", "installer.jar", "--installServer"]
        if returncode == 0:
            if new_layout:
                group = "minecraftforge/forge/1.20.1-47.1.0" if kind == "forge" else "neoforged/neoforge/21.1.172"
                arguments = folder / "libraries" / "net" / group
                arguments.mkdir(parents=True)
                (arguments / "win_args.txt").write_text("args")
                (arguments / "unix_args.txt").write_text("args")
            else:
                (folder / "forge-1.12.2-14.23.5.2860.jar").write_text("jar")
                (folder / "forge-1.12.2-14.23.5.2860-installer.jar").write_text("installer")
        return type("Result", (), {"returncode": returncode, "stdout": "log line one\nlog line two\nlog line three\nlast", "stderr": ""})()
    return run


class Detection(AppTestCase):
    def test_supported_loaders_and_the_refusal_message(self):
        for kind in ("fabric", "forge", "neoforge"):
            loaders.check_supported(kind)
        with self.assertRaisesRegex(packtools.ModpackError, "Quilt.*Fabric, Forge and NeoForge"):
            loaders.check_supported("quilt")

    def test_installer_addresses(self):
        self.assertEqual(loaders.installer_url("forge", "1.20.1", "47.1.0"),
                         "https://maven.minecraftforge.net/net/minecraftforge/forge/1.20.1-47.1.0/forge-1.20.1-47.1.0-installer.jar")
        self.assertEqual(loaders.installer_url("neoforge", "1.21.1", "21.1.172"),
                         "https://maven.neoforged.net/releases/net/neoforged/neoforge/21.1.172/neoforge-21.1.172-installer.jar")
        with self.assertRaisesRegex(packtools.ModpackError, "1.20.1"):
            loaders.installer_url("neoforge", "1.20.1", "47.1.106")

    def test_entry_for_the_new_layout_and_the_old_one(self):
        new = HOME / "new"
        (new / "libraries" / "net" / "minecraftforge" / "forge" / "1.20.1-47.1.0").mkdir(parents=True)
        for name in ("win_args.txt", "unix_args.txt"):
            (new / "libraries" / "net" / "minecraftforge" / "forge" / "1.20.1-47.1.0" / name).write_text("x")
        entry = loaders.find_entry("forge", "1.20.1", new)
        self.assertEqual(entry["entry"], {"kind": "args", "dir": "libraries/net/minecraftforge/forge/1.20.1-47.1.0"})
        old = HOME / "old"
        old.mkdir()
        (old / "forge-1.12.2-14.23.5.2860-installer.jar").write_text("i")
        (old / "forge-1.12.2-14.23.5.2860.jar").write_text("j")
        self.assertEqual(loaders.find_entry("forge", "1.12.2", old), {"jar": "forge-1.12.2-14.23.5.2860.jar"})
        empty = HOME / "empty"
        empty.mkdir()
        with self.assertRaisesRegex(packtools.ModpackError, "produced nothing"):
            loaders.find_entry("forge", "1.20.1", empty)


class RunningInstallers(AppTestCase):
    def install(self, kind, mc, version, *, java=("java", 21), download=None, sha=None, run=None):
        folder = HOME / "servers" / f"srv-{kind}-{self.id().rsplit('.', 1)[-1]}"  # servers/ is cleared between tests
        folder.mkdir(parents=True, exist_ok=True)
        steps = []
        with mock.patch.object(loaders, "choose_java", return_value=java), mock.patch.object(loaders, "download_verified", download or fake_download()), \
                mock.patch.object(loaders, "fetch_bytes", sha or fake_sha1()), mock.patch.object(loaders.subprocess, "run", run or fake_run(kind)):
            entry = loaders.install_loader(kind, mc, version, folder, steps.append)
        return entry, folder, steps

    def test_forge_installs_and_cleans_up(self):
        entry, folder, steps = self.install("forge", "1.20.1", "47.1.0", java=("java", 17))
        self.assertEqual(entry["entry"]["kind"], "args")
        self.assertIn("forge", entry["entry"]["dir"])
        self.assertFalse((folder / "installer.jar").exists())
        self.assertTrue(any("few minutes" in step for step in steps))

    def test_neoforge_installs(self):
        entry, _, _ = self.install("neoforge", "1.21.1", "21.1.172")
        self.assertEqual(entry["entry"]["dir"], "libraries/net/neoforged/neoforge/21.1.172")

    def test_old_forge_layout(self):
        entry, _, _ = self.install("forge", "1.12.2", "14.23.5.2860", java=("java", 8), run=fake_run("forge", new_layout=False))
        self.assertEqual(entry, {"jar": "forge-1.12.2-14.23.5.2860.jar"})

    def test_needs_a_suitable_java(self):
        with self.assertRaisesRegex(packtools.ModpackError, "needs Java 21"):
            self.install("neoforge", "1.21.1", "21.1.172", java=(None, None))
        with self.assertRaisesRegex(packtools.ModpackError, "needs Java 21"):
            self.install("neoforge", "1.21.1", "21.1.172", java=("java", 17))

    def test_checksum_mismatch_is_refused_and_the_installer_removed(self):
        with self.assertRaisesRegex(packtools.ModpackError, "checksum"):
            self.install("forge", "1.20.1", "47.1.0", sha=fake_sha1("0" * 40))
        self.assertEqual(list((HOME / "servers").glob("*/installer.jar")), [])

    def test_missing_checksum_file_is_tolerated(self):
        entry, _, _ = self.install("forge", "1.20.1", "47.1.0", sha=fake_sha1(None))
        self.assertIn("entry", entry)

    def test_failed_installer_reports_the_tail_of_its_output(self):
        with self.assertRaisesRegex(packtools.ModpackError, "installer failed: .*last"):
            self.install("forge", "1.20.1", "47.1.0", run=fake_run("forge", returncode=1))
        self.assertEqual(list((HOME / "servers").glob("*/installer.jar")), [])

    def test_installer_timeout(self):
        def hang(command, cwd, **kwargs):
            raise subprocess.TimeoutExpired(command, 1)
        with self.assertRaisesRegex(packtools.ModpackError, "did not finish"):
            self.install("forge", "1.20.1", "47.1.0", run=hang)

    def test_download_failure_is_readable(self):
        def broken(*args, **kwargs):
            raise requests.ConnectionError("boom")
        with self.assertRaisesRegex(packtools.ModpackError, "could not be downloaded"):
            self.install("forge", "1.20.1", "47.1.0", download=broken)

    def test_versions_are_validated(self):
        for bad in ("../x", "1.20.1; rm", ""):
            with self.assertRaises(packtools.ModpackError):
                loaders.install_loader("forge", "1.20.1", bad, HOME)
        with self.assertRaises(packtools.ModpackError):
            loaders.install_loader("quilt", "1.20.1", "0.20", HOME)

    def test_installer_hosts_are_only_the_official_mavens(self):
        self.assertEqual(loaders.INSTALLER_HOSTS, {"maven.minecraftforge.net", "maven.neoforged.net"})


class StartingForgeServers(AppTestCase):
    def meta(self):
        return {"type": "forge", "version": "1.20.1", "jar": "libraries/x/win_args.txt",
                "entry": {"kind": "args", "dir": "libraries/net/minecraftforge/forge/1.20.1-47.1.0"}}

    def test_command_uses_the_arguments_file(self):
        folder = make_server("forge1", **self.meta())
        arguments = "win_args.txt" if os.name == "nt" else "unix_args.txt"
        cmd = procs.jvm_command("java", self.meta(), folder / "ignored.jar", 6144, folder)
        self.assertEqual(cmd[:3], ["java", "-Xms3072M", "-Xmx6144M"])
        self.assertEqual(cmd[-2:], [f"@libraries/net/minecraftforge/forge/1.20.1-47.1.0/{arguments}", "nogui"])
        self.assertNotIn("-jar", cmd)
        (folder / "user_jvm_args.txt").write_text("# jvm args")
        self.assertIn("@user_jvm_args.txt", procs.jvm_command("java", self.meta(), folder / "ignored.jar", 6144, folder))

    def test_a_jar_server_is_unchanged(self):
        folder = make_server()
        self.assertEqual(procs.jvm_command("java", {}, folder / "server.jar", 2048, folder)[-3:], ["-jar", str(folder / "server.jar"), "nogui"])

    def test_path_escape_in_the_entry_is_ignored(self):
        for bad in ("../x", "a/../../b", ""):
            self.assertIsNone(procs.args_file({"entry": {"kind": "args", "dir": bad}}, HOME))

    def test_start_explains_a_missing_arguments_file(self):
        make_server("forge2", **self.meta())
        with mock.patch.object(procs, "choose_java", return_value=("java", 17)):
            ok, message = procs.start_server("forge2")
        self.assertFalse(ok)
        self.assertIn("Start-up file missing", message)
