"""Rulebook page images (English art -> Korean text). 42 pages, 320x320 RGBA, white strokes on transparent.

  export                       dump English pages to assets/pages_src/<nl name>.png (nl name = bundle texture name)
  render [name ...]            assets/pages_spec/<nl name>.json -> assets/pages/<nl name>.png (+ out/preview/<name>.png)
spec json: {"erase": [[x,y,w,h] | <patch>, ...],
            "texts": [{"xy": [x,y], "text": "한 줄\n두 줄", "size": 20, "rot": -4, "line": 1.15}],
            "boxes": [{"patch": <patch>|null, "x":..,"y":..,"w":..,"h":..,"rot":..,
                       "text": "..", "size": null, "align": "center", "bold": 0}]}
  erase rects / patches are cleared to transparent (use them to remove the English words, never the drawings);
  xy = top-left of the text block before rotation; rot in degrees, counter-clockwise positive.
  boxes: text laid out (scripts/pagetools/textbox.py) into a w x h flat render, then warped
  through the Coons patch (rect_patch(x,y,w,h,rot) when "patch" is null).
  env: PAGES_SRC_DIR / PAGES_SPEC_DIR / PAGES_OUT_DIR / PAGES_PREV_DIR override the dirs (tests, previews, TOC link textures).
  texts: drawn 3x supersampled; "gamma" (default 0.6) lifts thin antialiased strokes. No bold/dilation (it clogs Hangul in game).
"""
import json, os, re, sys
import UnityPy
from PIL import Image, ImageDraw, ImageFilter, ImageFont

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
GAME = "/mnt/s/Installation/Steam/steamapps/common/King of the Bridge"
BUN = f"{GAME}/King of the Bridge_Data/StreamingAssets/aa/StandaloneWindows64/"
SRC, SPEC, OUT, PREV = (os.path.join(ROOT, p) for p in ("assets/pages_src", "assets/pages_spec", "assets/pages", "out/preview"))
SRC = os.environ.get("PAGES_SRC_DIR", SRC)
PREV = os.environ.get("PAGES_PREV_DIR", PREV)
SPEC = os.environ.get("PAGES_SPEC_DIR", SPEC)
OUT = os.environ.get("PAGES_OUT_DIR", OUT)
FONT = os.path.join(ROOT, "assets/fonts/Griun602.ttf")


def nl_name(en):
    m = re.fullmatch(r"secretpage(\d)_eng", en)
    if m: return f"secretpage{m[1]}_dutch"
    m = re.fullmatch(r"page_contents_(\d)", en)
    if m: return f"page_contents{m[1]}"
    m = re.fullmatch(r"page_tutorial_(\d)", en)
    if m: return f"page_t{m[1]}"
    m = re.fullmatch(r"page(\d+)", en)
    return f"page_{m[1]}"


def export():
    os.makedirs(SRC, exist_ok=True)
    for o in UnityPy.load(BUN + "localization-assets-english(en)_assets_all.bundle").objects:
        if o.type.name == "Texture2D":
            d = o.read()
            d.image.convert("RGBA").save(f"{SRC}/{nl_name(d.m_Name)}.png")


def _pagetools():
    sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
    from pagetools import patch, textbox, warp
    return patch, textbox, warp


def _erase_patch(im, p, patch):
    """Clear the polygon sampled along the patch boundary to transparent."""
    import numpy as np
    pts = []
    ts = np.linspace(0, 1, 25)
    for k, rev in (("top", False), ("right", False), ("bottom", True), ("left", True)):
        e = patch.bez(p[k], ts)
        pts.append(e[::-1] if rev else e)
    mask = Image.new("L", im.size, 0)
    ImageDraw.Draw(mask).polygon([tuple(q) for q in np.concatenate(pts)], fill=255)
    im.paste((0, 0, 0, 0), (0, 0), mask)


def _render_box(im, b, patch, textbox, warp):
    """textbox layout -> flat render -> warp into the box patch, composited on im."""
    w, h = int(b["w"]), int(b["h"])
    box = textbox.layout(b["text"], w, h, font=FONT, size=b.get("size"),
                         align=b.get("align", "center"), line=b.get("line", 1.15),
                         bold=int(b.get("bold", 0)))
    flat = textbox.render(box)
    p = b.get("patch")
    if p is None:
        p = patch.rect_patch(b.get("x", 0), b.get("y", 0), w, h, b.get("rot", 0))
    im.alpha_composite(warp.warp(flat, p, im.size))


def text_layer(t):
    """3x supersampled draw+rotate, box downsample, alpha gamma boost: crisp thin strokes without dilation."""
    SS = 3
    size = t.get("size", 20)
    font = ImageFont.truetype(FONT, int(round(size * SS)))
    lines = t["text"].split("\n")
    step = int(size * SS * t.get("line", 1.15))
    w = max(int(font.getlength(l)) for l in lines) + 8 * SS
    layer = Image.new("RGBA", (w, step * len(lines) + 8 * SS), (255, 255, 255, 0))
    dr = ImageDraw.Draw(layer)
    for i, l in enumerate(lines):
        dr.text((4 * SS, 4 * SS + i * step), l, font=font, fill=(255, 255, 255, 255))
    layer = layer.rotate(t.get("rot", 0), expand=True, resample=Image.BICUBIC)
    layer = layer.resize((max(1, layer.width // SS), max(1, layer.height // SS)), Image.BOX)
    layer.putalpha(layer.getchannel("A").point(lambda v: int(255 * (v / 255) ** t.get("gamma", 0.6) + 0.5)))
    return layer


def render(name):
    spec = json.load(open(f"{SPEC}/{name}.json", encoding="utf-8"))
    im = Image.open(f"{SRC}/{name}.png").convert("RGBA")
    for e in spec.get("erase", []):
        if isinstance(e, dict):
            patch, _, _ = _pagetools()
            _erase_patch(im, patch.load(e), patch)
        else:
            x, y, w, h = e
            im.paste((0, 0, 0, 0), (x, y, x + w, y + h))
    for t in spec.get("texts", []):
        layer = text_layer(t)
        im.alpha_composite(layer, (max(0, t["xy"][0]), max(0, t["xy"][1])))
    if spec.get("boxes"):
        patch, textbox, warp = _pagetools()
        for b in spec["boxes"]:
            _render_box(im, b, patch, textbox, warp)
    im = Image.merge("RGBA", (Image.new("L", im.size, 255),) * 3 + (im.getchannel("A"),))  # ink is white; drop black RGB under dilated/antialiased alpha (dark fringes)
    os.makedirs(OUT, exist_ok=True); os.makedirs(PREV, exist_ok=True)
    im.save(f"{OUT}/{name}.png")
    bg = Image.new("RGBA", im.size, (34, 40, 40, 255)); bg.alpha_composite(im)
    src = Image.new("RGBA", im.size, (34, 40, 40, 255)); src.alpha_composite(Image.open(f"{SRC}/{name}.png").convert("RGBA"))
    pv = Image.new("RGBA", (im.width * 2 + 10, im.height), (0, 0, 0, 255)); pv.paste(src, (0, 0)); pv.paste(bg, (im.width + 10, 0))
    pv.resize((pv.width * 2, pv.height * 2), Image.NEAREST).save(f"{PREV}/{name}.png")


if __name__ == "__main__":
    if sys.argv[1] == "export": export()
    else:
        for n in sys.argv[2:] or sorted(f[:-5] for f in os.listdir(SPEC) if f.endswith(".json")): render(n)
