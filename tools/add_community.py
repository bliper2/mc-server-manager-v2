"""Adds an image to the community library after checking it: python tools/add_community.py logo|banner FILE [FILE ...]

Copies each file into static/community/logos or static/community/banners under a clean name, so it can go into a pull
request. Only the file's real content is checked (PNG, JPG, GIF or WebP, within the size limit), not its extension.
You are responsible for having the right to share the image: see static/community/README.md."""
import re
import shutil
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from manager.logos import IMAGE_TYPES, MAX_BANNER_BYTES, MAX_LOGO_BYTES, image_extension  # noqa: E402

FOLDERS = {"logo": (ROOT / "static" / "community" / "logos", MAX_LOGO_BYTES), "banner": (ROOT / "static" / "community" / "banners", MAX_BANNER_BYTES)}


def main(argv):
    if len(argv) < 3 or argv[1] not in FOLDERS:
        print(__doc__)
        return 2
    folder, limit = FOLDERS[argv[1]]
    folder.mkdir(parents=True, exist_ok=True)
    status = 0
    for name in argv[2:]:
        source = Path(name)
        try:
            data = source.read_bytes()
        except OSError as exc:
            print(f"skipped {name}: {exc}")
            status = 1
            continue
        extension = image_extension(data)
        if extension not in IMAGE_TYPES or len(data) > limit:
            print(f"skipped {name}: not a PNG, JPG, GIF or WebP image within {limit // (1024 * 1024)} MB")
            status = 1
            continue
        stem = re.sub(r"[^\w \-]+", "", source.stem).strip()[:60] or argv[1]
        target = folder / f"{stem}{extension}"
        number = 2
        while target.exists() and target.read_bytes() != data:
            target = folder / f"{stem}-{number}{extension}"
            number += 1
        shutil.copyfile(source, target)
        shown = target.relative_to(ROOT) if ROOT in target.parents else target
        print(f"added {shown} ({len(data) // 1024} KB)")
    return status


if __name__ == "__main__":
    sys.exit(main(sys.argv))
