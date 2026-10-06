"""Server logos: the bundled library, logos the owner imports (files or a Pinterest pin) and putting one on a server.

A server keeps its own copy of the chosen image (manager_logo.<ext> in its folder), so deleting a library logo or
cloning and backing up a server never loses the picture."""

import hashlib
import html
import re
import time
from pathlib import Path
from urllib.parse import urljoin, urlparse

import requests
from flask import abort, jsonify, request, send_from_directory

from . import app
from .config import ASSET_VERSION, BASE_DIR, DATA_DIR, HEADERS
from .store import get_server_path, load_meta, save_meta

BUNDLED_DIR = BASE_DIR / "static" / "logos"
CUSTOM_DIR = DATA_DIR / "logos"
MAX_LOGO_BYTES = 4 * 1024 * 1024
MAX_CUSTOM_LOGOS = 300
MAX_UPLOAD_FILES = 40
MAX_PIN_PAGE_BYTES = 4 * 1024 * 1024  # a pin page is about 1.2 MB and its og:image tag sits well past the first megabyte
NAME_PATTERN = re.compile(r"^[a-z0-9][a-z0-9_-]{0,80}\.(png|jpg|webp)$")
CATEGORY_ORDER = ("combat", "royal", "nature", "build", "pixel")
PIN_IMAGE_HOST = "i.pinimg.com"


class LogoError(Exception):
    """A problem with a logo, phrased for the person choosing it."""


def image_extension(data: bytes):
    """The file type from the bytes themselves, so a page renamed to .png is never stored as an image."""
    if data.startswith(b"\x89PNG\r\n\x1a\n"):
        return ".png"
    if data.startswith(b"\xff\xd8\xff"):
        return ".jpg"
    if data[:4] == b"RIFF" and data[8:12] == b"WEBP":
        return ".webp"
    return None


def resolve_ref(ref):
    """The file a logo reference such as "bundled/combat-sword-gold.png" or "custom/my-logo-1a2b3c.png" names, or None."""
    kind, _, name = str(ref or "").partition("/")
    folder = {"bundled": BUNDLED_DIR, "custom": CUSTOM_DIR}.get(kind)
    if folder is None or not NAME_PATTERN.match(name):
        return None
    path = folder / name
    return path if path.is_file() else None


def describe(kind: str, path: Path) -> dict:
    parts = path.stem.split("-")
    if kind == "bundled":
        category = parts[0]
        label = " ".join(parts[1:]).title() if category == "pixel" else f"{parts[1].title()} ({parts[2]})" if len(parts) > 2 else path.stem.title()
        url = f"/static/logos/{path.name}?v={ASSET_VERSION}"
    else:
        category = "mine"
        label = " ".join(parts[:-1] or parts).title()
        url = f"/api/logos/custom/{path.name}"
    return {"ref": f"{kind}/{path.name}", "name": label, "category": category, "url": url}


def list_logos() -> dict:
    def order(logo):
        category = logo["category"]
        return (CATEGORY_ORDER.index(category) if category in CATEGORY_ORDER else len(CATEGORY_ORDER), logo["name"])

    bundled = sorted((describe("bundled", p) for p in BUNDLED_DIR.glob("*.png") if NAME_PATTERN.match(p.name)), key=order)
    custom = []
    if CUSTOM_DIR.is_dir():
        files = sorted((p for p in CUSTOM_DIR.iterdir() if p.is_file() and NAME_PATTERN.match(p.name)), key=lambda p: p.stat().st_mtime, reverse=True)
        custom = [describe("custom", p) for p in files]
    return {"bundled": bundled, "custom": custom}


def add_custom(data: bytes, label: str) -> dict:
    if len(data) > MAX_LOGO_BYTES:
        raise LogoError(f"That image is larger than {MAX_LOGO_BYTES // (1024 * 1024)} MB")
    extension = image_extension(data)
    if not extension:
        raise LogoError("Use a PNG, JPG or WebP image")
    CUSTOM_DIR.mkdir(parents=True, exist_ok=True)
    if sum(1 for _ in CUSTOM_DIR.iterdir()) >= MAX_CUSTOM_LOGOS:
        raise LogoError(f"The logo library is full ({MAX_CUSTOM_LOGOS} logos). Delete some first")
    slug = re.sub(r"[^a-z0-9]+", "-", label.lower()).strip("-")[:40] or "logo"
    path = CUSTOM_DIR / f"{slug}-{hashlib.sha1(data).hexdigest()[:6]}{extension}"
    if not path.exists():
        path.write_bytes(data)
    return describe("custom", path)


def install_server_logo(server_id: str, data: bytes) -> str:
    """Stores `data` as the server's logo and returns the file name. Replaces any earlier logo."""
    if len(data) > MAX_LOGO_BYTES:
        raise LogoError(f"That image is larger than {MAX_LOGO_BYTES // (1024 * 1024)} MB")
    extension = image_extension(data)
    if not extension:
        raise LogoError("Use a PNG, JPG, or WebP image")
    server_path = get_server_path(server_id)
    for old in server_path.glob("manager_logo.*"):
        old.unlink(missing_ok=True)
    filename = f"manager_logo{extension}"
    (server_path / filename).write_bytes(data)
    meta = load_meta(server_id)
    logo = meta.get("logo") if isinstance(meta.get("logo"), dict) else {}
    logo.update(file=filename, rev=int(time.time()))  # rev lets the browser tell the new picture from the cached one
    meta["logo"] = logo
    save_meta(server_id, meta)
    return filename


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


def fetch_pinterest_image(link: str):
    """Returns (image bytes, a name for it) for a pin link or a direct i.pinimg.com image link."""
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
    data = download(image_url, pin_image_allowed, MAX_LOGO_BYTES)
    if not image_extension(data):
        raise LogoError("That pin is not a PNG, JPG or WebP image")
    return data, label


# ---------------------------------------------------------------- routes
@app.route("/api/logos")
def api_logos():
    return jsonify({"ok": True, **list_logos()})


@app.route("/api/logos/custom/<name>")
def api_logo_custom_file(name):
    path = resolve_ref(f"custom/{name}")
    if path is None:
        abort(404)
    return send_from_directory(CUSTOM_DIR, path.name)


@app.route("/api/logos/custom", methods=["POST"])
def api_logos_import():
    uploads = request.files.getlist("files")[:MAX_UPLOAD_FILES]
    if not uploads:
        return jsonify({"ok": False, "error": "Choose one or more image files"}), 400
    added, skipped = [], []
    for upload in uploads:
        try:
            added.append(add_custom(upload.stream.read(MAX_LOGO_BYTES + 1), Path(upload.filename or "logo").stem))
        except LogoError as exc:
            skipped.append(f"{upload.filename or 'file'}: {exc}")
    return jsonify({"ok": bool(added), "added": added, "skipped": skipped, "error": "; ".join(skipped[:3])}), 200 if added else 400


@app.route("/api/logos/custom/<name>", methods=["DELETE"])
def api_logos_delete(name):
    path = resolve_ref(f"custom/{name}")
    if path is None:
        return jsonify({"ok": False, "error": "That logo no longer exists"}), 404
    path.unlink(missing_ok=True)
    return jsonify({"ok": True})


@app.route("/api/logos/pinterest", methods=["POST"])
def api_logos_pinterest():
    link = str((request.get_json(silent=True) or {}).get("url") or "")
    try:
        data, label = fetch_pinterest_image(link)
        return jsonify({"ok": True, "logo": add_custom(data, label)})
    except LogoError as exc:
        return jsonify({"ok": False, "error": str(exc)}), 400
    except requests.RequestException:
        return jsonify({"ok": False, "error": "Could not reach Pinterest. Check the link and your connection, then try again"}), 502
