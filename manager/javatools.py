"""Finding a suitable Java, knowing which one a Minecraft version needs, and installing one on Windows."""

import os
import re
import shutil
import subprocess
import threading
from pathlib import Path

from .state import java_install

JAVA_EXE = "java.exe" if os.name == "nt" else "java"
WINDOWS_VENDORS = ("Eclipse Adoptium", "Java", "Microsoft", "Zulu", "Amazon Corretto", "BellSoft", "Programs/Eclipse Adoptium")
INSTALL_TARGET = 21
WINGET_ID = "EclipseAdoptium.Temurin.21.JDK"

_major_cache: dict = {}
_install_lock = threading.Lock()


def required_java_major(mc_version: str | None):
    """Minimum Java major for a Minecraft version, or None when the version cannot be read."""
    match = re.match(r"^(\d+)\.(\d+)(?:\.(\d+))?", str(mc_version or ""))
    if not match:
        return None
    first, second, third = int(match.group(1)), int(match.group(2)), int(match.group(3) or 0)
    if first >= 26:
        return 25  # calendar-versioned releases (26.x)
    if first != 1:
        return None
    if second >= 21 or (second == 20 and third >= 5):
        return 21
    if second >= 17:
        return 17
    return 8


def java_major(path: str):
    """Major version of the java binary at `path`, cached per file. None if it cannot be run."""
    try:
        stamp = (path, os.stat(path).st_mtime_ns)
    except OSError:
        return None
    if stamp in _major_cache:
        return _major_cache[stamp]
    major = None
    try:
        result = subprocess.run([path, "-version"], capture_output=True, text=True, timeout=10)
        match = re.search(r'version "(\d+)(?:\.(\d+))?', (result.stderr or "") + (result.stdout or ""))
        if match:
            major = int(match.group(2)) if match.group(1) == "1" and match.group(2) else int(match.group(1))
    except (OSError, subprocess.SubprocessError):
        major = None
    _major_cache[stamp] = major
    return major


def java_candidates() -> list:
    """Every java binary we can find, de-duplicated, in the order PATH, JAVA_HOME, install folders."""
    found: list = []

    def add(candidate):
        if candidate and os.path.isfile(candidate):
            resolved = str(Path(candidate).resolve())
            if resolved.lower() not in [f.lower() for f in found]:
                found.append(resolved)

    add(shutil.which("java"))
    home = os.environ.get("JAVA_HOME")
    if home:
        add(str(Path(home) / "bin" / JAVA_EXE))
    if os.name == "nt":
        for var in ("ProgramFiles", "ProgramFiles(x86)", "LOCALAPPDATA"):
            root = os.environ.get(var)
            if not root:
                continue
            for vendor in WINDOWS_VENDORS:
                base = Path(root) / vendor
                if base.is_dir():
                    for candidate in sorted(base.glob(f"*/bin/{JAVA_EXE}")):
                        add(str(candidate))
    return found


def installed_javas() -> list:
    """[(path, major)] for every Java found on this PC, lowest major first."""
    found = [(path, java_major(path)) for path in java_candidates()]
    return sorted(((path, major) for path, major in found if major), key=lambda item: item[1])


def preferred_java(meta: dict):
    """The Java major a server was told to use (Launch settings), or None for automatic."""
    stored = meta.get("launch") if isinstance(meta.get("launch"), dict) else {}
    value = str(stored.get("java") or "")
    return int(value) if value.isdigit() else None


def java_for(meta: dict):
    """(path, major) a server will start on: its chosen Java when that is installed and new enough, otherwise the automatic pick."""
    return choose_java(required_java_major(meta.get("version")), preferred_java(meta))


def choose_java(required: int | None, prefer: int | None = None):
    """Pick (path, major): the Java the server was told to use when it is installed and satisfies `required`, else the
    lowest installed Java that does, else the newest installed. Returns (None, None) when no Java exists at all."""
    installed = installed_javas()
    if not installed:
        return None, None
    if prefer:
        chosen = [item for item in installed if item[1] == prefer and (not required or item[1] >= required)]
        if chosen:
            return chosen[0]
    if required:
        suitable = sorted((item for item in installed if item[1] >= required), key=lambda item: item[1])
        if suitable:
            return suitable[0]
    return sorted(installed, key=lambda item: item[1], reverse=True)[0]


def java_status(mc_version: str | None = None, prefer: int | None = None) -> dict:
    required = required_java_major(mc_version)
    path, major = choose_java(required, prefer)
    return {
        "found": bool(path),
        "path": path,
        "major": major,
        "required": required,
        "ok": bool(path) and (required is None or (major or 0) >= required),
        "can_install": os.name == "nt" and bool(shutil.which("winget")),
        "target": INSTALL_TARGET,
        "install": dict(java_install, log=java_install["log"][-12:]),
    }


def _install_worker():
    def note(message):
        java_install["message"] = message
        java_install["log"].append(message)

    try:
        note(f"Installing Temurin {INSTALL_TARGET} with winget. This can take a few minutes.")
        result = subprocess.run(
            ["winget", "install", "-e", "--id", WINGET_ID, "--silent",
             "--accept-package-agreements", "--accept-source-agreements"],
            capture_output=True, text=True, timeout=1200,
        )
        for line in (result.stdout or "").splitlines()[-6:]:
            if line.strip():
                java_install["log"].append(line.strip()[:160])
        _major_cache.clear()
        installed_major = choose_java(INSTALL_TARGET)[1] or 0
        if result.returncode == 0 or installed_major >= INSTALL_TARGET:
            java_install["state"] = "done"
            note(f"Java {INSTALL_TARGET} installed. You can start servers now.")
        else:
            java_install["state"] = "error"
            note(f"winget exited with code {result.returncode}. Install Java {INSTALL_TARGET} manually from adoptium.net.")
    except (OSError, subprocess.SubprocessError) as exc:
        java_install["state"] = "error"
        note(f"Install failed: {exc}")


def start_java_install():
    """Returns (started, message). Windows only; needs winget."""
    if os.name != "nt" or not shutil.which("winget"):
        return False, f"One-click install needs Windows with winget. Download Java {INSTALL_TARGET} from adoptium.net."
    with _install_lock:
        if java_install["state"] == "running":
            return False, "An install is already running"
        java_install.update(state="running", message="Starting...", log=[])
        threading.Thread(target=_install_worker, daemon=True, name="java-install").start()
    return True, "Install started"
