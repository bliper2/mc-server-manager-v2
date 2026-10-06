"""Builds the bundled server logo library into static/logos (128 x 128 PNG files).

Run it again only to change the library: python tools/make_logos.py   (needs Pillow; the manager itself does not).
File names are <category>-<name>[-<colour>].png, which the manager turns into the labels and filters of the logo picker.
Everything here is drawn from code, so the logos are original artwork with no licence attached.
"""
import math
import sys
from pathlib import Path

from PIL import Image, ImageChops, ImageDraw, ImageFilter

OUT = Path(__file__).resolve().parent.parent / "static" / "logos"
SIZE, BIG = 128, 512


def mix(a, b, t):
    return tuple(round(a[i] + (b[i] - a[i]) * t) for i in range(3))


def shade(color, amount):
    """amount > 0 lightens towards white, amount < 0 darkens towards black."""
    return mix(color, (255, 255, 255), amount) if amount > 0 else mix(color, (0, 0, 0), -amount)


# ---------------------------------------------------------------- emblems: a badge shape, a colour pair and a glyph
PALETTES = {
    "emerald": ((52, 211, 153), (4, 120, 87), (240, 253, 244)),
    "diamond": ((103, 232, 249), (14, 116, 144), (236, 254, 255)),
    "redstone": ((248, 113, 113), (153, 27, 27), (254, 242, 242)),
    "gold": ((245, 158, 11), (146, 64, 14), (255, 251, 235)),
    "amethyst": ((167, 139, 250), (91, 33, 182), (250, 245, 255)),
    "obsidian": ((71, 85, 105), (15, 23, 42), (226, 232, 240)),
    "ember": ((251, 146, 60), (194, 65, 12), (255, 247, 237)),
    "ice": ((125, 211, 252), (3, 105, 161), (240, 249, 255)),
    "rose": ((251, 113, 133), (159, 18, 57), (255, 241, 242)),
    "lime": ((163, 230, 53), (63, 98, 18), (247, 254, 231)),
    "sunset": ((251, 113, 133), (109, 40, 217), (255, 255, 255)),
    "sapphire": ((96, 165, 250), (30, 64, 175), (239, 246, 255)),
    "midnight": ((99, 102, 241), (30, 27, 75), (238, 242, 255)),
}
SHAPES = ("circle", "squircle", "hexagon", "badge")


def unit(points):
    """Glyphs are drawn in a 0-100 box; this maps them onto the supersampled canvas, leaving a margin for the badge."""
    scale = BIG * 0.56 / 100
    offset = BIG * 0.22
    return [(offset + x * scale, offset + y * scale) for x, y in points]


def poly(draw, points, fill):
    draw.polygon(unit(points), fill=fill)


def disc(draw, cx, cy, r, fill):
    (x0, y0), (x1, y1) = unit([(cx - r, cy - r), (cx + r, cy + r)])
    draw.ellipse([x0, y0, x1, y1], fill=fill)


def bar(draw, x0, y0, x1, y1, fill, radius=3):
    (a, b), (c, d) = unit([(x0, y0), (x1, y1)])
    draw.rounded_rectangle([a, b, c, d], radius=radius * BIG * 0.0056, fill=fill)


def star_points(cx, cy, outer, inner, points=5, turn=-90):
    result = []
    for i in range(points * 2):
        radius = outer if i % 2 == 0 else inner
        angle = math.radians(turn + i * 180 / points)
        result.append((cx + radius * math.cos(angle), cy + radius * math.sin(angle)))
    return result


def g_star(d, fg, acc):
    poly(d, star_points(50, 54, 46, 20), fg)
    poly(d, star_points(50, 54, 28, 12), acc)


def g_bolt(d, fg, acc):
    poly(d, [(60, 2), (18, 58), (44, 58), (36, 98), (82, 38), (56, 38)], fg)
    poly(d, [(60, 2), (44, 58), (56, 38)], acc)


def g_crown(d, fg, acc):
    poly(d, [(8, 80), (8, 32), (30, 56), (50, 20), (70, 56), (92, 32), (92, 80)], fg)
    for x, y in ((8, 30), (50, 16), (92, 30)):
        disc(d, x, y, 6, acc)
    bar(d, 8, 82, 92, 94, acc)
    for x in (30, 50, 70):
        disc(d, x, 70, 5, acc)


def g_drop(d, fg, acc):
    disc(d, 50, 64, 32, fg)
    poly(d, [(50, 2), (21, 52), (79, 52)], fg)
    disc(d, 38, 66, 9, acc)


def g_cube(d, fg, acc):
    poly(d, [(50, 6), (90, 28), (50, 50), (10, 28)], shade(fg, 0.0))
    poly(d, [(10, 28), (50, 50), (50, 96), (10, 72)], shade(fg, -0.28))
    poly(d, [(90, 28), (50, 50), (50, 96), (90, 72)], shade(fg, -0.5))


def g_gem(d, fg, acc):
    for points, amount in (([(30, 16), (70, 16), (60, 42), (40, 42)], 0.0), ([(30, 16), (40, 42), (4, 42)], -0.18),
                           ([(70, 16), (96, 42), (60, 42)], -0.3), ([(4, 42), (40, 42), (50, 94)], -0.45),
                           ([(40, 42), (60, 42), (50, 94)], -0.2), ([(60, 42), (96, 42), (50, 94)], -0.55)):
        poly(d, points, shade(fg, amount))


def g_shield(d, fg, acc):
    poly(d, [(50, 4), (90, 16), (86, 58), (50, 98), (14, 58), (10, 16)], fg)
    poly(d, [(50, 16), (76, 24), (73, 54), (50, 82), (27, 54), (24, 24)], acc)
    poly(d, star_points(50, 50, 16, 7), fg)


def g_moon(d, fg, acc):
    disc(d, 50, 50, 42, fg)
    disc(d, 68, 38, 36, (0, 0, 0, 0))
    poly(d, star_points(78, 66, 11, 4.5), acc)


def g_tree(d, fg, acc):
    bar(d, 44, 70, 56, 96, acc)
    for top, bottom, half in ((2, 38, 24), (24, 60, 32), (46, 82, 40)):
        poly(d, [(50, top), (50 + half, bottom), (50 - half, bottom)], fg)


def g_cloud(d, fg, acc):
    disc(d, 32, 62, 22, fg)
    disc(d, 56, 46, 28, fg)
    disc(d, 76, 64, 20, fg)
    bar(d, 30, 62, 78, 84, fg, 10)
    bar(d, 30, 74, 78, 84, acc, 5)


def g_gear(d, fg, acc):
    for i in range(8):
        angle = math.radians(i * 45)
        cx, cy = 50 + 38 * math.cos(angle), 50 + 38 * math.sin(angle)
        corners = [(-9, -8), (9, -8), (9, 8), (-9, 8)]
        poly(d, [(cx + x * math.cos(angle) - y * math.sin(angle), cy + x * math.sin(angle) + y * math.cos(angle)) for x, y in corners], fg)
    disc(d, 50, 50, 34, fg)
    disc(d, 50, 50, 15, acc)


def g_castle(d, fg, acc):
    bar(d, 14, 40, 86, 94, fg, 2)
    for x in (8, 38, 66):
        bar(d, x, 14, x + 26, 46, fg, 2)
    for x in (8, 38, 66):
        for step in (0, 10, 20):
            bar(d, x + step, 4, x + step + 6, 18, fg, 1)
    poly(d, [(40, 94), (40, 66), (50, 56), (60, 66), (60, 94)], acc)


def g_heart(d, fg, acc):
    points = []
    for i in range(160):
        t = i / 160 * 2 * math.pi
        points.append((50 + 3.0 * 16 * math.sin(t) ** 3,
                       48 - 2.7 * (13 * math.cos(t) - 5 * math.cos(2 * t) - 2 * math.cos(3 * t) - math.cos(4 * t))))
    poly(d, points, fg)
    disc(d, 30, 34, 7, acc)


def g_leaf(d, fg, acc):
    left, right = [], []
    for i in range(41):
        y = 4 + 92 * i / 40
        width = 32 * math.sin(math.pi * i / 40) ** 0.8
        left.append((50 - width, y))
        right.append((50 + width, y))
    poly(d, left + right[::-1], fg)
    poly(d, [(48.5, 18), (51.5, 18), (51.5, 96), (48.5, 96)], acc)


def g_sword(d, fg, acc):
    poly(d, [(50, 0), (60, 14), (60, 62), (40, 62), (40, 14)], fg)
    poly(d, [(50, 0), (50, 62), (40, 62), (40, 14)], shade(fg, -0.22))
    bar(d, 22, 62, 78, 72, acc)
    bar(d, 45, 72, 55, 90, shade(acc, -0.2))
    disc(d, 50, 94, 6, acc)


def g_pickaxe(d, fg, acc):
    bar(d, 45, 26, 55, 98, acc, 4)
    outer, inner = [], []
    for i in range(33):
        angle = math.radians(200 + 140 * i / 32)
        outer.append((50 + 46 * math.cos(angle), 56 + 46 * math.sin(angle)))
        inner.append((50 + 32 * math.cos(angle), 56 + 32 * math.sin(angle) * 0.9))
    poly(d, outer + inner[::-1], fg)


GLYPHS = {
    "combat": (("sword", g_sword, 45), ("shield", g_shield, 0), ("bolt", g_bolt, 0), ("castle", g_castle, 0)),
    "royal": (("crown", g_crown, 0), ("star", g_star, 0), ("gem", g_gem, 0), ("heart", g_heart, 0)),
    "nature": (("leaf", g_leaf, 40), ("tree", g_tree, 0), ("drop", g_drop, 0), ("cloud", g_cloud, 0), ("moon", g_moon, 0)),
    "build": (("cube", g_cube, 0), ("pickaxe", g_pickaxe, 40), ("gear", g_gear, 0)),
}


def badge_mask(shape):
    mask = Image.new("L", (BIG, BIG), 0)
    draw = ImageDraw.Draw(mask)
    m, c = 18, BIG / 2
    if shape == "circle":
        draw.ellipse([m, m, BIG - m, BIG - m], fill=255)
    elif shape == "squircle":
        draw.rounded_rectangle([m, m, BIG - m, BIG - m], radius=BIG * 0.27, fill=255)
    elif shape == "hexagon":
        draw.polygon([(c + (c - m) * math.cos(math.radians(60 * i - 90)), c + (c - m) * math.sin(math.radians(60 * i - 90))) for i in range(6)], fill=255)
        mask = mask.filter(ImageFilter.GaussianBlur(3)).point(lambda v: 255 if v > 127 else 0)
    else:
        draw.polygon([(c, m), (BIG - m, m + 50), (BIG - m - 20, BIG * 0.62), (c, BIG - m), (m + 20, BIG * 0.62), (m, m + 50)], fill=255)
        mask = mask.filter(ImageFilter.GaussianBlur(14)).point(lambda v: 255 if v > 127 else 0)
    return mask


def gradient(top, bottom):
    column = Image.new("RGB", (1, BIG))
    column.putdata([mix(top, bottom, y / (BIG - 1)) for y in range(BIG)])
    return column.resize((BIG, BIG))


def emblem(shape, palette, glyph, shine=None):
    """shine (0 to 1) draws a light stripe at that point of its sweep across the badge, for the animated logos."""
    top, bottom, fg = PALETTES[palette]
    _, draw_glyph, rotation = glyph
    mask = badge_mask(shape)
    body = gradient(top, bottom).convert("RGBA")
    # a lighter rim and a soft gloss on the upper half give the flat colour some depth
    rim = ImageChops.subtract(mask, mask.filter(ImageFilter.MinFilter(15)))
    body.paste(Image.new("RGBA", (BIG, BIG), (255, 255, 255, 70)), mask=rim)
    gloss = Image.new("L", (BIG, BIG), 0)
    ImageDraw.Draw(gloss).ellipse([-BIG * 0.2, -BIG * 0.55, BIG * 1.2, BIG * 0.5], fill=46)
    body.paste(Image.new("RGBA", (BIG, BIG), (255, 255, 255, 255)), mask=ImageChops.multiply(gloss, mask))

    layer = Image.new("RGBA", (BIG, BIG), (0, 0, 0, 0))
    draw_glyph(ImageDraw.Draw(layer), fg + (255,), shade(bottom, -0.05) + (255,))
    if rotation:
        layer = layer.rotate(-rotation, resample=Image.BICUBIC)
    shadow = Image.new("RGBA", (BIG, BIG), (0, 0, 0, 0))
    shadow.paste((0, 0, 0, 120), mask=layer.getchannel("A"))
    shadow = ImageChops.offset(shadow.filter(ImageFilter.GaussianBlur(7)), 0, 9)
    body = Image.alpha_composite(Image.alpha_composite(body, shadow), layer)
    if shine is not None:
        band = Image.new("L", (BIG, BIG), 0)
        left = -BIG * 0.5 + shine * BIG * 1.7
        ImageDraw.Draw(band).polygon([(left, 0), (left + 90, 0), (left + 90 - BIG * 0.45, BIG), (left - BIG * 0.45, BIG)], fill=255)
        band = ImageChops.multiply(band.filter(ImageFilter.GaussianBlur(14)), mask).point(lambda v: int(v * 0.6))
        body.paste(Image.new("RGBA", (BIG, BIG), (255, 255, 255, 255)), mask=band)

    out = Image.new("RGBA", (BIG, BIG), (0, 0, 0, 0))
    out.paste(body, mask=mask)
    return out.resize((SIZE, SIZE), Image.LANCZOS)


# ---------------------------------------------------------------- pixel art: 16 x 16 sprites on a dark tile
COLOURS = {
    "k": (22, 22, 32), "w": (255, 255, 255), "l": (214, 218, 226), "g": (148, 154, 168), "d": (84, 90, 104),
    "b": (160, 104, 56), "B": (96, 58, 28), "y": (253, 224, 71), "Y": (202, 138, 4), "o": (251, 146, 60),
    "r": (239, 68, 68), "R": (153, 27, 27), "n": (251, 148, 170), "G": (74, 222, 128), "D": (22, 120, 60),
    "c": (103, 232, 249), "C": (14, 116, 144), "p": (192, 132, 252), "P": (107, 33, 168), "u": (96, 165, 250),
    "U": (30, 64, 175), "t": (120, 84, 50), "s": (214, 164, 108),
}
TILES = (((46, 50, 68), (20, 22, 32)), ((22, 70, 56), (10, 30, 28)), ((30, 52, 100), (12, 20, 48)),
         ((96, 36, 44), (38, 14, 20)), ((72, 40, 104), (28, 16, 46)), ((20, 72, 84), (10, 30, 38)))

SPRITES = {
    "sword": ("..............kk", ".............kwk", "............kwlk", "...........kwlk.", "..........kwlk..", ".........kwlk...",
              "..kk....kwlk....", "..kyk..kwlk.....", "...kykkwlk......", "....kyywk.......", ".....kyyk.......", "....kbkkyk......",
              "...kbk..kk......", "..kBk...........", ".kBk............", "..k............."),
    "pickaxe": ("................", "...kkkkkkk......", "..kllgggddk.....", ".klgkkkkkdk.....", ".kgk...kbkdk....", "..k....kbk.dk...",
                "......kbk..dk...", ".....kbk....k...", "....kbk.........", "...kbk..........", "..kBk...........", ".kBk............",
                ".kk.............", "................", "................", "................"),
    "heart": ("................", "..kkkk....kkkk..", ".krrrrk..krrrrk.", "krrwwrrkkrrrrrrk", "krwwrrrrrrrrrrRk", "krwrrrrrrrrrrrRk",
              "krrrrrrrrrrrrrRk", "krrrrrrrrrrrrRRk", ".krrrrrrrrrrRRk.", "..krrrrrrrrRRk..", "...krrrrrrRRk...", "....krrrrRRk....",
              ".....krrRRk.....", "......krRk......", ".......kk.......", "................"),
    "diamond": ("................", "................", "....kkkkkkkk....", "...kwwcccccck...", "..kwcccccccccck.", ".kkkkkkkkkkkkkk.",
                ".kcwccccccccCCk.", "..kcwcccccccCk..", "...kcwccccccCk..", "....kcwcccCCk...", ".....kccccCk....", "......kcccCk....",
                ".......kcCk.....", "........kk......", "................", "................"),
    "apple": ("................", ".........kkk....", "........kGGk....", ".......kDk......", "...kkkkkbkkkk...", "..krrrrrkrrrrk..",
              ".krwwrrrrrrrrRk.", ".krwrrrrrrrrrRk.", ".krrrrrrrrrrrRk.", ".krrrrrrrrrrRRk.", ".krrrrrrrrrrRRk.", "..krrrrrrrrRRk..",
              "..kRrrrrrrRRk...", "...kkRRRRRkk....", ".....kkkkk......", "................"),
    "potion": ("................", "......kkkk......", ".....kbbbbk.....", "......klwk......", "......klwk......", ".....kllwlk.....",
               "....kcccccck....", "...kccccwcccCk..", "..kcccccwccccCk.", "..kccccccccccCk.", "..kcccccccccCCk.", "..kCcccccccCCCk.",
               "...kCCCCCCCCCk..", "....kkkkkkkkk...", "................", "................"),
    "torch": ("................", "......kkk.......", ".....koyok......", "....koyyyok.....", "....koyyyok.....", "....kooyook.....",
              ".....kooook.....", "......kbbk......", "......kbtk......", "......kbtk......", "......kbtk......", "......kbtk......",
              "......kbtk......", "......kBBk......", ".......kk.......", "................"),
    "chest": ("................", "................", "..kkkkkkkkkkkk..", ".kbbbbbbbbbbbbk.", ".kbtttttttttbBk.", ".kbtttttttttbBk.",
              ".kkkkkkYYkkkkkk.", ".kbbbbbkYkbbbbk.", ".kbtttttkktttbk.", ".kbtttttttttbBk.", ".kbtttttttttbBk.", ".kbbbbbbbbbbbBk.",
              "..kkkkkkkkkkkk..", "................", "................", "................"),
    "grass": ("................", "................", "..kkkkkkkkkkkk..", ".kGGDGGGGDGGGGk.", ".kGGGGDGGGGDGGk.", ".kDGGGGGGGGGGDk.",
              ".kbDbbDbbbDbbbk.", ".kbbbbbbbbbbbbk.", ".kbbtbbbbbtbbBk.", ".kbbbbbbbbbbbBk.", ".kbtbbbbtbbbbBk.", ".kbbbbbbbbbbBBk.",
              "..kkkkkkkkkkkk..", "................", "................", "................"),
    "star": ("................", ".......kk.......", "......kyyk......", "......kyyk......", ".....kyyyyk.....", "kkkkkkyyyykkkkkk",
             ".kyyyyyyyyyyyyk.", "..kyyyyyyyyyyk..", "...kyyyyyyyyk...", "....kyyyyyyk....", "....kyyyyyyk....", "...kyyyYYyyyk...",
             "...kyyk..kyyk...", "..kyyk....kyyk..", "..kkk......kkk..", "................"),
    "key": ("................", "..kkkk..........", ".kyyyyk.........", "kyykkyyk........", "kyk..kyk........", "kyk..kyk........",
            "kyyk.kyk........", ".kyyyyk.kk......", "..kkkkYykYk.....", ".......kYykk....", "........kYyk....", ".........kYYk...",
            ".........kYk....", "..........k.....", "................", "................"),
    "skull": ("................", "....kkkkkkkk....", "..kkwwwwwwwwkk..", ".kwwwwwwwwwwwwk.", ".kwwwwwwwwwwwwk.", ".kwkkkwwwwkkkwk.",
              ".kwkkkwwwwkkkwk.", ".kwkkkwwwwkkkwk.", ".kwwwwwkkwwwwwk.", "..kwwwwkkwwwwk..", "...kkwwwwwwkk...", "....kwkwkwkk....",
              "....kwkwkwkk....", ".....kkkkkk.....", "................", "................"),
    "flame": ("................", ".......kk.......", "......koyk......", "......kooyk.....", ".....kooooyk....", "....kooyyoook...",
              "...kooyyyyook...", "...kooyyyyyook..", "..kooyyyyyyook..", "..kooyyyyyyyook.", "..kooyyyyyyyook.", "..koooyyyyyook..",
              "...koooyyyook...", "....kkooooookk..", "......kkkkk.....", "................"),
    "tree": ("................", ".....kkkkkk.....", "...kkGGGGGGkk...", "..kGGGGDGGGGGk..", ".kGGDGGGGGGDGGk.", ".kGGGGGGDGGGGGk.",
             ".kGDGGGGGGGGDGk.", "..kGGGGGDGGGGk..", "...kkGGGGGGkk...", ".....kkbtkk.....", "......kbtk......", "......kbtk......",
             "......kbtk......", ".....kBBBBk.....", "......kkkk......", "................"),
    "crown": ("................", "................", ".ky...ky...yk...", ".kyk.kyyk.kyk...", ".kyyk.kyk.kyyk..", ".kyyykkykkyyyk..",
              ".kyyyyyyyyyyyk..", ".kyyrYyyyYrYyk..", ".kyyyyyyyyyyyk..", ".kYYYYYYYYYYYk..", "..kkkkkkkkkkkk..", "................",
              "................", "................", "................", "................"),
    "fish": ("................", "................", "................", "....kkkkk....kk.", "..kkcccccckkkck.", ".kcccccwkcccccck",
             "kcckccckcccccCCk", "kcccccckcccCCCk.", ".kCCccccccCCCCk.", "..kkCCCCCCkkkCk.", "....kkkkkk...kk.", "................",
             "................", "................", "................", "................"),
}


def pixel_sprite(rows, tile, scale=6, overlay=()):
    """overlay is a list of (x, y, colour letter) pixels drawn on top, for sparks and sparkles in the animated logos."""
    for row in rows:
        assert len(row) == 16, (len(row), row)
    assert len(rows) == 16
    top, bottom = TILES[tile % len(TILES)]
    mask = Image.new("L", (BIG, BIG), 0)
    ImageDraw.Draw(mask).rounded_rectangle([18, 18, BIG - 18, BIG - 18], radius=BIG * 0.2, fill=255)
    body = gradient(top, bottom).convert("RGBA")
    rim = ImageChops.subtract(mask, mask.filter(ImageFilter.MinFilter(11)))
    body.paste(Image.new("RGBA", (BIG, BIG), (255, 255, 255, 46)), mask=rim)
    tile_image = Image.new("RGBA", (BIG, BIG), (0, 0, 0, 0))
    tile_image.paste(body, mask=mask)
    tile_image = tile_image.resize((SIZE, SIZE), Image.LANCZOS)
    start = (SIZE - 16 * scale) // 2
    pixels = [(x, y, char) for y, row in enumerate(rows) for x, char in enumerate(row) if char != "."] + list(overlay)
    for x, y, char in pixels:
        px, py = start + x * scale, start + y * scale
        ImageDraw.Draw(tile_image).rectangle([px, py, px + scale - 1, py + scale - 1], fill=COLOURS[char] + (255,))
    return tile_image


def save(image, path):
    image.save(path, optimize=True, compress_level=9)  # a palette PNG is smaller but bands visibly on the gradients


# ---------------------------------------------------------------- animated logos (GIF)
def save_gif(frames, path, duration):
    """Frames are RGBA. A GIF has one-bit transparency, so the badge edge is thresholded at half opacity."""
    paletted = []
    for frame in frames:
        flat = frame.convert("RGB").quantize(colors=255, method=Image.MEDIANCUT, dither=Image.NONE)
        flat.paste(255, mask=frame.getchannel("A").point(lambda a: 255 if a < 128 else 0))
        paletted.append(flat)
    paletted[0].save(path, save_all=True, append_images=paletted[1:], duration=duration, loop=0, disposal=2, transparency=255)


def sway(rows, first, last, dx):
    """Moves rows first..last sideways by dx pixels: a flame leaning left or right."""
    rows = list(rows)
    for index in range(first, last + 1):
        row = rows[index]
        for _ in range(abs(dx)):
            row = ("." + row[:-1]) if dx > 0 else (row[1:] + ".")
        rows[index] = row
    return rows


ANIMATED_EMBLEMS = [("squircle", "gold", "crown"), ("hexagon", "diamond", "gem"), ("circle", "redstone", "sword"), ("badge", "amethyst", "shield"),
                    ("squircle", "emerald", "pickaxe"), ("hexagon", "ember", "bolt"), ("circle", "sapphire", "star"), ("squircle", "rose", "heart")]


def animated_logos():
    count = 0
    glyphs = {glyph[0]: glyph for group in GLYPHS.values() for glyph in group}
    for shape, palette, name in ANIMATED_EMBLEMS:
        sweep = [i / 9 for i in range(10)] + [None] * 6  # a shine passes over the badge, then a short pause
        save_gif([emblem(shape, palette, glyphs[name], shine) for shine in sweep], OUT / f"animated-{name}-{palette}.gif", 70)
        count += 1
    tile = {name: index for index, name in enumerate(SPRITES)}
    sprites = SPRITES
    flicker = [(0, ()), (1, [(8, 0, "y")]), (0, ()), (-1, [(6, 0, "o")])]
    for name, first, last in (("torch", 1, 6), ("flame", 1, 12)):
        frames = [pixel_sprite(sway(sprites[name], first, min(last, first + 3), dx), tile[name], overlay=spark if name == "torch" else ()) for dx, spark in flicker]
        save_gif(frames, OUT / f"animated-{name}.gif", 130)
        count += 1
    save_gif([pixel_sprite(sprites["heart"], tile["heart"], scale=scale) for scale in (6, 6, 7, 7, 7, 6, 6, 6)], OUT / "animated-heart.gif", 110)
    twinkle = [[], [(2, 3, "w"), (13, 9, "w")], [(13, 2, "w"), (3, 12, "w")], [(8, 1, "w"), (1, 8, "w")]]
    save_gif([pixel_sprite(sprites["star"], tile["star"], overlay=extra) for extra in twinkle], OUT / "animated-star.gif", 160)
    cross = lambda x, y: [(x, y - 1, "w"), (x - 1, y, "w"), (x, y, "w"), (x + 1, y, "w"), (x, y + 1, "w")]
    sparkle = [[], cross(5, 6), [(5, 6, "w")], [], cross(10, 8), [(10, 8, "w")]]
    save_gif([pixel_sprite(sprites["diamond"], tile["diamond"], overlay=extra) for extra in sparkle], OUT / "animated-diamond.gif", 120)
    return count + 3


def main():
    OUT.mkdir(parents=True, exist_ok=True)
    for old in list(OUT.glob("*.png")) + list(OUT.glob("*.gif")):
        old.unlink()
    count = 0
    palettes = list(PALETTES)
    glyph_index = 0
    for category, glyphs in GLYPHS.items():
        for glyph in glyphs:
            for k in range(7):
                palette = palettes[(glyph_index * 5 + k * 2) % len(palettes)]
                shape = SHAPES[(glyph_index + k) % len(SHAPES)]
                save(emblem(shape, palette, glyph), OUT / f"{category}-{glyph[0]}-{palette}.png")
                count += 1
            glyph_index += 1
    for index, (name, rows) in enumerate(SPRITES.items()):
        save(pixel_sprite(rows, index), OUT / f"pixel-{name}.png")
        count += 1
    count += animated_logos()
    total = sum(p.stat().st_size for p in list(OUT.glob("*.png")) + list(OUT.glob("*.gif")))
    print(f"wrote {count} logos, {total // 1024} KB, to {OUT}")


if __name__ == "__main__":
    sys.exit(main())
