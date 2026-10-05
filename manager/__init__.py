"""MC Server Manager.

`app` is created here; every module below registers its routes on it when imported.
Module layout (lower layers never import higher ones):
    config, state -> store -> util -> notify, javatools, totp -> providers, procs -> auth -> lifecycle
    -> backups, rconmap, updater -> routes_servers, routes_files, automation
"""
import secrets
from datetime import timedelta

from flask import Flask, jsonify, request
from werkzeug.exceptions import HTTPException

from .config import BASE_DIR, SECRET_FILE

app = Flask(__name__, template_folder=str(BASE_DIR / "templates"), static_folder=str(BASE_DIR / "static"))
app.config["MAX_CONTENT_LENGTH"] = 256 * 1024 * 1024


def load_secret_key() -> str:
    """Persisted so sessions survive restarts and the dev reloader."""
    try:
        stored = SECRET_FILE.read_text(encoding="utf-8").strip()
        if stored:
            return stored
    except OSError:
        pass
    generated = secrets.token_hex(32)
    SECRET_FILE.write_text(generated, encoding="utf-8")
    return generated


app.secret_key = load_secret_key()
app.config.update(
    SESSION_COOKIE_HTTPONLY=True,
    SESSION_COOKIE_SAMESITE="Strict",
    SESSION_COOKIE_NAME="mcm_session",
    PERMANENT_SESSION_LIFETIME=timedelta(hours=12),
)


def wants_json() -> bool:
    return request.path.startswith("/api/")


@app.errorhandler(413)
def handle_upload_too_large(_error):
    limit = app.config["MAX_CONTENT_LENGTH"] // (1024 * 1024)
    return jsonify({
        "ok": False,
        "error": f"That upload is larger than the {limit} MB limit for a single request. Folder imports are sent in batches, so this usually means one individual file is oversized."
    }), 413


@app.errorhandler(HTTPException)
def handle_http_error(error):
    if not wants_json():
        return error
    return jsonify({"ok": False, "error": error.description or error.name}), error.code


@app.errorhandler(Exception)
def handle_unexpected_error(error):
    if not wants_json():
        raise error
    app.logger.exception("Unhandled error on %s", request.path)
    return jsonify({"ok": False, "error": f"{type(error).__name__}: {error}"}), 500


# Imported last: each module decorates routes on `app`, so `app` must exist first.
from . import auth, automation, backups, lifecycle, rconmap, routes_files, routes_servers, updater  # noqa: E402,F401
