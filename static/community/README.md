# Community logos and banners

Images in these two folders ship with MC Server Manager and appear in everyone's logo and banner picker under **Community**:

- `logos/`: server logos (square works best, 128 x 128 up to 512 x 512)
- `banners/`: wide banners (about 640 x 192, or any 10:3 shape)

PNG, JPG, GIF and WebP all work, and **GIFs stay animated**. Updating the manager downloads them with everything else.

## Adding yours

1. Put the file in the right folder. Any readable name is fine (`Team Rocket.gif`); the name becomes the label in the picker.
2. Or run `python tools/add_community.py logo "path/to/file.png"` (use `banner` for banners). It checks the file and copies it with a clean name.
3. Open a pull request.

Rules, so the library stays safe to share:

- **Only submit art you made, or art whose licence lets anyone redistribute it** (CC0, CC BY, your own work). Do not submit logos or characters of games, brands, bands or streamers that you did not create, and do not submit images saved from Pinterest, Google Images or similar sites unless the artist's licence allows it. Images that break this are removed on request.
- Logos up to 8 MB and banners up to 16 MB, but please keep them small: under 300 KB for a logo and 700 KB for a banner is plenty.
- It must really be an image (the manager checks the file's content, not its name).
- Credit the artist in the pull request description when it is not you.

The ready-made logos and banners that are not in this folder were drawn by `tools/make_logos.py` and `tools/make_banners.py`, so they carry no licence at all.
