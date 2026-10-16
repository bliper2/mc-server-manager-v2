"""Scheduled plugin updates wait for a stopped server and record what happened."""
from unittest import mock

from tests.support import AppTestCase, make_server
from manager import automation, store

NOW = 1_700_000_000
UPDATE = {
    "folder": "plugins",
    "file": "EssentialsX.jar",
    "name": "EssentialsX",
    "latest_version": "2.21.0",
    "update_available": True,
    "download_url": "https://cdn.modrinth.com/data/a/b.jar",
    "download_filename": "EssentialsX-2.21.0.jar",
}


def _meta():
    return store.load_meta("alpha_1")


class PluginAutoUpdate(AppTestCase):
    def _server(self, **auto_update):
        settings = {"enabled": True, "interval_hours": 12, "install": True}
        settings.update(auto_update)
        make_server(auto_update=settings)

    def test_a_running_server_queues_the_download_until_it_stops(self):
        self._server()
        meta = _meta()
        with mock.patch.object(automation, "scan_plugin_updates", return_value=[UPDATE]) as scan, \
                mock.patch.object(automation, "apply_plugin_update") as apply, \
                mock.patch.object(automation, "is_running", return_value=True), \
                mock.patch.object(automation, "notify") as notify:
            self.assertTrue(automation.maintain_plugin_updates("alpha_1", meta, NOW))
            apply.assert_not_called()
            self.assertEqual(meta["plugin_update_pending"][0]["file"], "EssentialsX.jar")
            self.assertEqual(meta["last_update_report"]["waiting"], ["EssentialsX 2.21.0"])
            notify.assert_called_once()
            self.assertIn("will install after the server stops", notify.call_args.args[1])

            automation.maintain_plugin_updates("alpha_1", meta, NOW + 60)
            scan.assert_called_once()
            notify.assert_called_once()

        with mock.patch.object(automation, "scan_plugin_updates") as scan, \
                mock.patch.object(automation, "apply_plugin_update", return_value=(True, "EssentialsX-2.21.0.jar")) as apply, \
                mock.patch.object(automation, "is_running", return_value=False), \
                mock.patch.object(automation, "notify") as notify:
            automation.maintain_plugin_updates("alpha_1", meta, NOW + 120)
        scan.assert_not_called()
        apply.assert_called_once_with("alpha_1", "plugins", "EssentialsX.jar", UPDATE["download_url"], UPDATE["download_filename"])
        self.assertEqual(meta["plugin_update_pending"], [])
        self.assertEqual(meta["last_update_report"]["installed"], ["EssentialsX 2.21.0"])
        self.assertIn("Restart the server to load them", notify.call_args.args[1])

    def test_the_same_update_is_not_announced_twice(self):
        self._server(interval_hours=1)
        meta = _meta()
        with mock.patch.object(automation, "scan_plugin_updates", return_value=[UPDATE]), \
                mock.patch.object(automation, "is_running", return_value=True), \
                mock.patch.object(automation, "notify") as notify:
            automation.maintain_plugin_updates("alpha_1", meta, NOW)
            automation.maintain_plugin_updates("alpha_1", meta, NOW + 3600 + 5)
        notify.assert_called_once()

    def test_a_failed_check_waits_fifteen_minutes(self):
        self._server()
        meta = _meta()
        with mock.patch.object(automation, "scan_plugin_updates", side_effect=RuntimeError("down")) as scan, \
                mock.patch.object(automation, "is_running", return_value=False), \
                mock.patch.object(automation, "notify") as notify:
            automation.maintain_plugin_updates("alpha_1", meta, NOW)
            self.assertNotIn("last_update_check_at", meta)
            self.assertEqual(meta["next_update_check_at"], NOW + automation.UPDATE_RETRY_SECONDS)
            self.assertIn("Modrinth", meta["last_update_report"]["error"])
            automation.maintain_plugin_updates("alpha_1", meta, NOW + 60)
            scan.assert_called_once()
            automation.maintain_plugin_updates("alpha_1", meta, NOW + automation.UPDATE_RETRY_SECONDS)
        self.assertEqual(scan.call_count, 2)
        notify.assert_called_once()

    def test_a_rejected_download_is_not_retried_every_minute(self):
        self._server()
        meta = _meta()
        meta["plugin_update_pending"] = [dict(UPDATE)]
        meta["last_update_check_at"] = NOW
        with mock.patch.object(automation, "apply_plugin_update", return_value=(False, "Not installed. This is a Fabric mod.")) as apply, \
                mock.patch.object(automation, "is_running", return_value=False), \
                mock.patch.object(automation, "notify"):
            automation.maintain_plugin_updates("alpha_1", meta, NOW + 30)
            self.assertEqual(meta["plugin_update_pending"], [])
            self.assertEqual(meta["last_update_report"]["failed"][0]["file"], "EssentialsX.jar")
            automation.maintain_plugin_updates("alpha_1", meta, NOW + 90)
        apply.assert_called_once()

    def test_a_locked_file_stays_queued(self):
        self._server()
        meta = _meta()
        meta["plugin_update_pending"] = [dict(UPDATE)]
        meta["last_update_check_at"] = NOW
        locked = f"{automation._FILE_LOCKED}: [WinError 32]"
        with mock.patch.object(automation, "apply_plugin_update", return_value=(False, locked)), \
                mock.patch.object(automation, "is_running", return_value=False), \
                mock.patch.object(automation, "notify") as notify:
            automation.maintain_plugin_updates("alpha_1", meta, NOW + 30)
        self.assertEqual(meta["plugin_update_pending"][0]["tries"], 1)
        self.assertIn("will install after the server stops", notify.call_args.args[1])

    def test_the_api_reports_the_result_without_the_download_url(self):
        self._server(install=False)
        folder = make_server(auto_update={"enabled": True, "interval_hours": 6, "install": False})
        meta = store.load_meta("alpha_1")
        meta["last_update_check"] = "2026-10-16T12:00:00"
        meta["last_update_report"] = {
            "at": "2026-10-16T12:00:00",
            "found": 1,
            "installed": ["EssentialsX 2.21.0"],
            "waiting": [],
            "failed": [],
            "error": None,
            "download_url": "https://cdn.modrinth.com/secret.jar",
        }
        meta["plugin_update_pending"] = [dict(UPDATE)]
        store.save_meta("alpha_1", meta)
        reply = self.owner().get("/api/server/alpha_1/auto-update")
        body = reply.get_data(as_text=True)
        self.assertEqual(reply.status_code, 200)
        self.assertNotIn("cdn.modrinth.com", body)
        self.assertEqual(reply.get_json()["report"]["installed"], ["EssentialsX 2.21.0"])
        self.assertEqual(reply.get_json()["settings"]["interval_hours"], 6)
        self.assertTrue((folder / "manager_meta.json").exists())

    def test_maintenance_runs_the_check_and_saves_it(self):
        self._server(install=False)
        with mock.patch.object(automation, "check_manager_update"), \
                mock.patch.object(automation, "scan_plugin_updates", return_value=[]) as scan, \
                mock.patch.object(automation, "is_running", return_value=False):
            automation.run_scheduled_maintenance()
        scan.assert_called_once_with("alpha_1")
        meta = _meta()
        self.assertIn("last_update_check", meta)
        self.assertEqual(meta["last_update_report"]["found"], 0)
        self.assertIsNone(meta["last_update_report"]["error"])
