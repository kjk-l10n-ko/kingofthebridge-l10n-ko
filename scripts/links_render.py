"""Unified renderer for the 68 TOC entry textures (replaces the per-agent specs' text rendering).
Geometry (erase rects, start point and tilt of each text line) comes from assets/links_spec/<name>.json (the boxes'
patches); the text of every line and ALL typography comes from here, so every entry looks the same:
  font KangBujangnim, one size (shrunk only to stay left of the right bracket), 4x supersampled, alpha gamma 0.6, no bold.
  python3 scripts/links_render.py [name ...]  ->  assets/links/<name>.png"""
import json, math, os, sys
from PIL import Image, ImageDraw, ImageFont

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "pagetools"))
import patch as P, warp as W

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SRC, SPEC, OUT = (os.path.join(ROOT, "assets", d) for d in ("links_src", "links_spec", "links"))
FONT = os.path.join(ROOT, "assets/fonts/KangBujangnim.ttf")
SIZE, MIN_SIZE, SS, GAMMA, RIGHT = 13, 11, 4, 0.6, 137


def lines_of(spec):
    """(text, start xy, tilt deg ccw, height) per distinct text line; the agents' duplicate shifted boxes are dropped."""
    out = []
    for b in spec.get("boxes", []):
        p = b["patch"]; t = b.get("text")
        x0, y0 = p["top"][0]; x3, y3 = p["top"][3]
        h = math.hypot(p["left"][3][0] - p["left"][0][0], p["left"][3][1] - p["left"][0][1])
        item = (t, (x0, y0), -math.degrees(math.atan2(y3 - y0, x3 - x0)), h)
        if any(o[0] == t and abs(o[1][0] - x0) < 1.5 and abs(o[1][1] - y0) < 1.5 for o in out):
            continue
        out.append(item)
    return out


# two-line entries all share one geometry (taken from the left/right versions of entry 00): start of line 1 / line 2, tilt
GEOM = {"l": [((44.3, 12.8), 4.5), ((24.3, 29.3), 4.5)], "r": [((45.9, 4.2), -4.5), ((25.9, 18.8), -4.5)]}


def uniform(name, items):
    if len(items) != 2 or name.startswith(("link_e", "link_r8_")):
        return items
    g = GEOM[name[-1]]
    items = sorted(items, key=lambda it: it[1][1])
    return [(t, g[i][0], g[i][1], 20) for i, (t, _, _, _) in enumerate(items)]


def render(name):
    spec = json.load(open(f"{SPEC}/{name}.json", encoding="utf-8"))
    im = Image.open(f"{SRC}/{name}.png").convert("RGBA")
    items = uniform(name, lines_of(spec))
    if items:
        for x, y, w, h in spec.get("erase", []):
            im.paste((0, 0, 0, 0), (x, y, x + w, y + h))
    for text, (x0, y0), rot, h in items:
        size = SIZE
        while True:
            f = ImageFont.truetype(FONT, size * SS)
            w = f.getlength(text) / SS
            if x0 + w * math.cos(math.radians(rot)) <= RIGHT or size <= MIN_SIZE:
                break
            size -= 0.5
        hh = max(h, size * 1.5)
        flat = Image.new("RGBA", (int(w * SS) + 4, int(hh * SS)), (255, 255, 255, 0))
        ImageDraw.Draw(flat).text((2, flat.height / 2), text, font=f, fill=(255, 255, 255, 255), anchor="lm")
        patch = P.rect_patch(x0, y0, flat.width / SS, flat.height / SS, rot)
        layer = W.warp(flat, patch, im.size)
        a = layer.getchannel("A").point(lambda v: int(255 * (v / 255) ** GAMMA + 0.5))
        layer = Image.merge("RGBA", (Image.new("L", im.size, 255),) * 3 + (a,))
        im.alpha_composite(layer)
    im = Image.merge("RGBA", (Image.new("L", im.size, 255),) * 3 + (im.getchannel("A"),))
    os.makedirs(OUT, exist_ok=True)
    im.save(f"{OUT}/{name}.png")


if __name__ == "__main__":
    names = sys.argv[1:] or sorted(f[:-5] for f in os.listdir(SPEC) if f.endswith(".json"))
    for n in names:
        render(n)
    print("rendered", len(names))
