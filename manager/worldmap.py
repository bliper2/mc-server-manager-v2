"""The terrain behind the live map, read straight from the world folder: top-down tiles drawn from the region files,
the world spawn, and where every player was last seen. None of it needs a plugin or RCON, and it works while the
server is stopped.

Region files (Anvil, .mca) hold 32 x 32 chunks. A tile is one region drawn at one pixel per block, plus a small
overview (one pixel per four blocks) for zoomed-out views. Drawing a region takes a few seconds in plain Python, so it
happens on a small background pool and the result is cached on disk; requests meanwhile get "still rendering"."""

import gzip
import hashlib
import json
import logging
import re
import struct
import threading
import time
import zlib
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime
from functools import lru_cache
from pathlib import Path

from flask import Response, abort, jsonify, request, send_file

from . import app
from .config import DATA_DIR
from .store import get_server_path

log = logging.getLogger("manager")

DIMENSIONS = ("overworld", "the_nether", "the_end")
REGION_BLOCKS = 512
MAX_CHUNK_BYTES = 24 * 1024 * 1024  # uncompressed; a chunk is normally well under 1 MB
MAX_REGION_FILE_BYTES = 400 * 1024 * 1024
NETHER_SLICE_Y = 70  # the nether's roof is bedrock, so the view is the first solid block below this height
CACHE_DIR = DATA_DIR / "map_cache"
REFRESH_AFTER = 120  # seconds a drawn tile may lag behind a region file that is still being written
FORCE_REFRESH_AFTER = 10
MAX_QUEUE = 96
LEVEL_NAME = re.compile(r"^[\w .\-]{1,64}$")


# ---------------------------------------------------------------- NBT
class NbtError(ValueError):
    pass


def parse_nbt(data: bytes) -> dict:
    """Parses an uncompressed NBT document into dicts, lists and numbers. Arrays of longs and ints become lists of ints."""
    unpack_from = struct.unpack_from
    end = len(data)
    pos = 0

    def take(count: int) -> int:
        nonlocal pos
        if count < 0 or pos + count > end:
            raise NbtError("truncated NBT data")
        start = pos
        pos += count
        return start

    def string() -> str:
        (length,) = unpack_from(">H", data, take(2))
        start = take(length)
        return data[start:start + length].decode("utf-8", "replace")

    def payload(kind: int, depth: int):
        if depth > 48:
            raise NbtError("NBT nested too deeply")
        if kind == 10:
            result = {}
            while True:
                tag = data[take(1)]
                if tag == 0:
                    return result
                name = string()
                result[name] = payload(tag, depth + 1)
        if kind == 3:
            return unpack_from(">i", data, take(4))[0]
        if kind == 1:
            return unpack_from(">b", data, take(1))[0]
        if kind == 8:
            return string()
        if kind == 9:
            item = data[take(1)]
            (count,) = unpack_from(">i", data, take(4))
            if count <= 0:
                return []
            if count > end - pos:
                raise NbtError("NBT list longer than the data")
            return [payload(item, depth + 1) for _ in range(count)]
        if kind == 12 or kind == 11:
            (count,) = unpack_from(">i", data, take(4))
            width = 8 if kind == 12 else 4
            if count < 0 or count * width > end - pos:
                raise NbtError("NBT array longer than the data")
            start = take(count * width)
            return list(unpack_from(f">{count}{'q' if kind == 12 else 'i'}", data, start))
        if kind == 6:
            return unpack_from(">d", data, take(8))[0]
        if kind == 5:
            return unpack_from(">f", data, take(4))[0]
        if kind == 4:
            return unpack_from(">q", data, take(8))[0]
        if kind == 2:
            return unpack_from(">h", data, take(2))[0]
        if kind == 7:
            (count,) = unpack_from(">i", data, take(4))
            start = take(count)
            return data[start:start + count]
        raise NbtError(f"unknown NBT tag {kind}")

    try:
        root = data[take(1)]
        if root != 10:
            raise NbtError("NBT root is not a compound")
        string()  # the root name, usually empty
        return payload(10, 0)
    except (struct.error, IndexError) as exc:
        raise NbtError("truncated NBT data") from exc


def gunzip_nbt(path: Path) -> dict:
    decompressor = zlib.decompressobj(31)
    data = decompressor.decompress(path.read_bytes(), MAX_CHUNK_BYTES)
    return parse_nbt(data)


def unwrap(entry):
    """Since Minecraft 1.21.5 a list may mix types: a non-compound entry is wrapped as {"": value}."""
    if isinstance(entry, dict) and len(entry) == 1 and "" in entry:
        return entry[""]
    return entry


# ---------------------------------------------------------------- region files
def read_region(path: Path):
    """Yields (chunk index 0-1023, chunk NBT) for every chunk the region holds that can be read. Chunks that are
    missing, compressed with a method this reader does not know (LZ4) or damaged are skipped."""
    if path.stat().st_size > MAX_REGION_FILE_BYTES:
        return
    raw = path.read_bytes()
    if len(raw) < 8192:
        return
    for index in range(1024):
        (entry,) = struct.unpack_from(">I", raw, index * 4)
        offset = (entry >> 8) * 4096
        if not entry or offset + 5 > len(raw):
            continue
        length, method = struct.unpack_from(">IB", raw, offset)
        body = raw[offset + 5: offset + 4 + length]
        if length < 2 or len(body) < length - 1:
            continue
        try:
            if method in (1, 2):
                decompressor = zlib.decompressobj(31 if method == 1 else 15)
                data = decompressor.decompress(body, MAX_CHUNK_BYTES)
                if decompressor.unconsumed_tail or not decompressor.eof:
                    continue  # bigger than the limit, or cut off: a damaged chunk is never half drawn
            elif method == 3:
                data = body
            else:
                continue
            yield index, parse_nbt(data)
        except (zlib.error, NbtError, struct.error):
            continue


# ---------------------------------------------------------------- colours
EXACT = {
    "grass_block": (104, 164, 60), "dirt": (134, 96, 67), "coarse_dirt": (119, 85, 59), "rooted_dirt": (130, 94, 66), "podzol": (91, 63, 24),
    "mycelium": (111, 99, 105), "mud": (62, 58, 62), "packed_mud": (142, 106, 79), "dirt_path": (148, 122, 65), "farmland": (115, 78, 45),
    "stone": (125, 125, 125), "cobblestone": (110, 110, 110), "mossy_cobblestone": (97, 118, 91), "andesite": (136, 136, 136), "diorite": (190, 190, 190),
    "granite": (150, 103, 85), "deepslate": (78, 78, 82), "cobbled_deepslate": (72, 72, 76), "tuff": (108, 109, 102), "calcite": (223, 224, 220),
    "dripstone_block": (134, 107, 92), "smooth_basalt": (72, 72, 78), "basalt": (74, 73, 78), "blackstone": (42, 36, 41), "bedrock": (85, 85, 85),
    "sand": (219, 207, 163), "red_sand": (190, 102, 33), "suspicious_sand": (219, 207, 163), "gravel": (131, 127, 126), "clay": (160, 166, 179),
    "snow": (245, 250, 250), "snow_block": (245, 250, 250), "powder_snow": (245, 250, 250), "ice": (145, 183, 253), "packed_ice": (141, 180, 250),
    "blue_ice": (116, 167, 253), "frosted_ice": (150, 190, 250), "water": (46, 78, 178), "lava": (207, 92, 15), "magma_block": (142, 63, 31),
    "netherrack": (111, 54, 53), "nether_bricks": (44, 21, 26), "soul_sand": (81, 62, 50), "soul_soil": (75, 57, 46), "crimson_nylium": (189, 48, 49),
    "warped_nylium": (43, 114, 101), "nether_wart_block": (114, 2, 2), "warped_wart_block": (22, 126, 134), "shroomlight": (240, 146, 70),
    "glowstone": (171, 131, 84), "ancient_debris": (94, 66, 58), "crying_obsidian": (32, 10, 60), "obsidian": (15, 11, 25),
    "end_stone": (219, 222, 158), "end_stone_bricks": (218, 224, 162), "purpur_block": (169, 125, 169), "purpur_pillar": (172, 129, 172), "chorus_plant": (93, 57, 93),
    "chorus_flower": (151, 120, 151), "sandstone": (216, 203, 155), "red_sandstone": (181, 97, 31), "smooth_sandstone": (223, 214, 170),
    "prismarine": (99, 171, 158), "prismarine_bricks": (99, 171, 158), "dark_prismarine": (51, 91, 75), "sea_lantern": (172, 199, 190),
    "bricks": (150, 97, 83), "stone_bricks": (122, 121, 122), "mossy_stone_bricks": (115, 121, 105), "quartz_block": (236, 230, 223),
    "hay_block": (166, 139, 12), "pumpkin": (198, 118, 24), "melon": (111, 145, 30), "moss_block": (89, 109, 45), "moss_carpet": (89, 109, 45),
    "glass": (175, 213, 219), "tinted_glass": (44, 38, 46), "cactus": (85, 127, 43), "sugar_cane": (148, 192, 101), "bamboo": (93, 144, 19),
    "lily_pad": (32, 128, 48), "kelp": (58, 120, 60), "kelp_plant": (58, 120, 60), "seagrass": (50, 130, 60), "tall_seagrass": (50, 130, 60),
    "bubble_column": (46, 78, 178), "cobweb": (220, 220, 220), "bone_block": (229, 225, 207), "slime_block": (111, 192, 91), "honey_block": (251, 185, 52),
    "copper_block": (192, 107, 79), "iron_block": (220, 220, 220), "gold_block": (249, 236, 77), "diamond_block": (98, 219, 214), "emerald_block": (42, 203, 87),
    "coal_block": (16, 16, 16), "lapis_block": (31, 67, 140), "redstone_block": (171, 27, 9), "tnt": (219, 68, 54), "mud_bricks": (137, 104, 79),
}
LEAF_COLOURS = {"oak": (62, 126, 38), "spruce": (58, 94, 58), "birch": (112, 148, 64), "jungle": (48, 158, 22), "acacia": (98, 142, 38), "dark_oak": (44, 100, 28),
                "mangrove": (84, 142, 42), "cherry": (232, 168, 198), "azalea": (100, 124, 48), "flowering_azalea": (130, 124, 80), "pale_oak": (196, 206, 186)}
WOOD_COLOURS = {"oak": (162, 130, 78), "spruce": (114, 84, 48), "birch": (196, 176, 118), "jungle": (160, 115, 80), "acacia": (168, 90, 50), "dark_oak": (66, 43, 20),
                "mangrove": (117, 54, 48), "cherry": (226, 178, 172), "bamboo": (193, 173, 80), "crimson": (126, 58, 86), "warped": (43, 104, 99), "pale_oak": (228, 218, 210)}
DYES = {"white": (207, 213, 214), "orange": (224, 97, 1), "magenta": (169, 48, 159), "light_blue": (36, 137, 199), "yellow": (240, 175, 21), "lime": (94, 168, 24),
        "pink": (214, 101, 143), "gray": (55, 58, 62), "light_gray": (125, 125, 115), "cyan": (21, 119, 136), "purple": (100, 31, 156), "blue": (45, 47, 143),
        "brown": (96, 60, 32), "green": (73, 91, 36), "red": (142, 32, 32), "black": (8, 10, 15)}
DYED = ("_concrete_powder", "_concrete", "_wool", "_terracotta", "_stained_glass_pane", "_stained_glass", "_carpet", "_shulker_box", "_glazed_terracotta", "_candle_cake")
# Things that sit on top of the ground without being the ground: the map shows what is under them.
DECOR = ("short_grass", "tall_grass", "fern", "poppy", "dandelion", "orchid", "allium", "bluet", "tulip", "oxeye", "cornflower", "lily_of_the_valley", "sunflower",
         "lilac", "rose_bush", "peony", "sapling", "propagule", "vine", "leaf_litter", "dead_bush", "bush", "torch", "rail", "button", "lever", "pressure_plate",
         "tripwire", "fire", "lichen", "roots", "sprouts", "fungus", "flower", "pitcher", "wildflowers", "petals", "light", "sign", "carpet_of", "frogspawn", "turtle_egg")
AIR = ("air", "cave_air", "void_air", "structure_void")
WATER_LIKE = ("water", "bubble_column", "kelp", "kelp_plant", "seagrass", "tall_seagrass")
WATER_COLOUR = (28, 56, 150)


def short_name(entry) -> str:
    """A palette entry as a bare block name: 'minecraft:oak_log' -> 'oak_log'. Handles strings, {id, properties}, {Name, Properties} and wrapped strings."""
    entry = unwrap(entry)
    if isinstance(entry, dict):
        entry = entry.get("id") or entry.get("Name") or ""
    return str(entry).split("[", 1)[0].split(":", 1)[-1]


@lru_cache(maxsize=4096)
def block_colour(name: str):
    exact = EXACT.get(name)
    if exact:
        return exact
    for suffix in DYED:
        if name.endswith(suffix):
            return DYES.get(name[:-len(suffix)], (128, 128, 128))
    for family, colour in LEAF_COLOURS.items():
        if name == f"{family}_leaves":
            return colour
    for family, colour in WOOD_COLOURS.items():
        if name.startswith(family + "_"):
            rest = name[len(family) + 1:]
            if rest.startswith(("planks", "slab", "stairs", "fence", "door", "trapdoor", "button", "pressure", "sign", "hanging", "chest", "shelf")):
                return colour
            if rest.startswith(("log", "wood", "stem", "hyphae", "block")):
                return tuple(max(0, int(v * 0.72)) for v in colour)
    if name.endswith("_ore"):
        return (96, 96, 98) if name.startswith("deepslate_") else (122, 122, 122)
    for key, colour in (("terracotta", (152, 94, 67)), ("sandstone", (216, 203, 155)), ("coral", (200, 90, 140)), ("copper", (192, 107, 79)), ("deepslate", (78, 78, 82)),
                        ("stone", (125, 125, 125)), ("brick", (150, 97, 83)), ("quartz", (236, 230, 223)), ("prismarine", (99, 171, 158)), ("nether", (111, 54, 53)),
                        ("end_", (219, 222, 158)), ("purpur", (169, 125, 169)), ("glass", (175, 213, 219)), ("mud", (62, 58, 62)), ("dirt", (134, 96, 67)),
                        ("sand", (219, 207, 163)), ("snow", (245, 250, 250)), ("ice", (145, 183, 253)), ("leaves", (62, 126, 38)), ("grass", (104, 164, 60))):
        if key in name:
            return colour
    digest = hashlib.md5(name.encode()).digest()
    return (70 + digest[0] % 150, 70 + digest[1] % 150, 70 + digest[2] % 150)


def is_decor(name: str) -> bool:
    return any(word in name for word in DECOR)


# ---------------------------------------------------------------- chunk -> columns
def height_bits(count: int) -> int:
    for bits in range(1, 17):
        if -(-256 // (64 // bits)) == count:
            return bits
    return 9


def read_entry(longs, bits: int, index: int) -> int:
    per_long = 64 // bits
    return (longs[index // per_long] >> ((index % per_long) * bits)) & ((1 << bits) - 1)


class ChunkColumns:
    """Looks blocks up in one chunk without unpacking whole sections: each lookup reads one entry of the packed data."""

    def __init__(self, nbt: dict):
        self.sections = {}
        for section in nbt.get("sections") or []:
            states = section.get("block_states") if isinstance(section, dict) else None
            if isinstance(states, dict) and states.get("palette"):
                names = [short_name(entry) for entry in states["palette"]]
                self.sections[section.get("Y", 0)] = (names, states.get("data"), max(4, (len(names) - 1).bit_length()))
        self.min_y = int(nbt.get("yPos", -4)) * 16
        maps = nbt.get("Heightmaps") or {}
        self.surface = self._heights(maps.get("WORLD_SURFACE"))
        self.floor = self._heights(maps.get("OCEAN_FLOOR"))

    @staticmethod
    def _heights(longs):
        if not longs:
            return None
        bits = height_bits(len(longs))
        return [read_entry(longs, bits, index) for index in range(256)]

    def block(self, x: int, y: int, z: int) -> str:
        section = self.sections.get(y >> 4)
        if section is None:
            return "air"
        names, data, bits = section
        if len(names) == 1 or not data:
            return names[0]
        try:
            return names[read_entry(data, bits, ((y & 15) << 8) | (z << 4) | x)]
        except IndexError:
            return "air"

    def top_air_free(self, x: int, z: int, start: int, stop: int):
        """The first solid, non-air block scanning down from `start`: (y, name), used where the roof would hide the view."""
        y = start
        while y >= stop:
            section = self.sections.get(y >> 4)
            if section is None or (len(section[0]) == 1 and section[0][0] in AIR):
                y = ((y >> 4) << 4) - 1  # nothing in this whole section: step to the top of the one below
                continue
            name = self.block(x, y, z)
            if name not in AIR and not is_decor(name) and name not in WATER_LIKE:
                return y, name
            y -= 1
        return None


def chunk_pixels(nbt: dict, dimension: str):
    """[(red, green, blue, height)] for the chunk's 256 columns in z-major order, or None if the chunk cannot be drawn."""
    if str(nbt.get("Status", "minecraft:full")).split(":")[-1] != "full":
        return None
    chunk = ChunkColumns(nbt)
    if not chunk.sections:
        return None
    columns = []
    for index in range(256):
        z, x = divmod(index, 16)
        colour = height = None
        if dimension == "the_nether":
            found = chunk.top_air_free(x, z, NETHER_SLICE_Y, chunk.min_y)
            if found:
                height, name = found
                colour = block_colour(name)
        elif chunk.surface:
            top = chunk.surface[index] + chunk.min_y - 1
            name = chunk.block(x, top, z)
            guard = 0
            while name in AIR or (is_decor(name) and guard < 4):
                if name in AIR and guard > 6:
                    break
                top -= 1
                name = chunk.block(x, top, z)
                guard += 1
            height = top
            if name in WATER_LIKE:
                floor_y = (chunk.floor[index] + chunk.min_y - 1) if chunk.floor else top - 4
                floor_name = chunk.block(x, floor_y, z)
                floor = block_colour(floor_name) if floor_name not in AIR and floor_name not in WATER_LIKE else (90, 85, 70)
                mix = min(0.92, 0.38 + max(0, top - floor_y) * 0.07)
                colour = tuple(round(floor[i] * (1 - mix) + WATER_COLOUR[i] * mix) for i in range(3))
            elif name not in AIR:
                colour = block_colour(name)
        columns.append((*colour, height) if colour else None)
    return columns


# ---------------------------------------------------------------- region -> PNG
def png_rgba(width: int, height: int, rgba: bytes) -> bytes:
    stride = width * 4
    raw = b"".join(b"\x00" + rgba[y * stride:(y + 1) * stride] for y in range(height))

    def chunk(tag: bytes, body: bytes) -> bytes:
        return struct.pack(">I", len(body)) + tag + body + struct.pack(">I", zlib.crc32(tag + body) & 0xFFFFFFFF)

    return (b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", struct.pack(">IIBBBBB", width, height, 8, 6, 0, 0, 0))
            + chunk(b"IDAT", zlib.compress(raw, 4)) + chunk(b"IEND", b""))


def render_region(path: Path, dimension: str):
    """(full-size PNG, overview PNG, chunks drawn) for one region file. The full tile is 512 x 512, one pixel per block."""
    size = REGION_BLOCKS
    pixels = bytearray(size * size * 4)
    heights = [None] * (size * size)
    drawn = 0
    for index, nbt in read_region(path):
        columns = chunk_pixels(nbt, dimension)
        if not columns:
            continue
        drawn += 1
        base_z, base_x = (index // 32) * 16, (index % 32) * 16
        for column, value in enumerate(columns):
            if value is None:
                continue
            z, x = divmod(column, 16)
            at = (base_z + z) * size + base_x + x
            pixels[at * 4:at * 4 + 4] = bytes((value[0], value[1], value[2], 255))
            heights[at] = value[3]
    # relief: a column higher than the one to its north is lit, a lower one shadowed, as on Minecraft's own maps
    for z in range(1, size):
        row = z * size
        for x in range(size):
            here, north = heights[row + x], heights[row - size + x]
            if here is None or north is None or here == north:
                continue
            factor = 1 + max(-5, min(5, here - north)) * 0.05
            at = (row + x) * 4
            pixels[at] = min(255, int(pixels[at] * factor))
            pixels[at + 1] = min(255, int(pixels[at + 1] * factor))
            pixels[at + 2] = min(255, int(pixels[at + 2] * factor))
    overview = bytearray((size // 4) * (size // 4) * 4)
    source, target = memoryview(pixels).cast("I"), memoryview(overview).cast("I")
    for y in range(size // 4):
        target[y * (size // 4):(y + 1) * (size // 4)] = source[(y * 4 + 2) * size + 2:(y * 4 + 3) * size:4]
    return png_rgba(size, size, bytes(pixels)), png_rgba(size // 4, size // 4, bytes(overview)), drawn


# ---------------------------------------------------------------- where the world is
def level_name(server_id: str) -> str:
    properties = get_server_path(server_id) / "server.properties"
    try:
        for line in properties.read_text(encoding="utf-8", errors="replace").splitlines():
            if line.startswith("level-name="):
                name = line.split("=", 1)[1].strip()
                if LEVEL_NAME.match(name) and not name.startswith("."):
                    return name
    except OSError:
        pass
    return "world"


def world_dir(server_id: str):
    root = get_server_path(server_id).resolve()
    folder = (root / level_name(server_id)).resolve()
    return folder if folder.is_dir() and root in folder.parents else None


def region_dir(server_id: str, dimension: str):
    """The folder holding a dimension's .mca files, for the current layout (world/dimensions/minecraft/<dim>) and the
    older ones (world/region, world/DIM-1, world/DIM1, and Paper's separate <world>_nether and <world>_the_end)."""
    world = world_dir(server_id)
    if world is None or dimension not in DIMENSIONS:
        return None
    root = get_server_path(server_id).resolve()
    name = level_name(server_id)
    legacy = {"overworld": ["region"], "the_nether": ["DIM-1/region"], "the_end": ["DIM1/region"]}[dimension]
    candidates = [world / "dimensions" / "minecraft" / dimension / "region"] + [world / sub for sub in legacy]
    if dimension != "overworld":
        suffix = "_nether" if dimension == "the_nether" else "_the_end"
        candidates += [root / f"{name}{suffix}" / sub for sub in legacy]
    return next((path for path in candidates if path.is_dir()), None)


REGION_FILE = re.compile(r"^r\.(-?\d+)\.(-?\d+)\.mca$")


def list_regions(server_id: str, dimension: str) -> list:
    folder = region_dir(server_id, dimension)
    found = []
    if folder:
        for path in folder.iterdir():
            match = REGION_FILE.match(path.name)
            if match and path.is_file() and path.stat().st_size >= 8192:
                found.append((int(match.group(1)), int(match.group(2))))
    return found


def dimension_name(value) -> str:
    if isinstance(value, int):
        return {0: "overworld", -1: "the_nether", 1: "the_end"}.get(value, "overworld")
    return str(value or "overworld").split(":")[-1]


def read_spawn(world: Path):
    try:
        level = gunzip_nbt(world / "level.dat")
    except (OSError, zlib.error, NbtError):
        return None
    data = level.get("data") or level.get("Data") or level
    spawn = data.get("spawn")
    if isinstance(spawn, dict) and isinstance(spawn.get("pos"), list) and len(spawn["pos"]) == 3:
        x, y, z = spawn["pos"]
        return {"x": x, "y": y, "z": z, "dim": dimension_name(spawn.get("dimension"))}
    if all(key in data for key in ("SpawnX", "SpawnZ")):
        return {"x": data["SpawnX"], "y": data.get("SpawnY", 64), "z": data["SpawnZ"], "dim": "overworld"}
    return None


def usernames(server_id: str) -> dict:
    names = {}
    for file in ("usercache.json", "ops.json", "whitelist.json"):
        try:
            for entry in json.loads((get_server_path(server_id) / file).read_text(encoding="utf-8")):
                if isinstance(entry, dict) and entry.get("uuid") and entry.get("name"):
                    names[str(entry["uuid"]).lower()] = str(entry["name"])
        except (OSError, ValueError):
            continue
    return names


def read_player_files(server_id: str, world: Path) -> list:
    """Where every player who has ever joined was last seen, from their data file (so it works with the server off)."""
    folder = next((world / sub for sub in ("players/data", "playerdata") if (world / sub).is_dir()), None)
    if folder is None:
        return []
    names = usernames(server_id)
    players = []
    for path in sorted(folder.glob("*.dat"), key=lambda p: p.stat().st_mtime, reverse=True)[:200]:
        try:
            data = gunzip_nbt(path)
        except (OSError, zlib.error, NbtError):
            continue
        position = data.get("Pos")
        if not (isinstance(position, list) and len(position) == 3):
            continue
        entry = {"uuid": path.stem, "name": names.get(path.stem.lower()) or path.stem[:8], "x": round(position[0], 1), "y": round(position[1], 1), "z": round(position[2], 1),
                 "dim": dimension_name(data.get("Dimension")), "health": data.get("Health"), "seen": datetime.fromtimestamp(path.stat().st_mtime).isoformat(timespec="seconds")}
        rotation = data.get("Rotation")
        if isinstance(rotation, list) and rotation:
            entry["yaw"] = round(float(rotation[0]) % 360, 1)
        respawn = data.get("respawn") if isinstance(data.get("respawn"), dict) else None
        if respawn and isinstance(respawn.get("pos"), list) and len(respawn["pos"]) == 3:
            entry["bed"] = {"x": respawn["pos"][0], "y": respawn["pos"][1], "z": respawn["pos"][2], "dim": dimension_name(respawn.get("dimension"))}
        elif all(key in data for key in ("SpawnX", "SpawnZ")):
            entry["bed"] = {"x": data["SpawnX"], "y": data.get("SpawnY", 64), "z": data["SpawnZ"], "dim": dimension_name(data.get("SpawnDimension"))}
        players.append(entry)
    return players


def world_info(server_id: str) -> dict:
    world = world_dir(server_id)
    if world is None:
        return {"world": None, "dimensions": {}, "spawn": None, "players": []}
    dimensions = {}
    for dimension in DIMENSIONS:
        regions = list_regions(server_id, dimension)
        bounds = None
        if regions:
            bounds = [min(r[0] for r in regions), min(r[1] for r in regions), max(r[0] for r in regions), max(r[1] for r in regions)]
        dimensions[dimension] = {"regions": len(regions), "bounds": bounds, "list": [list(r) for r in regions[:6000]]}
    return {"world": world.name, "dimensions": dimensions, "spawn": read_spawn(world), "players": read_player_files(server_id, world)}


# ---------------------------------------------------------------- the render queue and the disk cache
_pool = ThreadPoolExecutor(max_workers=2, thread_name_prefix="mapgen")
_inflight = {}
_failed = {}
_lock = threading.RLock()  # re-entrant: a future that is already done runs its callback inside the submitting thread


def cache_files(server_id: str, dimension: str, rx: int, rz: int):
    base = CACHE_DIR / server_id / dimension
    return base / f"r.{rx}.{rz}.1.png", base / f"r.{rx}.{rz}.4.png", base / f"r.{rx}.{rz}.json"


def _render_job(server_id: str, dimension: str, rx: int, rz: int, source: Path):
    full_png, overview_png, stamp_file = cache_files(server_id, dimension, rx, rz)
    before = source.stat()
    started = time.time()
    full, overview, drawn = render_region(source, dimension)
    full_png.parent.mkdir(parents=True, exist_ok=True)
    full_png.write_bytes(full)
    overview_png.write_bytes(overview)
    stamp_file.write_text(json.dumps({"mtime_ns": before.st_mtime_ns, "size": before.st_size, "at": time.time(), "chunks": drawn, "seconds": round(time.time() - started, 1)}), encoding="utf-8")


def _finished(key, future):
    with _lock:
        _inflight.pop(key, None)
        error = future.exception()
        if error:
            _failed[key] = time.time()
            log.warning("Map tile %s failed: %s", key, error)


def tile_state(server_id: str, dimension: str, rx: int, rz: int, lod: int, force: bool = False):
    """('ready', path) | ('rendering', None) | ('none', None). Stale tiles are served while a fresh one is drawn behind them."""
    folder = region_dir(server_id, dimension)
    source = folder / f"r.{rx}.{rz}.mca" if folder else None
    if source is None or not source.is_file() or source.stat().st_size < 8192:
        return "none", None
    full_png, overview_png, stamp_file = cache_files(server_id, dimension, rx, rz)
    wanted = overview_png if lod == 4 else full_png
    info = source.stat()
    stale = True
    drawn_at = 0
    if wanted.is_file() and stamp_file.is_file():
        try:
            stamp = json.loads(stamp_file.read_text(encoding="utf-8"))
            drawn_at = stamp.get("at", 0)
            current = stamp.get("mtime_ns") == info.st_mtime_ns and stamp.get("size") == info.st_size
            stale = not current and time.time() - drawn_at > (FORCE_REFRESH_AFTER if force else REFRESH_AFTER)
            if force and time.time() - drawn_at > FORCE_REFRESH_AFTER:
                stale = True
        except (OSError, ValueError):
            stale = True
    key = (server_id, dimension, rx, rz)
    if stale:
        with _lock:
            failed_at = _failed.get(key, 0)
            if key not in _inflight and len(_inflight) < MAX_QUEUE and time.time() - failed_at > 60:
                future = _pool.submit(_render_job, server_id, dimension, rx, rz, source)
                _inflight[key] = future
                future.add_done_callback(lambda done, key=key: _finished(key, done))
    if wanted.is_file():
        return "ready", wanted
    return ("none", None) if _failed.get(key) and key not in _inflight else ("rendering", None)


def clear_cache(server_id: str) -> int:
    import shutil
    folder = CACHE_DIR / server_id
    count = sum(1 for _ in folder.rglob("*.png")) if folder.is_dir() else 0
    shutil.rmtree(folder, ignore_errors=True)
    with _lock:
        for key in [k for k in _failed if k[0] == server_id]:
            _failed.pop(key, None)
    return count


# ---------------------------------------------------------------- routes
@app.route("/api/server/<sid>/map/world")
def api_map_world(sid):
    if not get_server_path(sid).is_dir():
        return jsonify({"ok": False, "error": "Server not found"}), 404
    return jsonify({"ok": True, **world_info(sid)})


@app.route("/api/server/<sid>/map/tile/<dim>/<int(signed=True):rx>/<int(signed=True):rz>.png")  # regions west and north of the origin are negative
def api_map_tile(sid, dim, rx, rz):
    if dim not in DIMENSIONS or not get_server_path(sid).is_dir() or abs(rx) > 100000 or abs(rz) > 100000:
        abort(404)
    lod = 4 if request.args.get("lod") == "4" else 1
    state, path = tile_state(sid, dim, rx, rz, lod, force=request.args.get("force") == "1")
    if state == "none":
        return Response(status=204)
    if state == "rendering":
        response = jsonify({"ok": True, "rendering": True})
        response.status_code = 202
        response.headers["Retry-After"] = "2"
        return response
    return send_file(path, mimetype="image/png", conditional=True, max_age=0)


@app.route("/api/server/<sid>/map/cache", methods=["DELETE"])
def api_map_cache_clear(sid):
    if not get_server_path(sid).is_dir():
        return jsonify({"ok": False, "error": "Server not found"}), 404
    return jsonify({"ok": True, "removed": clear_cache(sid)})
