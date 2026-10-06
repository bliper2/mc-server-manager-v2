"""Builds the bundled server banners into static/banners: still PNGs and looping animated GIFs, 640 x 192.

Run again only to change the set: python tools/make_banners.py   (needs Pillow; the manager itself does not).
File names are <category>-<name>.<ext> (scenic, pattern, animated). Everything is drawn from code, so the banners are
original artwork with no licence attached."""
import math
import random
import sys
from pathlib import Path

from PIL import Image, ImageChops, ImageDraw, ImageFilter

OUT = Path(__file__).resolve().parent.parent / "static" / "banners"
W, H = 640, 192


def mix(a, b, t):
    return tuple(round(a[i] + (b[i] - a[i]) * t) for i in range(3))


def gradient(top, bottom, size=(W, H)):
    column = Image.new("RGB", (1, size[1]))
    column.putdata([mix(top, bottom, y / max(size[1] - 1, 1)) for y in range(size[1])])
    return column.resize(size)


def glow(image, center, radius, color, strength=0.55):
    """A soft round light: used for suns and moons."""
    layer = Image.new("L", image.size, 0)
    ImageDraw.Draw(layer).ellipse([center[0] - radius, center[1] - radius, center[0] + radius, center[1] + radius], fill=255)
    layer = layer.filter(ImageFilter.GaussianBlur(radius * 0.7)).point(lambda v: int(v * strength))
    image.paste(Image.new("RGB", image.size, color), mask=layer)


def ridge(draw, base, amplitude, wavelength, phase, color, step=4):
    points = [(x, base + amplitude * math.sin(x / wavelength + phase) + amplitude * 0.5 * math.sin(x / (wavelength * 0.43) + phase * 1.7)) for x in range(0, W + step, step)]
    draw.polygon(points + [(W, H), (0, H)], fill=color)


# ---------------------------------------------------------------- stills
def sunset_hills():
    image = gradient((255, 150, 90), (96, 46, 150))
    glow(image, (470, 112), 60, (255, 220, 140), 0.7)
    draw = ImageDraw.Draw(image)
    draw.ellipse([436, 78, 504, 146], fill=(255, 236, 170))
    for index, (base, color) in enumerate(((118, (150, 60, 140)), (140, (104, 44, 120)), (162, (62, 28, 92)))):
        ridge(draw, base, 12 + index * 3, 52 - index * 8, index * 1.7, color)
    return image


def night_sky():
    image = gradient((6, 8, 28), (34, 26, 78))
    rng = random.Random(4)
    draw = ImageDraw.Draw(image)
    for _ in range(140):
        x, y, bright = rng.randrange(W), rng.randrange(int(H * 0.85)), rng.randrange(140, 256)
        size = 1 if rng.random() < 0.8 else 2
        draw.rectangle([x, y, x + size - 1, y + size - 1], fill=(bright, bright, min(255, bright + 20)))
    glow(image, (520, 62), 46, (200, 210, 255), 0.35)
    draw.ellipse([494, 36, 548, 90], fill=(240, 240, 225))
    draw.ellipse([510, 30, 566, 86], fill=image.getpixel((540, 40)))
    ridge(draw, 164, 8, 60, 0.4, (14, 14, 40))
    return image


def ocean():
    image = gradient((40, 150, 190), (6, 36, 90))
    glow(image, (130, 60), 50, (220, 250, 255), 0.45)
    draw = ImageDraw.Draw(image)
    for index in range(5):
        ridge(draw, 70 + index * 24, 5 + index, 38 + index * 6, index * 1.3, mix((70, 170, 210), (8, 50, 110), index / 4))
    return image


def forest():
    image = gradient((176, 224, 205), (244, 250, 232))
    draw = ImageDraw.Draw(image)
    rng = random.Random(9)
    for layer, (color, base, size) in enumerate(((  (96, 160, 130), 150, 44), ((52, 120, 92), 168, 56), ((22, 78, 60), 190, 70))):
        x = -20
        while x < W + 40:
            height = size + rng.randrange(-8, 14)
            width = height * 0.55
            for tier in range(3):
                top = base - height + tier * height * 0.22
                bottom = base - height * 0.35 + tier * height * 0.22
                draw.polygon([(x, top), (x + width * (0.45 + tier * 0.12), bottom), (x - width * (0.45 + tier * 0.12), bottom)], fill=color)
            x += width * (0.75 + rng.random() * 0.4)
    return image


def desert():
    image = gradient((252, 206, 128), (252, 238, 206))
    glow(image, (140, 90), 56, (255, 240, 200), 0.6)
    draw = ImageDraw.Draw(image)
    draw.ellipse([112, 62, 168, 118], fill=(255, 244, 214))
    for index, color in enumerate(((236, 180, 104), (214, 150, 80), (186, 120, 62))):
        ridge(draw, 120 + index * 24, 14 - index * 2, 70 - index * 10, index * 2.1, color)
    return image


def mountains():
    image = gradient((116, 168, 232), (222, 238, 252))
    rng = random.Random(21)
    draw = ImageDraw.Draw(image)
    for color, snow, base, spread in (((120, 140, 178), None, 150, 38), ((78, 98, 140), (250, 252, 255), 168, 54)):
        x = -30
        while x < W + 60:
            width, height = rng.randrange(110, 190), rng.randrange(40, 40 + spread * 2)
            peak = (x + width / 2, base - height)
            draw.polygon([(x, base + 40), peak, (x + width, base + 40)], fill=color)
            if snow:
                cap = height * 0.32
                draw.polygon([peak, (peak[0] - width * 0.16, peak[1] + cap), (peak[0] - width * 0.05, peak[1] + cap * 0.7), (peak[0] + width * 0.05, peak[1] + cap * 1.05),
                              (peak[0] + width * 0.16, peak[1] + cap)], fill=snow)
            x += width * 0.62
    draw.rectangle([0, 176, W, H], fill=(48, 70, 112))
    return image


def pixel_grid():
    image = Image.new("RGB", (W, H))
    rng = random.Random(3)
    palette = [(18, 70, 56), (26, 98, 76), (34, 126, 96), (52, 160, 118), (96, 205, 150), (24, 52, 60)]
    draw = ImageDraw.Draw(image)
    for x in range(0, W, 16):
        for y in range(0, H, 16):
            draw.rectangle([x, y, x + 15, y + 15], fill=rng.choice(palette))
    return image


def circuit():
    image = gradient((6, 22, 28), (4, 14, 20))
    rng = random.Random(8)
    draw = ImageDraw.Draw(image)
    for _ in range(26):
        x, y = rng.randrange(0, W, 16), rng.randrange(0, H, 16)
        points = [(x, y)]
        for _ in range(rng.randrange(3, 7)):
            if rng.random() < 0.5:
                x += rng.choice((-1, 1)) * rng.randrange(2, 8) * 16
            else:
                y += rng.choice((-1, 1)) * rng.randrange(1, 4) * 16
            points.append((x, y))
        color = rng.choice(((0, 210, 200), (0, 150, 190), (60, 230, 160)))
        draw.line(points, fill=color, width=2)
        draw.ellipse([points[0][0] - 4, points[0][1] - 4, points[0][0] + 4, points[0][1] + 4], outline=color, width=2)
        draw.ellipse([points[-1][0] - 3, points[-1][1] - 3, points[-1][0] + 3, points[-1][1] + 3], fill=color)
    return image


def hexes():
    image = Image.new("RGB", (W, H), (20, 10, 40))
    draw = ImageDraw.Draw(image)
    radius = 24
    row_height = radius * 1.5
    for row in range(-1, int(H / row_height) + 2):
        for col in range(-1, int(W / (radius * math.sqrt(3))) + 2):
            cx = col * radius * math.sqrt(3) + (radius * math.sqrt(3) / 2 if row % 2 else 0)
            cy = row * row_height
            t = min(max((cx / W + (cy / H) * 0.4) / 1.4, 0), 1)
            color = mix((120, 60, 220), (240, 90, 170), t)
            points = [(cx + (radius - 2) * math.cos(math.radians(60 * i + 30)), cy + (radius - 2) * math.sin(math.radians(60 * i + 30))) for i in range(6)]
            draw.polygon(points, fill=mix(color, (20, 10, 40), 0.35 + 0.25 * ((row + col) % 3) / 2), outline=color)
    return image


def stripes():
    image = gradient((18, 90, 140), (12, 40, 90))
    draw = ImageDraw.Draw(image)
    for x in range(-H, W, 48):
        draw.polygon([(x, H), (x + 24, H), (x + 24 + H, 0), (x + H, 0)], fill=(30, 130, 170))
    return image


STILLS = {"scenic-sunset-hills": sunset_hills, "scenic-night-sky": night_sky, "scenic-ocean": ocean, "scenic-forest": forest, "scenic-desert": desert,
          "scenic-mountains": mountains, "pattern-pixel-grid": pixel_grid, "pattern-circuit": circuit, "pattern-hexagons": hexes, "pattern-stripes": stripes}


# ---------------------------------------------------------------- animated (every one loops without a visible seam)
def aurora(t, frames):
    image = gradient((4, 8, 24), (14, 30, 52))
    rng = random.Random(5)
    draw = ImageDraw.Draw(image)
    for _ in range(90):
        x, y = rng.randrange(W), rng.randrange(int(H * 0.8))
        draw.point((x, y), fill=(200, 210, 240))
    phase = 2 * math.pi * t / frames
    for index, (color, base, thickness) in enumerate(((  (60, 255, 150), 78, 46), ((140, 90, 255), 96, 38), ((60, 210, 255), 62, 30))):
        mask = Image.new("L", (W, H), 0)
        top = [(x, base + 22 * math.sin(x / 70 + phase * (1 + index % 2) + index) + 10 * math.sin(x / 31 - phase * 2 + index * 2)) for x in range(0, W + 8, 8)]
        bottom = [(x, y + thickness + 10 * math.sin(x / 55 + phase + index)) for x, y in reversed(top)]
        ImageDraw.Draw(mask).polygon(top + bottom, fill=210)
        mask = mask.filter(ImageFilter.GaussianBlur(11))
        image.paste(Image.new("RGB", (W, H), color), mask=mask.point(lambda v: int(v * 0.8)))
    return image


def starfield(t, frames):
    image = gradient((2, 3, 14), (12, 10, 40))
    rng = random.Random(12)
    draw = ImageDraw.Draw(image)
    stars = [(rng.randrange(W), rng.randrange(H), rng.choice((1, 1, 2)), rng.choice((20, 40, 60)), rng.randrange(150, 256)) for _ in range(150)]
    for x, y, size, speed, bright in stars:
        px = (x - speed * t) % W  # speed * frames is a multiple of W, so the last frame leads straight into the first
        draw.rectangle([px, y, px + size - 1, y + size - 1], fill=(bright, bright, 255))
        if speed == 60:
            draw.line([px, y, px + 8, y], fill=(bright // 2, bright // 2, 150))
    return image


def waves(t, frames):
    image = gradient((70, 190, 220), (8, 60, 120))
    draw = ImageDraw.Draw(image)
    phase = 2 * math.pi * t / frames
    for index in range(5):
        base = 62 + index * 26
        points = [(x, base + (5 + index * 1.5) * math.sin(x / (34 + index * 7) + phase * (1 + index % 2) * (1 if index % 2 else -1) + index)) for x in range(0, W + 6, 6)]
        draw.polygon(points + [(W, H), (0, H)], fill=mix((120, 210, 235), (8, 56, 118), (index + 1) / 5))
    return image


def sunset_clouds(t, frames):
    image = gradient((255, 140, 96), (110, 56, 156))
    glow(image, (470, 118), 64, (255, 215, 150), 0.65)
    draw = ImageDraw.Draw(image)
    draw.ellipse([436, 84, 504, 152], fill=(255, 238, 180))
    rng = random.Random(15)
    period = 800
    for _ in range(9):
        x0, y, scale = rng.randrange(period), rng.randrange(20, 120), rng.uniform(0.7, 1.5)
        x = (x0 - (period // frames) * t) % period - 80
        shade = mix((255, 200, 170), (200, 120, 170), (y - 20) / 100)
        for dx, dy, r in ((0, 0, 20), (22, -8, 26), (50, 0, 22), (24, 6, 24)):
            draw.ellipse([x + dx * scale - r * scale, y + dy * scale - r * 0.55 * scale, x + dx * scale + r * scale, y + dy * scale + r * 0.55 * scale], fill=shade)
    ridge(draw, 168, 6, 40, 1.2, (60, 28, 90))
    return image


def neon_grid(t, frames):
    image = gradient((28, 0, 56), (150, 0, 130))
    horizon = 112
    sun = Image.new("RGB", (W, H))
    for y in range(36, horizon):
        ImageDraw.Draw(sun).line([(0, y), (W, y)], fill=mix((255, 214, 70), (255, 50, 150), (y - 36) / (horizon - 36)))
    mask = Image.new("L", (W, H), 0)
    mask_draw = ImageDraw.Draw(mask)
    mask_draw.ellipse([262, 36, 378, 152], fill=255)
    for band in range(5):  # dark slits that thicken towards the horizon
        top = 74 + band * 8
        mask_draw.rectangle([0, top, W, top + 1 + band], fill=0)
    mask_draw.rectangle([0, horizon, W, H], fill=0)
    image.paste(sun, mask=mask)
    draw = ImageDraw.Draw(image)
    draw.rectangle([0, horizon, W, H], fill=(18, 0, 38))
    for k in range(-12, 13):
        draw.line([(320 + k * 14, horizon), (320 + k * 140, H)], fill=(255, 60, 200), width=1)
    for k in range(9):
        s = (k + t / frames) / 9
        y = horizon + (H - horizon) * s ** 2.2
        draw.line([(0, y), (W, y)], fill=(255, 60, 200), width=1)
    draw.line([(0, horizon), (W, horizon)], fill=(255, 140, 230), width=2)
    return image


def embers(t, frames):
    image = gradient((26, 6, 6), (84, 18, 6))
    layer = Image.new("RGB", (W, H), (0, 0, 0))
    draw = ImageDraw.Draw(layer)
    rng = random.Random(30)
    for _ in range(70):
        x0, y0, size = rng.randrange(W), rng.randrange(H), rng.choice((1, 2, 2, 3))
        speed = rng.choice((12, 24))  # a multiple of H / frames keeps the loop seamless
        y = (y0 - speed * t) % H
        x = x0 + 6 * math.sin(2 * math.pi * (t / frames) * (1 if speed == 12 else 2) + x0)
        color = rng.choice(((255, 160, 40), (255, 100, 20), (255, 220, 120)))
        draw.ellipse([x - size, y - size, x + size, y + size], fill=color)
    halo = layer.filter(ImageFilter.GaussianBlur(5))
    image = ImageChops.add(image, halo)
    return ImageChops.add(image, layer)


def rain(t, frames):
    image = gradient((18, 24, 40), (34, 44, 70))
    draw = ImageDraw.Draw(image)
    rng = random.Random(40)
    for _ in range(42):  # a skyline
        pass
    x = 0
    while x < W:
        width, height = rng.randrange(24, 60), rng.randrange(40, 100)
        draw.rectangle([x, H - height, x + width, H], fill=(10, 14, 28))
        for wx in range(x + 5, x + width - 5, 9):
            for wy in range(H - height + 6, H - 6, 11):
                if rng.random() < 0.3:
                    draw.rectangle([wx, wy, wx + 3, wy + 4], fill=(250, 210, 120))
        x += width + 2
    for _ in range(70):
        x0, y0 = rng.randrange(W), rng.randrange(H)
        speed = rng.choice((24, 48))
        y = (y0 + speed * t) % H
        draw.line([x0 - (speed - 10) * 0.15, y - 12, x0, y], fill=(150, 175, 215))
    return image


def lava(t, frames):
    image = gradient((40, 4, 4), (120, 24, 6))
    layer = Image.new("L", (W, H), 0)
    draw = ImageDraw.Draw(layer)
    rng = random.Random(50)
    phase = 2 * math.pi * t / frames
    for index in range(9):
        cx, cy, radius = rng.randrange(W), rng.randrange(40, H - 20), rng.randrange(22, 44)
        draw.ellipse([cx + 24 * math.sin(phase + index) - radius, cy + 30 * math.cos(phase * (1 + index % 2) + index) - radius,
                      cx + 24 * math.sin(phase + index) + radius, cy + 30 * math.cos(phase * (1 + index % 2) + index) + radius], fill=255)
    layer = layer.filter(ImageFilter.GaussianBlur(14)).point(lambda v: 255 if v > 120 else 0).filter(ImageFilter.GaussianBlur(2))
    image.paste(Image.new("RGB", (W, H), (255, 120, 20)), mask=layer)
    core = layer.filter(ImageFilter.MinFilter(9)).filter(ImageFilter.GaussianBlur(3))
    image.paste(Image.new("RGB", (W, H), (255, 220, 100)), mask=core.point(lambda v: int(v * 0.7)))
    return image


ANIMATED = {"animated-aurora": (aurora, 24, 90), "animated-starfield": (starfield, 32, 90), "animated-waves": (waves, 16, 90), "animated-sunset-clouds": (sunset_clouds, 16, 100),
            "animated-neon-grid": (neon_grid, 16, 90), "animated-embers": (embers, 16, 90), "animated-rain": (rain, 12, 70), "animated-lava": (lava, 20, 100)}


def save_gif(frames, path, duration):
    """One palette for all frames, no dithering: a GIF of smooth gradients stays small and does not shimmer."""
    palette = frames[0].quantize(colors=128, method=Image.MEDIANCUT, dither=Image.NONE)
    converted = [frame.quantize(palette=palette, dither=Image.NONE) for frame in frames]
    converted[0].save(path, save_all=True, append_images=converted[1:], duration=duration, loop=0, optimize=True)


def main():
    OUT.mkdir(parents=True, exist_ok=True)
    for old in list(OUT.glob("*.png")) + list(OUT.glob("*.gif")):
        old.unlink()
    for name, build in STILLS.items():
        build().save(OUT / f"{name}.png", optimize=True)
    for name, (build, frames, duration) in ANIMATED.items():
        save_gif([build(t, frames) for t in range(frames)], OUT / f"{name}.gif", duration)
    files = list(OUT.glob("*"))
    print(f"wrote {len(files)} banners, {sum(p.stat().st_size for p in files) // 1024} KB, to {OUT}")
    for p in sorted(files):
        print(f"  {p.name:34} {p.stat().st_size // 1024:5} KB")


if __name__ == "__main__":
    sys.exit(main())
