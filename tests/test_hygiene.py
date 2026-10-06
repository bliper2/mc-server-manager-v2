"""Guards the repository itself: no real webhook URLs, API keys or private data files may be committed."""
import re
import shutil
import subprocess
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
WEBHOOK = re.compile(r"discord(?:app)?\.com/api/webhooks/(\d{17,20})/[\w-]{20,}")
CURSEFORGE_KEY = re.compile(r"\$2a\$10\$[A-Za-z0-9./]{20,}")
FAKE_WEBHOOK_ID = "123456789012345678"  # the placeholder the tests use
FAKE_KEY_MARK = "abcdefghijklmnopqrstuvwxyz"  # likewise
PRIVATE_FILES = {"staff.json", ".secret_key", "audit.jsonl", "manager_settings.json", "restart_state.json", "update_state.json"}


def tracked_files():
    if not shutil.which("git") or not (ROOT / ".git").exists():
        return None
    out = subprocess.run(["git", "ls-files", "-z"], cwd=ROOT, capture_output=True, check=True).stdout
    return [ROOT / name for name in out.decode("utf-8").split("\0") if name]


@unittest.skipIf(tracked_files() is None, "not a git checkout")
class RepositoryHygiene(unittest.TestCase):
    def test_no_real_webhook_or_api_key_is_committed(self):
        found = []
        for path in tracked_files():
            try:
                text = path.read_text(encoding="utf-8")
            except (UnicodeDecodeError, OSError):
                continue
            for match in WEBHOOK.finditer(text):
                if match.group(1) != FAKE_WEBHOOK_ID:
                    found.append(f"{path.relative_to(ROOT)}: a Discord webhook URL")
            for match in CURSEFORGE_KEY.finditer(text):
                if FAKE_KEY_MARK not in match.group(0):
                    found.append(f"{path.relative_to(ROOT)}: an API key")
        self.assertEqual(found, [], "secrets must live in manager_settings.json (ignored by git), never in the repository")

    def test_private_data_files_are_not_tracked(self):
        tracked = {path.name for path in tracked_files()}
        self.assertEqual(sorted(tracked & PRIVATE_FILES), [])
        self.assertEqual([p for p in tracked_files() if p.name.startswith(".env")], [])
        self.assertEqual([p for p in tracked_files() if p.relative_to(ROOT).parts[0] in ("logos", "banners")], [], "imported logos and banners are the owner's data, not part of the repository")
