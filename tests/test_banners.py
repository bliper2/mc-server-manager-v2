"""Wide server banners, animated GIFs included, from the banners folder."""
import io
import json
import shutil
from unittest import mock

from manager import logos
from tests import test_logos as shared
from tests.support import HOME, AppTestCase, make_server

GIF, PNG, JPEG, PAGE = shared.GIF, shared.PNG, shared.JPEG, shared.PAGE_AS_PNG


class Banners(AppTestCase):
    def drop(self, name, data):
        (HOME / "banners").mkdir(exist_ok=True)
        (HOME / "banners" / name).write_bytes(data)

    def test_the_banners_folder_has_a_note_and_lists_dropped_files_including_gifs(self):
        shutil.rmtree(HOME / "banners", ignore_errors=True)
        owner = self.owner()
        self.assertEqual(owner.get("/api/banners").get_json()["custom"], [])
        self.assertEqual([p.name for p in (HOME / "banners").iterdir()], ["Put your banner images here.txt"])
        self.drop("Raid night.GIF", GIF)
        self.drop("plain", PNG)
        self.drop("fake.gif", PAGE)
        listed = owner.get("/api/banners").get_json()["custom"]
        self.assertEqual(sorted(banner["name"] for banner in listed), ["Raid night", "plain"])
        gif = next(banner for banner in listed if banner["name"] == "Raid night")
        served = owner.get(gif["url"])
        served.close()
        self.assertEqual((served.status_code, served.mimetype), (200, "image/gif"))
        self.assertIn("no-cache", served.headers["Cache-Control"])

    def test_banners_and_logos_are_separate_libraries(self):
        owner = self.owner()
        (HOME / "logos").mkdir(exist_ok=True)
        (HOME / "logos" / "only-a-logo.png").write_bytes(PNG)
        self.drop("only-a-banner.png", PNG)
        self.assertEqual([b["ref"] for b in owner.get("/api/banners").get_json()["custom"]], ["custom/only-a-banner.png"])
        self.assertEqual([b["ref"] for b in owner.get("/api/logos").get_json()["custom"]], ["custom/only-a-logo.png"])
        self.assertIsNone(logos.resolve_banner("custom/only-a-logo.png"))
        self.assertIsNone(logos.resolve_ref("custom/only-a-banner.png"))

    def test_a_banner_gif_is_copied_byte_for_byte_so_it_stays_animated(self):
        folder = make_server()
        owner = self.owner()
        self.drop("raid.gif", GIF)
        reply = owner.post("/api/server/alpha_1/banner", json={"banner": "custom/raid.gif"})
        self.assertEqual(reply.status_code, 200, reply.get_json())
        self.assertEqual((folder / "manager_banner.gif").read_bytes(), GIF)
        banner = next(s for s in owner.get("/api/servers").get_json() if s["id"] == "alpha_1")["banner"]
        self.assertEqual(banner["file"], "manager_banner.gif")
        self.assertGreater(banner["rev"], 0)
        self.assertEqual(shared.status_of(owner, f"/api/server/alpha_1/banner/{banner['file']}?v={banner['rev']}"), 200)

    def test_replacing_and_removing_a_banner(self):
        folder = make_server()
        owner = self.owner()
        self.drop("one.gif", GIF)
        self.drop("two.png", PNG)
        owner.post("/api/server/alpha_1/banner", json={"banner": "custom/one.gif"})
        owner.post("/api/server/alpha_1/banner", json={"banner": "custom/two.png"})
        self.assertEqual(sorted(p.name for p in folder.glob("manager_banner.*")), ["manager_banner.png"])
        self.assertEqual(owner.delete("/api/server/alpha_1/banner").status_code, 200)
        self.assertEqual(list(folder.glob("manager_banner.*")), [])
        self.assertNotIn("banner", json.loads((folder / "manager_meta.json").read_text()))
        self.assertEqual(owner.delete("/api/server/alpha_1/banner").status_code, 200, "removing twice is harmless")

    def test_uploading_a_banner_straight_to_a_server_checks_the_content(self):
        folder = make_server()
        owner = self.owner()
        ok = owner.post("/api/server/alpha_1/banner", data={"banner": (io.BytesIO(GIF), "x.gif")}, content_type="multipart/form-data")
        self.assertEqual(ok.status_code, 200, ok.get_json())
        bad = owner.post("/api/server/alpha_1/banner", data={"banner": (io.BytesIO(PAGE), "x.png")}, content_type="multipart/form-data")
        self.assertEqual(bad.status_code, 400)
        self.assertEqual((folder / "manager_banner.gif").read_bytes(), GIF, "a refused upload leaves the old banner alone")
        self.assertEqual(owner.post("/api/server/alpha_1/banner", json={"banner": "custom/missing.png"}).status_code, 400)
        self.assertEqual(owner.post("/api/server/alpha_1/banner", json={"banner": "bundled/pixel-apple.png"}).status_code, 400)
        self.assertEqual(owner.post("/api/server/nope/banner", json={"banner": "custom/x.png"}).status_code, 404)

    def test_banners_may_be_bigger_than_logos(self):
        self.assertGreater(logos.MAX_BANNER_BYTES, logos.MAX_LOGO_BYTES)
        make_server()
        owner = self.owner()
        big = GIF + b"0" * (logos.MAX_LOGO_BYTES + 10)
        self.drop("big.gif", big)
        self.assertEqual(len(owner.get("/api/banners").get_json()["custom"]), 1)
        self.assertEqual(owner.post("/api/server/alpha_1/banner", json={"banner": "custom/big.gif"}).status_code, 200)
        self.assertEqual(owner.post("/api/server/alpha_1/logo", data={"logo": (io.BytesIO(big), "big.gif")}, content_type="multipart/form-data").status_code, 400)
        with mock.patch.object(logos, "MAX_BANNER_BYTES", 100):
            self.assertEqual(owner.get("/api/banners").get_json()["custom"], [])

    def test_banner_uploads_to_the_library_and_permissions(self):
        owner = self.owner()
        make_server()
        files = self.staff(owner, "files", "filespass1", permissions=["files"])
        maker = self.staff(owner, "maker", "makerpass1", permissions=["manage"])
        added = maker.post("/api/banners/custom", data=shared.upload(("raid.gif", GIF), ("bad.png", PAGE)), content_type="multipart/form-data").get_json()
        self.assertEqual([a["name"] for a in added["added"]], ["raid"])
        self.assertEqual(len(added["skipped"]), 1)
        self.assertEqual(files.post("/api/banners/custom", data=shared.upload(("x.gif", GIF)), content_type="multipart/form-data").status_code, 403)
        self.assertEqual(files.delete("/api/banners/custom/raid.gif").status_code, 403)
        self.assertEqual(files.post("/api/banners/pinterest", json={"url": "https://pin.it/x"}).status_code, 403)
        self.assertEqual(files.post("/api/server/alpha_1/banner", json={"banner": "custom/raid.gif"}).status_code, 200, "files permission may set a banner")
        self.assertEqual(maker.post("/api/server/alpha_1/banner", json={"banner": "custom/raid.gif"}).status_code, 403)
        self.assertEqual(maker.delete("/api/banners/custom/raid.gif").status_code, 200)
        self.assertEqual(owner.get("/api/banners").get_json()["custom"], [])

    def test_banner_urls_with_a_revision_are_cached_and_the_rest_is_not(self):
        make_server()
        owner = self.owner()
        self.drop("a.png", PNG)
        owner.post("/api/server/alpha_1/banner", json={"banner": "custom/a.png"})
        with_rev = owner.get("/api/server/alpha_1/banner/manager_banner.png?v=5")
        with_rev.close()
        without = owner.get("/api/server/alpha_1/banner/manager_banner.png")
        without.close()
        self.assertIn("immutable", with_rev.headers["Cache-Control"])
        self.assertEqual(without.headers["Cache-Control"], "no-store")
        self.assertEqual(shared.status_of(owner, "/api/server/alpha_1/banner/manager_meta.json?v=1"), 404)
        self.assertEqual(shared.status_of(owner, "/api/server/alpha_1/banner/..%2Fmanager_meta.json?v=1"), 404)

    def test_a_pinterest_pin_can_become_a_banner(self):
        owner = self.owner()
        pin, image = "https://www.pinterest.com/pin/586734657738896202/", "https://i.pinimg.com/736x/ac/eb/b0/acebb0df.jpg"
        web = shared.FakeWeb({pin: shared.FakeResponse(shared.pin_page()), image: shared.FakeResponse(JPEG)})
        with mock.patch.object(logos.requests, "get", web.get):
            body = owner.post("/api/banners/pinterest", json={"url": pin}).get_json()
        self.assertTrue(body["ok"], body)
        self.assertTrue(body["banner"]["ref"].startswith("custom/pin-"))
        self.assertEqual(len(list((HOME / "banners").glob("pin-*"))), 1)
        self.assertEqual(logos.list_logos()["custom"], [], "it went into the banners folder, not the logos folder")
