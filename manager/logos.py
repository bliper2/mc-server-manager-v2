"""Server logos and banners: the bundled logo library, images the owner imports (files or a Pinterest pin) and putting
one on a server.

A server keeps its own copy of the chosen image (manager_logo.<ext> or manager_banner.<ext> in its folder), so deleting
a library image or cloning and backing up a server never loses the picture.

Three sources feed the picker: artwork that ships with the manager (static/logos and static/banners, drawn by the scripts
in tools/), images contributed through GitHub (static/community/logos and static/community/banners) and the owner's own
images. The owner's live in two folders next to the manager, "logos" and "banners". Any PNG, JPG, GIF or WebP dropped
in there shows up in the picker, whatever it is called (even with no file extension, as saved pages and "Save image as"
often leave it); imports from the panel and from Pinterest are saved into the same folders. GIFs stay animated."""

import hashlib
import html
import re
import time
from functools import lru_cache
from pathlib import Path
from urllib.parse import quote, urljoin, urlparse

import requests
from flask import abort, jsonify, request, send_from_directory

from . import app
from .config import ASSET_VERSION, BASE_DIR, DATA_DIR, HEADERS
from .store import get_server_path, load_meta, save_meta

BUNDLED_DIR = BASE_DIR / "static" / "logos"
BUNDLED_BANNERS = BASE_DIR / "static" / "banners"
COMMUNITY_LOGO_DIR = BASE_DIR / "static" / "community" / "logos"
COMMUNITY_BANNER_DIR = BASE_DIR / "static" / "community" / "banners"
CUSTOM_DIR = DATA_DIR / "logos"
BANNER_DIR = DATA_DIR / "banners"
MAX_LOGO_BYTES = 8 * 1024 * 1024
MAX_BANNER_BYTES = 16 * 1024 * 1024  # animated banners are big
MAX_CUSTOM_LOGOS = 300  # per folder
MAX_UPLOAD_FILES = 40
MAX_PIN_PAGE_BYTES = 4 * 1024 * 1024  # a pin page is about 1.2 MB and its og:image tag sits well past the first megabyte
NAME_PATTERN = re.compile(r"^[a-z0-9][a-z0-9_-]{0,80}\.(png|jpg|webp|gif)$")  # bundled files are named by us
IMAGE_TYPES = {".png": "image/png", ".jpg": "image/jpeg", ".gif": "image/gif", ".webp": "image/webp"}
CATEGORY_ORDER = ("animated", "combat", "royal", "nature", "build", "pixel", "scenic", "pattern")
PLAIN_LABEL_CATEGORIES = {"pixel", "animated", "scenic", "pattern"}  # <category>-<name>: the label is just the name
PIN_IMAGE_HOST = "i.pinimg.com"
SERVER_IMAGES = {"logo": ("manager_logo", "logo"), "banner": ("manager_banner", "banner")}  # kind -> (file stem in the server folder, metadata key)


class LogoError(Exception):
    """A problem with a logo or banner, phrased for the person choosing it."""


def image_extension(data: bytes):
    """The file type from the bytes themselves, so a page renamed to .png is never stored as an image."""
    if data.startswith(b"\x89PNG\r\n\x1a\n"):
        return ".png"
    if data.startswith(b"\xff\xd8\xff"):
        return ".jpg"
    if data[:6] in (b"GIF87a", b"GIF89a"):
        return ".gif"
    if data[:4] == b"RIFF" and data[8:12] == b"WEBP":
        return ".webp"
    return None


def custom_name_ok(name) -> bool:
    """Files the owner dropped in can have any name (the content decides whether it is an image), but never a path or a hidden name."""
    return (isinstance(name, str) and 0 < len(name) <= 255 and name == Path(name).name and "/" not in name and "\\" not in name
            and not name.startswith(".") and not any(ord(char) < 32 for char in name))


class Gallery:
    """The owner's own images of one kind: a folder, a size limit and where they are served from."""

    def __init__(self, kind: str, folder: Path, limit, url_base: str):
        self.kind, self.folder, self.limit, self.url_base = kind, folder, limit, url_base
        self.note = (f"Put your server {kind} images (PNG, JPG, GIF or WebP) in this folder.\r\n"
                     f"They appear under \"My {kind}s\" when you choose a {kind} in MC Server Manager.\r\n")

    def ensure(self):
        """Creates the folder, with a note explaining it, so the owner has somewhere obvious to drop images."""
        if self.folder.is_dir():
            return
        self.folder.mkdir(parents=True, exist_ok=True)
        (self.folder / f"Put your {self.kind} images here.txt").write_text(self.note, encoding="utf-8")

    def is_image(self, path: Path) -> bool:
        """A real image of a sensible size, judged by its first bytes, not by its name."""
        try:
            if not path.is_file() or path.stat().st_size > self.limit():
                return False
            with path.open("rb") as handle:
                return image_extension(handle.read(16)) is not None
        except OSError:
            return False

    def resolve(self, name):
        if not custom_name_ok(name):
            return None
        path = self.folder / name
        return path if self.is_image(path) else None

    def describe(self, path: Path) -> dict:
        base = path.stem if path.suffix.lower() in IMAGE_TYPES or path.suffix.lower() == ".jpeg" else path.name  # a long page title is not an extension
        return {"ref": f"custom/{path.name}", "name": re.sub(r"[-_]+", " ", base).strip() or path.name, "category": "mine",
                "url": f"{self.url_base}{quote(path.name)}"}

    def paths(self) -> list:
        self.ensure()
        return sorted((p for p in self.folder.iterdir() if custom_name_ok(p.name) and self.is_image(p)), key=lambda p: p.stat().st_mtime, reverse=True)

    def listing(self) -> list:
        return [self.describe(p) for p in self.paths()]

    def add(self, data: bytes, label: str) -> dict:
        if len(data) > self.limit():
            raise LogoError(f"That image is larger than {self.limit() // (1024 * 1024)} MB")
        extension = image_extension(data)
        if not extension:
            raise LogoError("Use a PNG, JPG, GIF or WebP image")
        self.ensure()
        stem = re.sub(r"[^\w \-]+", "", label).strip()[:60] or self.kind
        path = self.folder / f"{stem}{extension}"
        for number in range(2, 100):
            if not path.exists():
                break
            if path.read_bytes() == data:
                return self.describe(path)  # the same picture is already in the folder
            path = self.folder / f"{stem}-{number}{extension}"
        else:
            raise LogoError("There are too many images with that name. Rename the file and try again")
        if sum(1 for _ in self.folder.iterdir()) >= MAX_CUSTOM_LOGOS:
            raise LogoError(f"The {self.folder.name} folder is full ({MAX_CUSTOM_LOGOS} files). Delete some first")
        path.write_bytes(data)
        return self.describe(path)


@lru_cache(maxsize=2048)
def _digest(path: str, mtime_ns: int, size: int) -> str:
    return hashlib.sha1(Path(path).read_bytes()).hexdigest()


def file_digest(path: Path) -> str:
    info = path.stat()
    return _digest(str(path), info.st_mtime_ns, info.st_size)


class CommunityGallery(Gallery):
    """Images contributed through GitHub. They live in the repository (served as static files), so the manager only reads them."""

    def ensure(self):
        pass

    def describe(self, path: Path) -> dict:
        return {"ref": f"community/{path.name}", "name": re.sub(r"[-_]+", " ", path.stem).strip() or path.name, "category": "community",
                "url": f"/static/community/{self.kind}s/{quote(path.name)}?v={ASSET_VERSION}"}


LOGOS = Gallery("logo", CUSTOM_DIR, lambda: MAX_LOGO_BYTES, "/api/logos/custom/")
BANNERS = Gallery("banner", BANNER_DIR, lambda: MAX_BANNER_BYTES, "/api/banners/custom/")
COMMUNITY_LOGOS = CommunityGallery("logo", COMMUNITY_LOGO_DIR, lambda: MAX_LOGO_BYTES, "")
COMMUNITY_BANNERS = CommunityGallery("banner", COMMUNITY_BANNER_DIR, lambda: MAX_BANNER_BYTES, "")


def ensure_folder():
    LOGOS.ensure()
    BANNERS.ensure()


def is_logo_file(path: Path) -> bool:
    return LOGOS.is_image(path)


def resolve_ref(ref):
    """The file a logo reference such as "bundled/combat-sword-gold.png" or "custom/My Logo.png" names, or None."""
    kind, _, name = str(ref or "").partition("/")
    if kind == "bundled" and NAME_PATTERN.match(name):
        path = BUNDLED_DIR / name
        return path if path.is_file() else None
    if kind == "community":
        return COMMUNITY_LOGOS.resolve(name)
    if kind == "custom":
        return LOGOS.resolve(name)
    return None


def resolve_banner(ref):
    kind, _, name = str(ref or "").partition("/")
    if kind == "bundled" and NAME_PATTERN.match(name):
        path = BUNDLED_BANNERS / name
        return path if path.is_file() else None
    if kind == "community":
        return COMMUNITY_BANNERS.resolve(name)
    return BANNERS.resolve(name) if kind == "custom" else None


def describe_bundled(path: Path, folder: str = "logos") -> dict:
    parts = path.stem.split("-")
    category = parts[0]
    if category in PLAIN_LABEL_CATEGORIES or len(parts) < 3:
        label = " ".join(parts[1:]).title() or path.stem.title()
    else:
        label = f"{parts[1].title()} ({parts[2]})"  # emblems: <category>-<shape of the glyph>-<colour>
    return {"ref": f"bundled/{path.name}", "name": label, "category": category, "url": f"/static/{folder}/{path.name}?v={ASSET_VERSION}"}


def list_bundled(folder: Path, name: str) -> list:
    def order(item):
        category = item["category"]
        return (CATEGORY_ORDER.index(category) if category in CATEGORY_ORDER else len(CATEGORY_ORDER), item["name"])

    if not folder.is_dir():
        return []
    return sorted((describe_bundled(p, name) for p in folder.iterdir() if NAME_PATTERN.match(p.name)), key=order)


def with_community(own: Gallery, community: Gallery) -> tuple:
    """(the owner's images, community images). A community picture the owner already has a copy of is left out, so an
    install that holds the same file in both places shows it once, as the owner's own."""
    mine = own.paths()
    owned = {file_digest(path) for path in mine}
    shared = [community.describe(path) for path in community.paths() if file_digest(path) not in owned]
    return [own.describe(path) for path in mine], shared


def list_logos() -> dict:
    custom, community = with_community(LOGOS, COMMUNITY_LOGOS)
    return {"bundled": list_bundled(BUNDLED_DIR, "logos"), "community": community, "custom": custom}


def list_banners() -> dict:
    custom, community = with_community(BANNERS, COMMUNITY_BANNERS)
    return {"bundled": list_bundled(BUNDLED_BANNERS, "banners"), "community": community, "custom": custom}


def add_custom(data: bytes, label: str) -> dict:
    return LOGOS.add(data, label)


def install_server_image(server_id: str, data: bytes, kind: str) -> str:
    """Stores `data` as the server's logo or banner and returns the file name. Replaces any earlier one."""
    limit = MAX_LOGO_BYTES if kind == "logo" else MAX_BANNER_BYTES
    if len(data) > limit:
        raise LogoError(f"That image is larger than {limit // (1024 * 1024)} MB")
    extension = image_extension(data)
    if not extension:
        raise LogoError("Use a PNG, JPG, GIF or WebP image")
    stem, key = SERVER_IMAGES[kind]
    server_path = get_server_path(server_id)
    for old in server_path.glob(f"{stem}.*"):
        old.unlink(missing_ok=True)
    filename = f"{stem}{extension}"
    (server_path / filename).write_bytes(data)
    meta = load_meta(server_id)
    entry = meta.get(key) if isinstance(meta.get(key), dict) else {}
    entry.update(file=filename, rev=time.time_ns() // 1_000_000)  # rev lets the browser tell the new picture from the cached one
    meta[key] = entry
    save_meta(server_id, meta)
    return filename


def install_server_logo(server_id: str, data: bytes) -> str:
    return install_server_image(server_id, data, "logo")


def install_server_banner(server_id: str, data: bytes) -> str:
    return install_server_image(server_id, data, "banner")


def remove_server_banner(server_id: str):
    server_path = get_server_path(server_id)
    for old in server_path.glob("manager_banner.*"):
        old.unlink(missing_ok=True)
    meta = load_meta(server_id)
    if meta.pop("banner", None) is not None:
        save_meta(server_id, meta)


# ---------------------------------------------------------------- Pinterest
def pin_page_allowed(url: str) -> bool:
    try:
        parsed = urlparse(url)
    except ValueError:
        return False
    host = parsed.hostname or ""
    if parsed.scheme != "https":
        return False
    if host == "pin.it":
        return True  # Pinterest's short links; they redirect to the pin
    return (host == "pinterest.com" or host.endswith(".pinterest.com")) and parsed.path.startswith("/pin/")


def pin_image_allowed(url: str) -> bool:
    try:
        parsed = urlparse(url)
    except ValueError:
        return False
    return parsed.scheme == "https" and parsed.hostname == PIN_IMAGE_HOST


def download(url: str, allowed, limit: int, done=None) -> bytes:
    """GET that follows redirects by hand, so every address on the way is checked, and stops reading at `limit` bytes
    (or as soon as done(latest bytes) is true, which is how a pin page is cut off once its image tag has arrived)."""
    for _ in range(5):
        if not allowed(url):
            raise LogoError("That link does not lead to a Pinterest pin")
        with requests.get(url, headers=HEADERS, stream=True, timeout=(10, 20), allow_redirects=False) as response:
            if response.is_redirect:
                url = urljoin(url, response.headers.get("location", ""))
                continue
            response.raise_for_status()
            body = bytearray()
            for chunk in response.iter_content(65536):
                body += chunk
                if len(body) > limit:
                    raise LogoError("Pinterest sent more data than expected")
                if done and done(bytes(body[-131072:])):
                    break
            return bytes(body)
    raise LogoError("Pinterest redirected too many times")


def og_image(page: str):
    for tag in re.findall(r"<meta\b[^>]*>", page, re.I):
        if re.search(r"""(?:property|name)\s*=\s*["']og:image["']""", tag, re.I):
            content = re.search(r"""content\s*=\s*["']([^"']+)["']""", tag, re.I)
            if content:
                return html.unescape(content.group(1))
    return None


def fetch_pinterest_image(link: str, limit=None):
    """Returns (image bytes, a name for it) for a pin link or a direct i.pinimg.com image link."""
    limit = limit or MAX_LOGO_BYTES
    link = link.strip()
    if not link:
        raise LogoError("Paste a link to a Pinterest pin")
    if "://" not in link:
        link = "https://" + link
    link = re.sub(r"^http://", "https://", link, flags=re.I)
    if pin_image_allowed(link):
        image_url, label = link, "pinterest"
    else:
        page = download(link, pin_page_allowed, MAX_PIN_PAGE_BYTES, done=lambda tail: og_image(tail.decode("utf-8", "ignore")) is not None).decode("utf-8", "replace")
        image_url = og_image(page)
        if not image_url:
            raise LogoError("Pinterest did not show an image for that pin. It may be private or removed")
        pin_id = re.search(r"(\d{6,})/?$", urlparse(link).path)
        label = f"pin-{pin_id.group(1)[-8:]}" if pin_id else "pin"
    data = download(image_url, pin_image_allowed, limit)
    if not image_extension(data):
        raise LogoError("That pin is not a PNG, JPG, GIF or WebP image")
    return data, label


# ---------------------------------------------------------------- routes
ensure_folder()


def serve_custom(gallery: Gallery, name: str):
    path = gallery.resolve(name)
    if path is None:
        abort(404)
    with path.open("rb") as handle:
        mimetype = IMAGE_TYPES[image_extension(handle.read(16))]  # from the content, since the file name may say nothing
    return send_from_directory(gallery.folder, path.name, mimetype=mimetype)


def import_uploads(gallery: Gallery):
    uploads = request.files.getlist("files")[:MAX_UPLOAD_FILES]
    if not uploads:
        return jsonify({"ok": False, "error": "Choose one or more image files"}), 400
    added, skipped = [], []
    for upload in uploads:
        try:
            added.append(gallery.add(upload.stream.read(gallery.limit() + 1), Path(upload.filename or gallery.kind).stem))
        except LogoError as exc:
            skipped.append(f"{upload.filename or 'file'}: {exc}")
    return jsonify({"ok": bool(added), "added": added, "skipped": skipped, "error": "; ".join(skipped[:3])}), 200 if added else 400


def delete_custom(gallery: Gallery, name: str):
    path = gallery.resolve(name)
    if path is None:
        return jsonify({"ok": False, "error": f"That {gallery.kind} no longer exists"}), 404
    path.unlink(missing_ok=True)
    return jsonify({"ok": True})


def import_pin(gallery: Gallery):
    link = str((request.get_json(silent=True) or {}).get("url") or "")
    try:
        data, label = fetch_pinterest_image(link, gallery.limit())
        return jsonify({"ok": True, gallery.kind: gallery.add(data, label)})
    except LogoError as exc:
        return jsonify({"ok": False, "error": str(exc)}), 400
    except requests.RequestException:
        return jsonify({"ok": False, "error": "Could not reach Pinterest. Check the link and your connection, then try again"}), 502


@app.route("/api/logos")
def api_logos():
    return jsonify({"ok": True, **list_logos()})


@app.route("/api/logos/custom/<name>")
def api_logo_custom_file(name):
    return serve_custom(LOGOS, name)


@app.route("/api/logos/custom", methods=["POST"])
def api_logos_import():
    return import_uploads(LOGOS)


@app.route("/api/logos/custom/<name>", methods=["DELETE"])
def api_logos_delete(name):
    return delete_custom(LOGOS, name)


@app.route("/api/logos/pinterest", methods=["POST"])
def api_logos_pinterest():
    return import_pin(LOGOS)


@app.route("/api/banners")
def api_banners():
    return jsonify({"ok": True, **list_banners()})


@app.route("/api/banners/custom/<name>")
def api_banner_custom_file(name):
    return serve_custom(BANNERS, name)


@app.route("/api/banners/custom", methods=["POST"])
def api_banners_import():
    return import_uploads(BANNERS)


@app.route("/api/banners/custom/<name>", methods=["DELETE"])
def api_banners_delete(name):
    return delete_custom(BANNERS, name)


@app.route("/api/banners/pinterest", methods=["POST"])
def api_banners_pinterest():
    return import_pin(BANNERS)


@app.route("/api/server/<sid>/banner", methods=["POST"])
def api_server_banner(sid):
    if not get_server_path(sid).is_dir():
        return jsonify({"ok": False, "error": "Server not found"}), 404
    if request.is_json:  # a banner picked from the library
        chosen = resolve_banner((request.get_json(silent=True) or {}).get("banner"))
        if chosen is None:
            return jsonify({"ok": False, "error": "That banner is not in the banners folder any more"}), 400
        data = chosen.read_bytes()
    else:
        upload = request.files.get("banner")
        if not upload or not upload.filename:
            return jsonify({"ok": False, "error": "Choose a banner image"}), 400
        data = upload.stream.read(MAX_BANNER_BYTES + 1)
    try:
        filename = install_server_banner(sid, data)
    except LogoError as exc:
        return jsonify({"ok": False, "error": str(exc)}), 400
    return jsonify({"ok": True, "url": f"/api/server/{sid}/banner/{filename}"})


@app.route("/api/server/<sid>/banner", methods=["DELETE"])
def api_server_banner_remove(sid):
    if not get_server_path(sid).is_dir():
        return jsonify({"ok": False, "error": "Server not found"}), 404
    remove_server_banner(sid)
    return jsonify({"ok": True})


@app.route("/api/server/<sid>/banner/<filename>")
def api_server_banner_file(sid, filename):
    if Path(filename).name != filename or not filename.startswith("manager_banner."):
        abort(404)
    return send_from_directory(get_server_path(sid), filename)
