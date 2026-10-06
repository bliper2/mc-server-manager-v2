"""Transport hardening: security headers, gzip, cache headers and proxy-aware session cookies."""

import gzip

from flask import request
from flask.sessions import SecureCookieSessionInterface

from . import app
from .config import DEV_MODE

# Inline handlers are used throughout the UI, so script-src needs 'unsafe-inline'. Everything else is locked down.
CSP = "; ".join([
    "default-src 'self'",
    "script-src 'self' 'unsafe-inline'",
    "style-src 'self' 'unsafe-inline' https://fonts.googleapis.com",
    "font-src 'self' https://fonts.gstatic.com data:",
    "img-src 'self' data: blob: https:",
    "connect-src 'self'",
    "object-src 'none'",
    "base-uri 'self'",
    "form-action 'self'",
    "frame-ancestors 'none'",
])
COMPRESSIBLE = ("text/", "application/json", "application/javascript", "image/svg+xml")
MIN_COMPRESS_BYTES = 1024


class ProxyAwareSessions(SecureCookieSessionInterface):
    """Marks the session cookie Secure when the page was reached over HTTPS, including through Tailscale serve."""

    def get_cookie_secure(self, app):
        return request.is_secure or request.headers.get("X-Forwarded-Proto", "").lower() == "https"


app.session_interface = ProxyAwareSessions()


def wants_gzip(response) -> bool:
    return (
        response.status_code == 200
        and "Content-Encoding" not in response.headers
        and "gzip" in request.headers.get("Accept-Encoding", "").lower()
        and response.mimetype.startswith(COMPRESSIBLE)
        and (response.direct_passthrough or not response.is_streamed)
    )


@app.after_request
def harden_response(response):
    headers = response.headers
    headers.setdefault("X-Content-Type-Options", "nosniff")
    headers.setdefault("X-Frame-Options", "DENY")
    headers.setdefault("Referrer-Policy", "same-origin")
    headers.setdefault("Permissions-Policy", "camera=(), microphone=(), geolocation=(), payment=()")
    if response.mimetype == "text/html":
        headers.setdefault("Content-Security-Policy", CSP)
    if request.path.startswith("/api/logos/custom/") and request.method == "GET":
        headers["Cache-Control"] = "private, no-cache"  # files in the logos folder can be replaced under the same name, so the browser asks every time (a cheap 304)
    elif request.path.startswith("/api/"):
        headers["Cache-Control"] = "no-store"
    elif request.path.startswith("/static/") and "v" in request.args and response.status_code == 200 and not DEV_MODE:
        headers["Cache-Control"] = "public, max-age=31536000, immutable"  # the ?v= query changes with every release
    if wants_gzip(response):
        response.direct_passthrough = False
        body = response.get_data()
        response.close()  # releases the open static file straight away, which matters on Windows
        if len(body) >= MIN_COMPRESS_BYTES:
            response.set_data(gzip.compress(body, compresslevel=5))
            headers["Content-Encoding"] = "gzip"
            headers.add("Vary", "Accept-Encoding")
    return response
