"""The terrain behind the live map: NBT, region files, tiles, world info, markers and RCON polling."""
import gzip
import json
import struct
import zlib
from concurrent.futures import Future
from unittest import mock

from manager import rconmap, state, worldmap
from tests.support import HOME, AppTestCase, make_server

# ---------------------------------------------------------------- a tiny NBT writer, enough to build worlds in memory
TAG = {"byte": 1, "int": 3, "long": 4, "double": 6, "string": 8, "list": 9, "compound": 10, "longs": 12, "ints": 11}


def name_bytes(text):
    raw = text.encode("utf-8")
    return struct.pack(">H", len(raw)) + raw


def payload(kind, value):
    if kind == "byte":
        return struct.pack(">b", value)
    if kind == "int":
        return struct.pack(">i", value)
    if kind == "long":
        return struct.pack(">q", value)
    if kind == "double":
        return struct.pack(">d", value)
    if kind == "string":
        return name_bytes(value)
    if kind == "longs":
        return struct.pack(">i", len(value)) + struct.pack(f">{len(value)}q", *value)
    if kind == "ints":
        return struct.pack(">i", len(value)) + struct.pack(f">{len(value)}i", *value)
    if kind == "list":
        item_kind, items = value
        return bytes([TAG[item_kind]]) + struct.pack(">i", len(items)) + b"".join(payload(item_kind, item) for item in items)
    if kind == "compound":
        return b"".join(bytes([TAG[k]]) + name_bytes(n) + payload(k, v) for n, (k, v) in value.items()) + b"\x00"
    raise AssertionError(kind)


def nbt(compound):
    return bytes([10]) + name_bytes("") + payload("compound", compound)


def packed(values, bits):
    """Packs entries into longs the way Minecraft does since 1.16: entries never straddle two longs."""
    per_long = 64 // bits
    longs = []
    for start in range(0, len(values), per_long):
        word = 0
        for offset, value in enumerate(values[start:start + per_long]):
            word |= value << (offset * bits)
        longs.append(word - (1 << 64) if word >= 1 << 63 else word)
    return longs


def palette_entry(name, style):
    if style == "wrapped":
        return {"": ("string", name)}  # 1.21.5+: a plain string inside a list of compounds
    if style == "id":
        return {"id": ("string", name)}
    return {"Name": ("string", name)}


def section(y, palette, indexes=None, style="id"):
    block_states = {"palette": ("list", ("compound", [palette_entry(name, style) for name in palette]))}
    if len(palette) > 1:
        block_states["data"] = ("longs", packed(indexes, max(4, (len(palette) - 1).bit_length())))
    return {"Y": ("byte", y), "block_states": ("compound", block_states)}


def heights(values):
    return ("longs", packed(values, 9))


def chunk(cx, cz, sections, surface=None, floor=None, status="minecraft:full", min_section=-4):
    body = {"xPos": ("int", cx), "zPos": ("int", cz), "yPos": ("int", min_section), "Status": ("string", status),
            "sections": ("list", ("compound", sections))}
    if surface is not None:
        body["Heightmaps"] = ("compound", {"WORLD_SURFACE": heights(surface), "OCEAN_FLOOR": heights(floor or surface)})
    return nbt(body)


def column_index(x, y, z):
    return ((y & 15) << 8) | (z << 4) | x


def ground_chunk(cx, cz, top_y=64, decor=None):
    """Left half grass, right half water over a sand floor four blocks down. Optionally a plant on top of the grass."""
    floor_y = top_y - 4
    names = ["air", "grass_block", "water", "sand"] + ([decor] if decor else [])
    blocks = {}

    def put(x, y, z, index):
        blocks.setdefault(y >> 4, [0] * 4096)[column_index(x, y, z)] = index

    for z in range(16):
        for x in range(16):
            put(x, top_y, z, 1 if x < 8 else 2)
            if x >= 8:
                put(x, floor_y, z, 3)
            elif decor:
                put(x, top_y + 1, z, 4)
    sections = [section(y, names, indexes, style="wrapped" if decor else "id") for y, indexes in sorted(blocks.items())]
    surface = [top_y + (2 if decor and x < 8 else 1) + 64 for z in range(16) for x in range(16)]
    floor = [(top_y + (2 if decor else 1) + 64) if x < 8 else (floor_y + 1 + 64) for z in range(16) for x in range(16)]
    return chunk(cx, cz, sections, surface, floor)


def region_file(chunks, method=2):
    """chunks: {(local x, local z): NBT bytes}. Returns the bytes of an .mca file."""
    header, body = bytearray(8192), bytearray()
    for (lx, lz), data in chunks.items():
        packed_data = {2: zlib.compress(data), 1: gzip.compress(data), 3: data, 4: b"not really lz4", 9: zlib.compress(data)[:-5]}[method]
        entry = struct.pack(">IB", len(packed_data) + 1, 4 if method == 4 else 2 if method == 9 else method) + packed_data
        sectors = -(-len(entry) // 4096)
        offset = 2 + len(body) // 4096
        index = lz * 32 + lx
        header[index * 4:index * 4 + 4] = struct.pack(">I", (offset << 8) | sectors)
        body += entry + b"\x00" * (sectors * 4096 - len(entry))
    return bytes(header) + bytes(body)


def png_pixel(data, x, y):
    """(r, g, b, a) of one pixel of an 8-bit RGBA PNG made by worldmap.png_rgba, checking the file structure on the way."""
    assert data[:8] == b"\x89PNG\r\n\x1a\n"
    width, height = struct.unpack(">II", data[16:24])
    assert data[24:26] == bytes([8, 6])
    at, idat = 8, b""
    while at < len(data):
        length, tag = struct.unpack(">I4s", data[at:at + 8])
        body = data[at + 8:at + 8 + length]
        assert struct.unpack(">I", data[at + 8 + length:at + 12 + length])[0] == zlib.crc32(tag + body) & 0xFFFFFFFF, f"bad CRC in {tag}"
        if tag == b"IDAT":
            idat += body
        at += 12 + length
    raw = zlib.decompress(idat)
    assert len(raw) == height * (1 + width * 4)
    row = raw[y * (1 + width * 4) + 1:(y + 1) * (1 + width * 4)]
    return tuple(row[x * 4:x * 4 + 4]), (width, height)


def near(colour, expected, tolerance=3):
    return all(abs(a - b) <= tolerance for a, b in zip(colour[:3], expected))


class Rendering(AppTestCase):
    def render(self, chunks, dimension="overworld", method=2):
        path = HOME / "r.0.0.mca"
        path.write_bytes(region_file(chunks, method))
        return worldmap.render_region(path, dimension)

    def test_land_water_and_the_sea_floor_get_their_colours(self):
        full, overview, drawn = self.render({(0, 0): ground_chunk(0, 0)})
        self.assertEqual(drawn, 1)
        grass, size = png_pixel(full, 3, 5)
        self.assertEqual(size, (512, 512))
        self.assertEqual(grass, (104, 164, 60, 255), "grass is its plain colour where nothing is higher or lower")
        water, _ = png_pixel(full, 12, 5)
        self.assertTrue(near(water, (93, 107, 154)), f"water over sand, four blocks deep: {water}")
        self.assertEqual(png_pixel(full, 40, 40)[0][3], 0, "an area with no chunk stays transparent")
        small, dims = png_pixel(overview, 0, 1)
        self.assertEqual(dims, (128, 128))
        self.assertEqual(small, (104, 164, 60, 255), "the overview samples the full tile")

    def test_deeper_water_is_darker_and_higher_ground_is_lit(self):
        full, _, drawn = self.render({(0, 0): ground_chunk(0, 0, top_y=64), (0, 1): ground_chunk(0, 1, top_y=66)})
        self.assertEqual(drawn, 2)
        flat = png_pixel(full, 3, 20)[0]
        lit = png_pixel(full, 3, 16)[0]
        self.assertEqual(flat, (104, 164, 60, 255))
        self.assertTrue(lit[0] > flat[0] and lit[1] > flat[1], f"two blocks higher than the north neighbour: {lit}")
        self.assertTrue(near(lit, (114, 180, 66), 3))

    def test_plants_on_the_ground_show_the_ground_under_them(self):
        full, _, _ = self.render({(0, 0): ground_chunk(0, 0, decor="short_grass")})
        self.assertEqual(png_pixel(full, 3, 5)[0], (104, 164, 60, 255))

    def test_the_nether_shows_the_first_solid_block_below_its_roof(self):
        palette, indexes = ["air", "netherrack"], [0] * 4096
        for z in range(16):
            for x in range(16):
                indexes[column_index(x, 64, z)] = 1
        data = chunk(0, 0, [section(4, palette, indexes), section(7, ["bedrock"])], min_section=0)
        full, _, drawn = self.render({(0, 0): data}, dimension="the_nether")
        self.assertEqual(drawn, 1)
        self.assertEqual(png_pixel(full, 2, 2)[0], (111, 54, 53, 255), "the bedrock roof above y=70 is skipped")

    def test_chunks_that_are_not_finished_or_cannot_be_read_are_skipped(self):
        good = ground_chunk(1, 0)
        proto = chunk(0, 0, [section(4, ["air", "stone"], [0] * 4096)], status="minecraft:structure_starts")
        full, _, drawn = self.render({(0, 0): proto, (1, 0): good})
        self.assertEqual(drawn, 1)
        self.assertEqual(png_pixel(full, 3, 3)[0][3], 0)
        self.assertEqual(png_pixel(full, 19, 3)[0][3], 255)
        for method in (4, 9):  # LZ4, which this reader does not know, and a cut-off deflate stream
            _, _, drawn = self.render({(0, 0): good}, method=method)
            self.assertEqual(drawn, 0, method)
        for method in (1, 3):  # gzip and uncompressed chunks are fine
            _, _, drawn = self.render({(0, 0): good}, method=method)
            self.assertEqual(drawn, 1, method)

    def test_a_chunk_that_inflates_beyond_the_limit_is_skipped(self):
        with mock.patch.object(worldmap, "MAX_CHUNK_BYTES", 64):
            _, _, drawn = self.render({(0, 0): ground_chunk(0, 0)})
        self.assertEqual(drawn, 0)

    def test_files_that_are_not_regions_are_ignored(self):
        path = HOME / "r.5.5.mca"
        path.write_bytes(b"too short")
        self.assertEqual(list(worldmap.read_region(path)), [])
        path.write_bytes(b"\x00" * 8192)
        self.assertEqual(list(worldmap.read_region(path)), [])

    def test_block_colours_cover_the_families(self):
        for name, expected in (("grass_block", (104, 164, 60)), ("red_concrete", worldmap.DYES["red"]), ("birch_leaves", worldmap.LEAF_COLOURS["birch"]),
                               ("deepslate_iron_ore", (96, 96, 98)), ("iron_ore", (122, 122, 122)), ("terracotta", (152, 94, 67))):
            self.assertEqual(worldmap.block_colour(name), expected, name)
        self.assertEqual(worldmap.block_colour("spruce_planks"), worldmap.WOOD_COLOURS["spruce"])
        self.assertLess(sum(worldmap.block_colour("oak_log")), sum(worldmap.block_colour("oak_planks")), "logs are darker than planks")
        unknown = worldmap.block_colour("some_modded_block")
        self.assertEqual(unknown, worldmap.block_colour("some_modded_block"), "unknown blocks get a stable colour")
        self.assertEqual(worldmap.short_name({"id": "minecraft:oak_log", "properties": {}}), "oak_log")
        self.assertEqual(worldmap.short_name({"": "minecraft:water"}), "water")
        self.assertEqual(worldmap.short_name("minecraft:stone[x=1]"), "stone")
        self.assertEqual(worldmap.short_name({"Name": "minecraft:sand"}), "sand")


class NbtParsing(AppTestCase):
    def test_a_round_trip_keeps_types_and_values(self):
        data = nbt({"a": ("int", -5), "b": ("string", "héllo"), "c": ("longs", [1, -2, 3]), "d": ("list", ("int", [1, 2])), "e": ("compound", {"x": ("double", 1.5)})})
        self.assertEqual(worldmap.parse_nbt(data), {"a": -5, "b": "héllo", "c": [1, -2, 3], "d": [1, 2], "e": {"x": 1.5}})

    def test_hostile_files_raise_a_clean_error(self):
        nested = b"\x0a\x00\x00" + (b"\x0a\x00\x01a") * 100 + b"\x00" * 101
        lying_list = b"\x0a\x00\x00" + b"\x09\x00\x01a" + b"\x03" + struct.pack(">i", 2_000_000_000) + b"\x00"
        lying_array = b"\x0a\x00\x00" + b"\x0c\x00\x01a" + struct.pack(">i", 2_000_000_000) + b"\x00"
        for bad in (b"", b"\x0a", nbt({"a": ("int", 1)})[:-3], nested, lying_list, lying_array, b"\x03\x00\x00\x00\x00\x00\x01", b"\x0a\x00\x00\x63\x00\x01a\x00"):
            with self.assertRaises(worldmap.NbtError, msg=bad[:20]):
                worldmap.parse_nbt(bad)


class WorldFolders(AppTestCase):
    def folder(self, *parts, files=None):
        path = HOME / "servers" / "alpha_1"
        for part in parts:
            path = path / part
        path.mkdir(parents=True, exist_ok=True)
        for name in files or ():
            (path / name).write_bytes(b"\x00" * 9000)
        return path

    def test_every_known_layout_is_found(self):
        make_server()
        new = self.folder("world", "dimensions", "minecraft", "overworld", "region", files=["r.0.0.mca", "r.-1.2.mca"])
        self.assertEqual(worldmap.region_dir("alpha_1", "overworld").resolve(), (new).resolve())
        self.assertEqual(sorted(worldmap.list_regions("alpha_1", "overworld")), [(-1, 2), (0, 0)])
        self.folder("world", "dimensions", "minecraft", "the_nether", "region", files=["r.0.0.mca"])
        self.assertIsNotNone(worldmap.region_dir("alpha_1", "the_nether"))

    def test_older_layouts(self):
        make_server("old_1", name="Old", port=25601)
        base = HOME / "servers" / "old_1"
        (base / "world" / "region").mkdir(parents=True)
        (base / "world" / "DIM-1" / "region").mkdir(parents=True)
        (base / "world" / "DIM1" / "region").mkdir(parents=True)
        self.assertEqual(worldmap.region_dir("old_1", "overworld").resolve(), (base / "world" / "region").resolve())
        self.assertEqual(worldmap.region_dir("old_1", "the_nether").resolve(), (base / "world" / "DIM-1" / "region").resolve())
        self.assertEqual(worldmap.region_dir("old_1", "the_end").resolve(), (base / "world" / "DIM1" / "region").resolve())
        make_server("split_1", name="Split", port=25602)
        split = HOME / "servers" / "split_1"
        (split / "world" / "region").mkdir(parents=True)
        (split / "world_nether" / "DIM-1" / "region").mkdir(parents=True)
        self.assertEqual(worldmap.region_dir("split_1", "the_nether").resolve(), (split / "world_nether" / "DIM-1" / "region").resolve(), "Paper keeps the nether in its own folder")
        self.assertIsNone(worldmap.region_dir("split_1", "the_end"))

    def test_the_level_name_comes_from_server_properties_and_cannot_escape(self):
        folder = make_server()
        (folder / "server.properties").write_text("level-name=My Survival World\n", encoding="utf-8")
        (folder / "My Survival World" / "region").mkdir(parents=True)
        self.assertEqual(worldmap.level_name("alpha_1"), "My Survival World")
        self.assertEqual(worldmap.region_dir("alpha_1", "overworld").resolve(), (folder / "My Survival World" / "region").resolve())
        for bad in ("../../etc", "..", "a/b", ".hidden", "", "x" * 80):
            (folder / "server.properties").write_text(f"level-name={bad}\n", encoding="utf-8")
            self.assertEqual(worldmap.level_name("alpha_1"), "world", repr(bad))

    def test_unknown_dimensions_and_missing_worlds(self):
        make_server()
        self.assertIsNone(worldmap.region_dir("alpha_1", "overworld"))
        self.assertIsNone(worldmap.region_dir("alpha_1", "../x"))
        self.assertEqual(worldmap.world_info("alpha_1"), {"world": None, "dimensions": {}, "spawn": None, "players": []})

    def test_the_spawn_in_both_level_dat_formats(self):
        folder = make_server()
        world = folder / "world"
        world.mkdir()
        (world / "level.dat").write_bytes(gzip.compress(nbt({"data": ("compound", {"spawn": ("compound", {"pos": ("ints", [10, 70, -20]), "dimension": ("string", "minecraft:overworld")})})})))
        self.assertEqual(worldmap.read_spawn(world), {"x": 10, "y": 70, "z": -20, "dim": "overworld"})
        (world / "level.dat").write_bytes(gzip.compress(nbt({"Data": ("compound", {"SpawnX": ("int", 5), "SpawnY": ("int", 80), "SpawnZ": ("int", 6)})})))
        self.assertEqual(worldmap.read_spawn(world), {"x": 5, "y": 80, "z": 6, "dim": "overworld"})
        (world / "level.dat").write_bytes(b"corrupt")
        self.assertIsNone(worldmap.read_spawn(world))

    def test_players_are_read_from_either_folder_with_names_and_beds(self):
        folder = make_server()
        (folder / "usercache.json").write_text(json.dumps([{"uuid": "11111111-1111-1111-1111-111111111111", "name": "Steve"}]), encoding="utf-8")
        data = nbt({"Pos": ("list", ("double", [100.5, 64.0, -30.25])), "Dimension": ("string", "minecraft:the_nether"), "Rotation": ("list", ("double", [-90.0, 0.0])),
                    "Health": ("double", 14.0), "respawn": ("compound", {"pos": ("ints", [1, 65, 2]), "dimension": ("string", "minecraft:overworld")})})
        for sub in ("players/data", "playerdata"):
            target = folder / "world" / sub
            target.mkdir(parents=True, exist_ok=True)
            (target / "11111111-1111-1111-1111-111111111111.dat").write_bytes(gzip.compress(data))
            (target / "22222222-2222-2222-2222-222222222222.dat").write_bytes(b"broken")
            players = worldmap.read_player_files("alpha_1", folder / "world")
            self.assertEqual(len(players), 1, "a damaged player file is skipped")
            self.assertEqual((players[0]["name"], players[0]["dim"], players[0]["x"], players[0]["z"], players[0]["yaw"]), ("Steve", "the_nether", 100.5, -30.2, 270.0))
            self.assertEqual(players[0]["bed"], {"x": 1, "y": 65, "z": 2, "dim": "overworld"})
            for name in ("players", "playerdata"):
                import shutil
                shutil.rmtree(folder / "world" / name, ignore_errors=True)


class Routes(AppTestCase):
    def setUp(self):
        super().setUp()
        self.folder = make_server()
        regions = self.folder / "world" / "dimensions" / "minecraft" / "overworld" / "region"
        regions.mkdir(parents=True)
        self.source = regions / "r.-1.0.mca"
        self.source.write_bytes(region_file({(5, 5): ground_chunk(-27, 5)}))
        worldmap._failed.clear()
        worldmap._inflight.clear()
        worldmap.clear_cache("alpha_1")
        self.inline = mock.patch.object(worldmap, "_pool", mock.Mock(submit=self.run_now))
        self.inline.start()
        self.addCleanup(self.inline.stop)
        self.renders = []

    def run_now(self, function, *args):
        future = Future()
        self.renders.append(args[1:3])
        try:
            future.set_result(function(*args))
        except Exception as exc:  # noqa: BLE001
            future.set_exception(exc)
        return future

    def test_negative_regions_are_routable_and_the_first_request_draws_then_serves(self):
        owner = self.owner()
        reply = owner.get("/api/server/alpha_1/map/tile/overworld/-1/0.png")
        self.assertEqual(reply.status_code, 200, reply.data[:100])
        self.assertEqual(reply.mimetype, "image/png")
        self.assertEqual(png_pixel(reply.data, 5 * 16 + 3, 5 * 16 + 3)[0], (104, 164, 60, 255))
        reply.close()
        again = owner.get("/api/server/alpha_1/map/tile/overworld/-1/0.png")
        again.close()
        self.assertEqual(len(self.renders), 1, "the second request comes from the cache")
        small = owner.get("/api/server/alpha_1/map/tile/overworld/-1/0.png?lod=4")
        small_data = small.data
        small.close()
        self.assertEqual(png_pixel(small_data, 1, 1)[1], (128, 128))
        self.assertEqual(len(self.renders), 1)

    def test_a_tile_still_being_drawn_answers_202(self):
        owner = self.owner()
        pending = Future()
        with mock.patch.object(worldmap, "_pool", mock.Mock(submit=lambda *a: pending)):
            reply = owner.get("/api/server/alpha_1/map/tile/overworld/-1/0.png")
        self.assertEqual(reply.status_code, 202)
        self.assertEqual(reply.headers["Retry-After"], "2")
        self.assertTrue(reply.get_json()["rendering"])

    def test_missing_regions_bad_dimensions_and_unknown_servers(self):
        owner = self.owner()
        self.assertEqual(owner.get("/api/server/alpha_1/map/tile/overworld/7/7.png").status_code, 204)
        for url in ("/api/server/alpha_1/map/tile/the_moon/0/0.png", "/api/server/nope/map/tile/overworld/0/0.png", "/api/server/alpha_1/map/tile/overworld/9999999/0.png",
                    "/api/server/alpha_1/map/tile/..%2f..%2fx/0/0.png"):
            self.assertEqual(owner.get(url).status_code, 404, url)
        self.assertEqual(self.renders, [])

    def test_a_region_that_is_still_changing_is_not_redrawn_every_request(self):
        owner = self.owner()
        owner.get("/api/server/alpha_1/map/tile/overworld/-1/0.png").close()
        self.source.write_bytes(region_file({(5, 5): ground_chunk(-27, 5), (6, 5): ground_chunk(-26, 5)}))
        owner.get("/api/server/alpha_1/map/tile/overworld/-1/0.png").close()
        self.assertEqual(len(self.renders), 1, "a fresh tile is served while the region file keeps changing")
        stamp = worldmap.cache_files("alpha_1", "overworld", -1, 0)[2]
        old = json.loads(stamp.read_text())
        old["at"] -= worldmap.REFRESH_AFTER + 5
        stamp.write_text(json.dumps(old))
        owner.get("/api/server/alpha_1/map/tile/overworld/-1/0.png").close()
        self.assertEqual(len(self.renders), 2, "after a couple of minutes the changed region is drawn again")

    def test_a_region_that_fails_to_draw_does_not_loop(self):
        owner = self.owner()
        with mock.patch.object(worldmap, "render_region", side_effect=RuntimeError("boom")), mock.patch.object(worldmap.log, "warning"):
            first = owner.get("/api/server/alpha_1/map/tile/overworld/-1/0.png")
            second = owner.get("/api/server/alpha_1/map/tile/overworld/-1/0.png")
        self.assertEqual((first.status_code, second.status_code), (204, 204))
        self.assertEqual(len(self.renders), 1, "a failure is remembered for a minute")

    def test_the_cache_can_be_cleared_with_the_files_permission(self):
        owner = self.owner()
        owner.get("/api/server/alpha_1/map/tile/overworld/-1/0.png").close()
        control = self.staff(owner, "ctl", "ctlpass12", permissions=["control"])
        files = self.staff(owner, "fil", "filpass12", permissions=["files"])
        self.assertEqual(control.delete("/api/server/alpha_1/map/cache").status_code, 403)
        reply = files.delete("/api/server/alpha_1/map/cache")
        self.assertEqual(reply.get_json(), {"ok": True, "removed": 2})
        self.assertFalse((worldmap.CACHE_DIR / "alpha_1").exists())

    def test_the_world_endpoint_lists_regions_spawn_and_players(self):
        owner = self.owner()
        body = owner.get("/api/server/alpha_1/map/world").get_json()
        self.assertEqual(body["world"], "world")
        self.assertEqual(body["dimensions"]["overworld"], {"regions": 1, "bounds": [-1, 0, -1, 0], "list": [[-1, 0]]})
        self.assertEqual(body["dimensions"]["the_end"]["regions"], 0)
        self.assertEqual(owner.get("/api/server/nope/map/world").status_code, 404)


class Markers(AppTestCase):
    def post(self, owner, **body):
        return owner.post("/api/server/alpha_1/map/markers", json=body)

    def test_places_belong_to_a_dimension_and_old_ones_to_the_overworld(self):
        folder = make_server()
        owner = self.owner()
        (folder / "map_markers.json").write_text(json.dumps([{"id": "old1", "label": "Old base", "kind": "base", "x": 1, "z": 2}]), encoding="utf-8")
        reply = self.post(owner, label="Nether hub", kind="portal", dim="the_nether", x=10, z=-4).get_json()
        self.assertTrue(reply["ok"])
        dims = {m["label"]: m["dim"] for m in reply["markers"]}
        self.assertEqual(dims, {"Old base": "overworld", "Nether hub": "the_nether"})

    def test_editing_renaming_and_moving(self):
        make_server()
        owner = self.owner()
        marker = self.post(owner, label="Farm", kind="farm", x=5, z=5).get_json()["marker"]
        edited = self.post(owner, edit=marker["id"], label="Iron farm", x=50).get_json()["marker"]
        self.assertEqual((edited["label"], edited["x"], edited["z"], edited["kind"]), ("Iron farm", 50.0, 5.0, "farm"), "only the fields that were sent change")
        self.assertEqual(self.post(owner, edit="nope", label="x").status_code, 404)
        self.assertEqual(self.post(owner, edit=marker["id"], label="  ").status_code, 400)

    def test_bad_input_is_refused(self):
        make_server()
        owner = self.owner()
        for body in ({"label": ""}, {"label": "x", "dim": "the_moon"}, {"label": "x", "x": "abc"}, {"label": "x", "x": float("inf")}, {"label": "x", "z": 10 ** 9}):
            reply = self.post(owner, **body)
            self.assertEqual(reply.status_code, 400, body)
        kind = self.post(owner, label="Odd", kind="Not A Kind!!").get_json()["marker"]["kind"]
        self.assertEqual(kind, "other")
        with mock.patch.object(rconmap, "MARKER_LIMIT", 2):
            self.post(owner, label="two")
            self.assertEqual(self.post(owner, label="three").status_code, 400)

    def test_removing_a_place(self):
        make_server()
        owner = self.owner()
        marker = self.post(owner, label="Gone").get_json()["marker"]
        self.assertEqual(self.post(owner, remove=marker["id"]).get_json()["markers"], [])


class RconPolling(AppTestCase):
    class FakeRcon:
        commands = []

        def __init__(self, *args):
            pass

        def __enter__(self):
            return self

        def __exit__(self, *args):
            return False

        def command(self, text):
            RconPolling.FakeRcon.commands.append(text)
            if text == "list":
                return "There are 2 of a max of 20 players online: Steve, Alex"
            name = text.split()[3]
            return {"Pos": f"{name} has the following entity data: [10.5d, 64.0d, -3.25d]", "Health": f"{name} has the following entity data: 18.5f",
                    "Dimension": f'{name} has the following entity data: "minecraft:the_nether"', "Rotation": f"{name} has the following entity data: [90.0f, 5.0f]"}[text.split()[4]]

    def setUp(self):
        super().setUp()
        folder = make_server(rcon={"enabled": True, "port": 25575, "password": "pw"})
        process = mock.Mock()
        process.poll.return_value = None
        state.running_servers["alpha_1"] = process
        self.addCleanup(lambda: state.running_servers.pop("alpha_1", None))
        rconmap._slow.clear()
        RconPolling.FakeRcon.commands = []
        patch = mock.patch.object(rconmap, "Rcon", RconPolling.FakeRcon)
        patch.start()
        self.addCleanup(patch.stop)

    def count(self, kind):
        return sum(1 for c in RconPolling.FakeRcon.commands if c.endswith(f" {kind}"))

    def test_the_reply_carries_everything_the_map_draws(self):
        snapshot = rconmap.rcon_player_snapshot("alpha_1")
        self.assertTrue(snapshot["ok"])
        self.assertEqual(snapshot["max"], 20)
        steve = snapshot["players"][0]
        self.assertEqual((steve["name"], steve["x"], steve["y"], steve["z"], steve["health"], steve["dimension"], steve["yaw"]), ("Steve", 10.5, 64.0, -3.25, 18.5, "minecraft:the_nether", 90.0))

    def test_slow_details_are_asked_for_less_often_than_positions(self):
        for _ in range(5):
            rconmap.rcon_player_snapshot("alpha_1")
        self.assertEqual(self.count("Pos"), 10)
        self.assertEqual(self.count("Health"), 2, "health is asked once per player, not once per poll")
        with mock.patch.object(rconmap.time, "time", return_value=rconmap.time.time() + rconmap.SLOW_TTL + 1):
            rconmap.rcon_player_snapshot("alpha_1")
        self.assertEqual(self.count("Health"), 4, "and again after the cache time")

    def test_players_who_leave_are_forgotten(self):
        rconmap.rcon_player_snapshot("alpha_1")
        self.assertEqual(len(rconmap._slow), 2)
        with mock.patch.object(RconPolling.FakeRcon, "command", lambda self, text: "There are 0 of a max of 20 players online: " if text == "list" else ""):
            rconmap.rcon_player_snapshot("alpha_1")
        self.assertEqual(rconmap._slow, {})

    def test_the_player_list_in_every_wording(self):
        self.assertEqual(rconmap.parse_player_list("There are 2 of a max of 20 players online: A, B"), (["A", "B"], 20))
        self.assertEqual(rconmap.parse_player_list("There are 0/20 players online:"), ([], 20))
        self.assertEqual(rconmap.parse_player_list("There are 1 of a max of 100 players online: Solo"), (["Solo"], 100))
        self.assertEqual(rconmap.parse_player_list(""), ([], None))
