"""Logo library: bundled artwork, imported images, Pinterest pins, and putting a logo on a server."""
import io
import json
import struct
from unittest import mock

import requests

from tests.support import HOME, AppTestCase, make_server
from manager import logos, routes_servers

PNG = b"\x89PNG\r\n\x1a\n" + b"0" * 64
JPEG = b"\xff\xd8\xff\xe0" + b"0" * 64
GIF = b"GIF89a" + b"0" * 64
PAGE_AS_PNG = b"<html><script>alert(1)</script></html>"


def status_of(client, url):
    """Status of a GET, closing the response so the served file is released straight away (matters on Windows)."""
    reply = client.get(url)
    reply.close()
    return reply.status_code


def upload(*files):
    return {"files": [(io.BytesIO(data), name) for name, data in files]}


class FakeResponse:
    def __init__(self, body=b"", status=200, location=None):
        self.body, self.status_code = body, status
        self.headers = {"location": location} if location else {}
        self.is_redirect = status in (301, 302, 303, 307, 308) and bool(location)

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def raise_for_status(self):
        if self.status_code >= 400:
            raise requests.HTTPError(f"{self.status_code}")

    def iter_content(self, size):
        for start in range(0, len(self.body), size):
            yield self.body[start:start + size]


def pin_page(image="https://i.pinimg.com/736x/ac/eb/b0/acebb0df.jpg", order="content-first"):
    tag = (f'<meta content="{image}" data-app="true" name="og:image" property="og:image"/>' if order == "content-first"
           else f'<meta property="og:image" content="{image}">')
    # like the real page: an early </head> inside a script, and the meta tag a long way down
    return f"<html><head><script>var s='</head>';</script>{'x' * 200000}{tag}</head><body>{'x' * 5000}</body></html>".encode()


class FakeWeb:
    """Answers requests.get for the URLs in `routes`; records what was asked."""

    def __init__(self, routes):
        self.routes, self.asked = routes, []

    def get(self, url, **kwargs):
        self.asked.append(url)
        assert kwargs.get("allow_redirects") is False, "redirects must be followed by hand"
        answer = self.routes[url]
        if isinstance(answer, Exception):
            raise answer
        return answer


class BundledLibrary(AppTestCase):
    def test_a_large_valid_library_ships_with_the_app(self):
        library = logos.list_logos()["bundled"]
        self.assertGreaterEqual(len(library), 100)
        self.assertEqual(len({logo["ref"] for logo in library}), len(library))
        self.assertTrue({"combat", "royal", "nature", "build", "pixel", "animated"} <= {logo["category"] for logo in library})
        animated = 0
        for logo in library:
            data = logos.resolve_ref(logo["ref"]).read_bytes()
            if logo["ref"].endswith(".gif"):
                animated += 1
                self.assertEqual(logos.image_extension(data), ".gif", logo["ref"])
                self.assertEqual(logo["category"], "animated", logo["ref"])
                self.assertEqual(struct.unpack("<HH", data[6:10]), (128, 128), logo["ref"])
                self.assertIn(b"NETSCAPE2.0", data[:1024], f"{logo['ref']} must loop")
                self.assertLess(len(data), 120 * 1024, logo["ref"])
            else:
                self.assertEqual(logos.image_extension(data), ".png", logo["ref"])
                self.assertEqual(struct.unpack(">II", data[16:24]), (128, 128), logo["ref"])
                self.assertLess(len(data), 40 * 1024, logo["ref"])
        self.assertGreaterEqual(animated, 10)

    def test_the_list_endpoint_describes_every_logo(self):
        owner = self.owner()
        data = owner.get("/api/logos").get_json()
        self.assertTrue(data["ok"])
        sword = next(logo for logo in data["bundled"] if logo["category"] == "combat" and logo["name"].startswith("Sword"))
        self.assertTrue(sword["url"].startswith("/static/logos/combat-sword-"))
        self.assertEqual(status_of(owner, sword["url"]), 200)
        self.assertEqual(data["custom"], [])
        self.assertIn("community", data)

    def test_references_cannot_leave_the_library_folders(self):
        for bad in ("bundled/../../app.py", "bundled/..", "custom/../staff.json", "other/x.png", "bundled/Pixel-Apple.png", "", None, "pixel-apple.png",
                    "bundled/pixel-apple.png/../../x.png", "bundled/pixel-apple.exe"):
            self.assertIsNone(logos.resolve_ref(bad), bad)
        self.assertIsNotNone(logos.resolve_ref("bundled/pixel-apple.png"))


class ImportedLogos(AppTestCase):
    def test_several_images_are_imported_at_once_and_bad_ones_are_skipped(self):
        owner = self.owner()
        reply = owner.post("/api/logos/custom", data=upload(("My Logo.png", PNG), ("photo.jpg", JPEG), ("trick.png", PAGE_AS_PNG)),
                           content_type="multipart/form-data")
        body = reply.get_json()
        self.assertEqual(reply.status_code, 200, body)
        self.assertEqual(sorted(logo["name"] for logo in body["added"]), ["My Logo", "photo"])
        self.assertEqual(len(body["skipped"]), 1)
        self.assertIn("trick.png", body["skipped"][0])
        listed = owner.get("/api/logos").get_json()["custom"]
        self.assertEqual(len(listed), 2)
        served = owner.get(listed[0]["url"])
        served.close()
        self.assertEqual(served.status_code, 200)
        self.assertIn("no-cache", served.headers["Cache-Control"], "files in the folder can change, so the browser must revalidate")
        self.assertTrue(served.headers.get("ETag"))
        self.assertEqual(owner.post("/api/logos/custom", data=upload(("only-bad.png", PAGE_AS_PNG)), content_type="multipart/form-data").status_code, 400)
        self.assertEqual(owner.post("/api/logos/custom", data={}, content_type="multipart/form-data").status_code, 400)

    def test_the_same_image_is_stored_once_and_a_different_one_gets_a_new_name(self):
        owner = self.owner()
        for _ in range(3):
            owner.post("/api/logos/custom", data=upload(("same.png", PNG)), content_type="multipart/form-data")
        self.assertEqual(len(owner.get("/api/logos").get_json()["custom"]), 1)
        owner.post("/api/logos/custom", data=upload(("same.png", PNG + b"1")), content_type="multipart/form-data")
        self.assertEqual(sorted(p.name for p in (HOME / "logos").glob("*.png")), ["same-2.png", "same.png"])

    def test_the_library_has_a_size_limit(self):
        owner = self.owner()
        with mock.patch.object(logos, "MAX_CUSTOM_LOGOS", 2):
            for index in range(3):
                reply = owner.post("/api/logos/custom", data=upload((f"n{index}.png", PNG + bytes([index]))), content_type="multipart/form-data")
        self.assertEqual(reply.status_code, 400)
        self.assertIn("full", reply.get_json()["error"])
        self.assertEqual(len(owner.get("/api/logos").get_json()["custom"]), 2)

    def test_imported_logos_can_be_deleted_but_bundled_ones_cannot(self):
        owner = self.owner()
        added = owner.post("/api/logos/custom", data=upload(("gone.png", PNG)), content_type="multipart/form-data").get_json()["added"][0]
        name = added["ref"].split("/")[1]
        self.assertEqual(owner.delete(f"/api/logos/custom/{name}").status_code, 200)
        self.assertEqual(status_of(owner, added["url"]), 404)
        self.assertEqual(owner.delete(f"/api/logos/custom/{name}").status_code, 404)
        self.assertEqual(owner.delete("/api/logos/custom/pixel-apple.png").status_code, 404)
        self.assertIsNotNone(logos.resolve_ref("bundled/pixel-apple.png"))

    def test_only_staff_who_may_create_servers_can_change_the_library(self):
        owner = self.owner()
        viewer = self.staff(owner, "viewer", permissions=["control", "files"])
        manager = self.staff(owner, "maker", "makerpass1", permissions=["manage"])
        self.assertEqual(viewer.get("/api/logos").status_code, 200)
        self.assertEqual(viewer.post("/api/logos/custom", data=upload(("a.png", PNG)), content_type="multipart/form-data").status_code, 403)
        self.assertEqual(viewer.post("/api/logos/pinterest", json={"url": "https://pin.it/x"}).status_code, 403)
        self.assertEqual(viewer.delete("/api/logos/custom/a.png").status_code, 403)
        self.assertEqual(manager.post("/api/logos/custom", data=upload(("a.png", PNG)), content_type="multipart/form-data").status_code, 200)


class LogosFolder(AppTestCase):
    """The owner can drop files into the logos folder by hand."""

    def test_the_folder_exists_with_a_note_and_the_note_is_not_a_logo(self):
        import shutil
        shutil.rmtree(HOME / "logos", ignore_errors=True)
        self.assertEqual(logos.list_logos()["custom"], [])
        self.assertTrue((HOME / "logos").is_dir())
        self.assertEqual([p.name for p in (HOME / "logos").iterdir()], ["Put your logo images here.txt"])

    def test_dropped_files_appear_whatever_they_are_called(self):
        owner = self.owner()
        folder = HOME / "logos"
        folder.mkdir(exist_ok=True)
        (folder / "My Server (final) #2.PNG").write_bytes(PNG)
        (folder / "holiday photo.jpeg").write_bytes(JPEG)
        (folder / "fake.png").write_bytes(PAGE_AS_PNG)
        (folder / "notes.txt").write_bytes(b"just some notes")
        (folder / ".hidden.png").write_bytes(PNG)
        (folder / "huge.png").write_bytes(PNG + b"0" * (logos.MAX_LOGO_BYTES + 1))
        listed = owner.get("/api/logos").get_json()["custom"]
        self.assertEqual(sorted(logo["name"] for logo in listed), ["My Server (final) #2", "holiday photo"])
        first = next(logo for logo in listed if logo["name"].startswith("My Server"))
        self.assertEqual(status_of(owner, first["url"]), 200, "names with spaces and # are served")
        self.assertNotIn(" ", first["url"])

    def test_gifs_and_files_with_no_image_extension_work_too(self):
        owner = self.owner()
        folder = HOME / "logos"
        folder.mkdir(exist_ok=True)
        (folder / "dancing.gif").write_bytes(GIF)
        page_title = "Minimal Abstract Mark _.  #logomark #minimalism" + "x" * 60 + "\u2026.  #logomark #minimalism\u2026"  # what saving a Pinterest page title leaves behind
        (folder / page_title).write_bytes(JPEG)
        listed = owner.get("/api/logos").get_json()["custom"]
        self.assertEqual(len(listed), 2, [logo["name"] for logo in listed])
        for logo in listed:
            reply = owner.get(logo["url"])
            reply.close()
            self.assertEqual(reply.status_code, 200, logo["name"])
            self.assertTrue(reply.mimetype in ("image/gif", "image/jpeg"), reply.mimetype)
        make_server()
        self.assertEqual(owner.post("/api/server/alpha_1/logo", json={"logo": next(l["ref"] for l in listed if l["name"] == "dancing")}).status_code, 200)
        self.assertTrue((HOME / "servers" / "alpha_1" / "manager_logo.gif").exists())

    def test_a_dropped_file_can_be_used_for_a_server_and_removed(self):
        folder = make_server()
        owner = self.owner()
        (HOME / "logos").mkdir(exist_ok=True)
        (HOME / "logos" / "Team Logo.jpg").write_bytes(JPEG)
        reply = owner.post("/api/server/alpha_1/logo", json={"logo": "custom/Team Logo.jpg"})
        self.assertEqual(reply.status_code, 200, reply.get_json())
        self.assertEqual((folder / "manager_logo.jpg").read_bytes(), JPEG)
        self.assertEqual(owner.delete("/api/logos/custom/Team%20Logo.jpg").status_code, 200)
        self.assertFalse((HOME / "logos" / "Team Logo.jpg").exists())
        self.assertTrue((folder / "manager_logo.jpg").exists(), "the server keeps its own copy")

    def test_names_cannot_reach_outside_the_folder(self):
        (HOME / "logos").mkdir(exist_ok=True)
        (HOME / "outside.png").write_bytes(PNG)
        (HOME / "logos" / "inside.png").write_bytes(PNG)
        for bad in ("custom/../outside.png", "custom/..\\outside.png", "custom/sub/inside.png", "custom//inside.png", "custom/.png", "custom/inside.png\x00", "custom/" + "x" * 300):
            self.assertIsNone(logos.resolve_ref(bad), bad)
        self.assertIsNotNone(logos.resolve_ref("custom/inside.png"))


class ServerLogos(AppTestCase):
    def test_a_library_logo_becomes_the_servers_own_copy(self):
        folder = make_server()
        owner = self.owner()
        self.assertEqual(owner.post("/api/server/alpha_1/logo", json={"logo": "bundled/pixel-apple.png"}).status_code, 200)
        self.assertEqual((folder / "manager_logo.png").read_bytes(), logos.resolve_ref("bundled/pixel-apple.png").read_bytes())
        meta = json.loads((folder / "manager_meta.json").read_text())
        self.assertEqual(meta["logo"]["file"], "manager_logo.png")
        self.assertGreater(meta["logo"]["rev"], 0)
        self.assertEqual(status_of(owner, "/api/server/alpha_1/logo/manager_logo.png"), 200)

    def test_changing_the_logo_replaces_the_old_file(self):
        folder = make_server()
        owner = self.owner()
        owner.post("/api/server/alpha_1/logo", data={"logo": (io.BytesIO(JPEG), "x.jpg")}, content_type="multipart/form-data")
        self.assertTrue((folder / "manager_logo.jpg").exists())
        owner.post("/api/server/alpha_1/logo", json={"logo": "bundled/pixel-heart.png"})
        self.assertEqual(sorted(p.name for p in folder.glob("manager_logo.*")), ["manager_logo.png"])

    def test_unknown_logos_and_fake_images_are_refused(self):
        folder = make_server()
        owner = self.owner()
        self.assertEqual(owner.post("/api/server/alpha_1/logo", json={"logo": "custom/missing.png"}).status_code, 400)
        self.assertEqual(owner.post("/api/server/alpha_1/logo", json={"logo": "bundled/../app.py"}).status_code, 400)
        fake = owner.post("/api/server/alpha_1/logo", data={"logo": (io.BytesIO(PAGE_AS_PNG), "x.png")}, content_type="multipart/form-data")
        self.assertEqual(fake.status_code, 400)
        self.assertEqual(list(folder.glob("manager_logo.*")), [])
        self.assertEqual(owner.post("/api/server/nope/logo", json={"logo": "bundled/pixel-apple.png"}).status_code, 404)

    def test_a_new_server_can_be_created_with_a_library_logo(self):
        owner = self.owner()
        with mock.patch.object(routes_servers, "download_paper", return_value=(b"jar", "paper.jar")):
            reply = owner.post("/api/create", json={"name": "Pretty", "version": "1.21.1", "port": 25599, "accept_eula": True,
                                                   "logo": {"mark": "PR", "style": "avatar-red", "library": "bundled/pixel-crown.png"}})
        body = reply.get_json()
        self.assertTrue(body["ok"], body)
        folder = HOME / "servers" / body["id"]
        self.assertEqual((folder / "manager_logo.png").read_bytes(), logos.resolve_ref("bundled/pixel-crown.png").read_bytes())
        self.assertEqual(body["meta"]["logo"]["file"], "manager_logo.png")
        self.assertEqual(body["meta"]["logo"]["style"], "avatar-red", "the generated fallback stays in the metadata")

    def test_a_bad_library_reference_at_creation_just_keeps_the_generated_logo(self):
        owner = self.owner()
        with mock.patch.object(routes_servers, "download_paper", return_value=(b"jar", "paper.jar")):
            body = owner.post("/api/create", json={"name": "Plain", "version": "1.21.1", "port": 25598, "accept_eula": True,
                                                  "logo": {"library": "bundled/../../app.py"}}).get_json()
        self.assertTrue(body["ok"], body)
        self.assertNotIn("file", body["meta"]["logo"])


class Pinterest(AppTestCase):
    PIN = "https://www.pinterest.com/pin/586734657738896202/"
    IMAGE = "https://i.pinimg.com/736x/ac/eb/b0/acebb0df.jpg"

    def run_import(self, routes, link):
        web = FakeWeb(routes)
        if getattr(self, "signed_in", None) is None:
            self.signed_in = self.owner()
        with mock.patch.object(logos.requests, "get", web.get):
            reply = self.signed_in.post("/api/logos/pinterest", json={"url": link})
        return reply, web

    def test_a_pin_link_becomes_a_library_logo(self):
        reply, web = self.run_import({self.PIN: FakeResponse(pin_page()), self.IMAGE: FakeResponse(JPEG)}, self.PIN)
        body = reply.get_json()
        self.assertEqual(reply.status_code, 200, body)
        self.assertEqual(body["logo"]["category"], "mine")
        self.assertTrue(body["logo"]["ref"].startswith("custom/pin-"), body["logo"]["ref"])
        self.assertEqual(logos.resolve_ref(body["logo"]["ref"]).read_bytes(), JPEG)
        self.assertEqual(web.asked, [self.PIN, self.IMAGE])

    def test_the_meta_tag_may_list_content_before_property(self):
        for order in ("content-first", "property-first"):
            reply, _ = self.run_import({self.PIN: FakeResponse(pin_page(order=order)), self.IMAGE: FakeResponse(JPEG)}, self.PIN)
            self.assertEqual(reply.status_code, 200, order)

    def test_links_without_https_and_short_links_work(self):
        short = "https://pin.it/4abc"
        routes = {short: FakeResponse(status=302, location=self.PIN), self.PIN: FakeResponse(pin_page()), self.IMAGE: FakeResponse(JPEG)}
        reply, web = self.run_import(routes, "pin.it/4abc")
        self.assertEqual(reply.status_code, 200, reply.get_json())
        self.assertEqual(web.asked, [short, self.PIN, self.IMAGE])
        reply, web = self.run_import({self.PIN: FakeResponse(pin_page()), self.IMAGE: FakeResponse(JPEG)}, "http://www.pinterest.com/pin/586734657738896202/")
        self.assertEqual(reply.status_code, 200)
        self.assertTrue(web.asked[0].startswith("https://"))

    def test_a_direct_pinimg_link_is_downloaded_without_a_page(self):
        reply, web = self.run_import({self.IMAGE: FakeResponse(PNG)}, self.IMAGE)
        self.assertEqual(reply.status_code, 200, reply.get_json())
        self.assertEqual(web.asked, [self.IMAGE])

    def test_only_pinterest_pins_are_fetched(self):
        for link in ("https://evil.example/pin/123456789/", "https://notpinterest.com/pin/123456789/", "https://pinterest.com.evil.example/pin/1234567/",
                     "https://www.pinterest.com/someuser/boards/", "https://www.pinterest.com/", "https://127.0.0.1/pin/123456789/", "file:///etc/passwd",
                     "https://pin.it.evil.example/x", "", "   "):
            reply, web = self.run_import({}, link)
            self.assertEqual(reply.status_code, 400, link)
            self.assertEqual(web.asked, [], f"nothing may be requested for {link!r}")

    def test_a_redirect_to_another_site_is_not_followed(self):
        short = "https://pin.it/4abc"
        reply, web = self.run_import({short: FakeResponse(status=302, location="https://evil.example/pin/123456789/")}, short)
        self.assertEqual(reply.status_code, 400)
        self.assertEqual(web.asked, [short])
        loop = {short: FakeResponse(status=302, location=short)}
        reply, _ = self.run_import(loop, short)
        self.assertIn("redirected too many", reply.get_json()["error"])

    def test_the_image_must_come_from_pinimg(self):
        reply, web = self.run_import({self.PIN: FakeResponse(pin_page(image="https://evil.example/x.jpg"))}, self.PIN)
        self.assertEqual(reply.status_code, 400)
        self.assertEqual(web.asked, [self.PIN])
        reply, _ = self.run_import({self.PIN: FakeResponse(b"<html><head></head></html>")}, self.PIN)
        self.assertIn("did not show an image", reply.get_json()["error"])

    def test_a_pin_that_is_not_an_image_is_refused(self):
        reply, _ = self.run_import({self.PIN: FakeResponse(pin_page()), self.IMAGE: FakeResponse(PAGE_AS_PNG)}, self.PIN)
        self.assertEqual(reply.status_code, 400)
        self.assertEqual(logos.list_logos()["custom"], [])

    def test_oversized_downloads_are_cut_off(self):
        with mock.patch.object(logos, "MAX_LOGO_BYTES", 100):
            reply, _ = self.run_import({self.PIN: FakeResponse(pin_page()), self.IMAGE: FakeResponse(PNG + b"0" * 500)}, self.PIN)
        self.assertEqual(reply.status_code, 400)
        self.assertEqual(logos.list_logos()["custom"], [])

    def test_network_failures_are_reported_plainly(self):
        reply, _ = self.run_import({self.PIN: requests.ConnectionError("down")}, self.PIN)
        self.assertEqual(reply.status_code, 502)
        self.assertIn("Could not reach Pinterest", reply.get_json()["error"])
        reply, _ = self.run_import({self.PIN: FakeResponse(status=404)}, self.PIN)
        self.assertEqual(reply.status_code, 502)


class RepositoryLibraries(AppTestCase):
    """The pictures that ship with the manager, and the folders GitHub contributors add to."""

    def test_bundled_banners_ship_with_the_app_and_the_animated_ones_loop(self):
        banners = logos.list_banners()["bundled"]
        self.assertGreaterEqual(len(banners), 12)
        self.assertTrue({"animated", "scenic", "pattern"} <= {banner["category"] for banner in banners})
        self.assertEqual(len({banner["ref"] for banner in banners}), len(banners))
        animated = 0
        for banner in banners:
            data = logos.resolve_banner(banner["ref"]).read_bytes()
            self.assertLess(len(data), 700 * 1024, banner["ref"])
            if banner["ref"].endswith(".gif"):
                animated += 1
                self.assertEqual(logos.image_extension(data), ".gif")
                self.assertEqual(struct.unpack("<HH", data[6:10]), (640, 192), banner["ref"])
                self.assertIn(b"NETSCAPE2.0", data[:1024], f"{banner['ref']} must loop")
            else:
                self.assertEqual(logos.image_extension(data), ".png")
                self.assertEqual(struct.unpack(">II", data[16:24]), (640, 192), banner["ref"])
        self.assertGreaterEqual(animated, 6)

    def test_a_bundled_banner_can_be_put_on_a_server(self):
        folder = make_server()
        owner = self.owner()
        gif = next(b for b in owner.get("/api/banners").get_json()["bundled"] if b["ref"].endswith(".gif"))
        self.assertEqual(status_of(owner, gif["url"]), 200)
        self.assertEqual(owner.post("/api/server/alpha_1/banner", json={"banner": gif["ref"]}).status_code, 200)
        self.assertEqual((folder / "manager_banner.gif").read_bytes(), logos.resolve_banner(gif["ref"]).read_bytes())
        self.assertEqual(owner.post("/api/server/alpha_1/banner", json={"banner": "bundled/../../app.py"}).status_code, 400)

    def test_contributed_files_appear_under_community_and_can_be_used(self):
        folder = make_server()
        owner = self.owner()
        logo_dir, banner_dir = HOME / "community_logos", HOME / "community_banners"
        logo_dir.mkdir(exist_ok=True)
        banner_dir.mkdir(exist_ok=True)
        (logo_dir / "Team Rocket.gif").write_bytes(GIF)
        (logo_dir / "README.md").write_text("not an image")
        (logo_dir / ".gitkeep").write_text("")
        (banner_dir / "Big raid.png").write_bytes(PNG)
        with mock.patch.object(logos.COMMUNITY_LOGOS, "folder", logo_dir), mock.patch.object(logos.COMMUNITY_BANNERS, "folder", banner_dir):
            community = owner.get("/api/logos").get_json()["community"]
            self.assertEqual([(c["name"], c["category"], c["ref"]) for c in community], [("Team Rocket", "community", "community/Team Rocket.gif")])
            self.assertIn("/static/community/logos/Team%20Rocket.gif", community[0]["url"])
            self.assertEqual([c["ref"] for c in owner.get("/api/banners").get_json()["community"]], ["community/Big raid.png"])
            self.assertEqual(owner.post("/api/server/alpha_1/logo", json={"logo": "community/Team Rocket.gif"}).status_code, 200)
            self.assertEqual(owner.post("/api/server/alpha_1/banner", json={"banner": "community/Big raid.png"}).status_code, 200)
            self.assertEqual((folder / "manager_logo.gif").read_bytes(), GIF)
            self.assertEqual((folder / "manager_banner.png").read_bytes(), PNG)
            self.assertEqual(owner.delete("/api/logos/custom/Team%20Rocket.gif").status_code, 404, "community files cannot be deleted through the panel")
            self.assertIsNone(logos.resolve_ref("community/../x.png"))
            self.assertIsNone(logos.resolve_banner("community/README.md"))

    def test_every_file_in_the_repository_community_folders_is_usable(self):
        """Guards pull requests: a contributed file that the picker would silently skip fails here instead."""
        for gallery in (logos.COMMUNITY_LOGOS, logos.COMMUNITY_BANNERS):
            if not gallery.folder.is_dir():
                continue
            for path in sorted(gallery.folder.iterdir()):
                if path.name in ("README.md", ".gitkeep"):
                    continue
                self.assertTrue(logos.custom_name_ok(path.name), f"{path.name}: use a plain file name (no hidden names or path characters)")
                self.assertTrue(gallery.is_image(path), f"{path.name}: not a PNG, JPG, GIF or WebP image within the size limit")

    def test_the_add_community_tool_checks_content_and_cleans_names(self):
        import importlib.util
        spec = importlib.util.spec_from_file_location("add_community", logos.BASE_DIR / "tools" / "add_community.py")
        tool = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(tool)
        target = HOME / "tool_logos"
        source = HOME / "tool_source"
        source.mkdir(exist_ok=True)
        (source / "Cool Logo (final)!!.png").write_bytes(PNG)
        (source / "fake.png").write_bytes(PAGE_AS_PNG)
        (source / "raw-no-extension").write_bytes(GIF)
        with mock.patch.dict(tool.FOLDERS, {"logo": (target, logos.MAX_LOGO_BYTES)}), mock.patch("builtins.print"):
            status = tool.main(["add_community.py", "logo", str(source / "Cool Logo (final)!!.png"), str(source / "fake.png"), str(source / "raw-no-extension"), str(source / "missing.png")])
            again = tool.main(["add_community.py", "logo", str(source / "Cool Logo (final)!!.png")])
        self.assertEqual(status, 1, "bad files make the tool report failure")
        self.assertEqual(again, 0)
        self.assertEqual(sorted(p.name for p in target.iterdir()), ["Cool Logo final.png", "raw-no-extension.gif"])
        with mock.patch("builtins.print"):
            self.assertEqual(tool.main(["add_community.py", "poster", "x.png"]), 2)

    def test_a_picture_the_owner_already_has_is_not_shown_twice(self):
        owner = self.owner()
        (HOME / "logos").mkdir(exist_ok=True)
        community_dir = HOME / "community_dupes"
        community_dir.mkdir(exist_ok=True)
        (HOME / "logos" / "My copy.gif").write_bytes(GIF)
        (community_dir / "Shared copy.gif").write_bytes(GIF)
        (community_dir / "Different.png").write_bytes(PNG)
        with mock.patch.object(logos.COMMUNITY_LOGOS, "folder", community_dir):
            data = owner.get("/api/logos").get_json()
            self.assertEqual([c["name"] for c in data["custom"]], ["My copy"])
            self.assertEqual([c["name"] for c in data["community"]], ["Different"], "the identical community file is hidden")
            (HOME / "logos" / "My copy.gif").unlink()
            again = owner.get("/api/logos").get_json()
            self.assertEqual(sorted(c["name"] for c in again["community"]), ["Different", "Shared copy"], "without the owner's copy it shows again")

