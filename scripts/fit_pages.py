"""Keep every page text block inside the visible page area (the texture is a two-page spread: gutter at x~160,
the book clips the bottom). Shrinks (0.5px steps, body >= 12px) and re-wraps to the available width until the
block's real ink box (after rotation) fits its half-page safe zone. Body sizes are unified to the largest size that
fits; titles stay 26px. Run after editing assets/pages_spec, then `pages.py render`."""
import json, os, re, sys
from PIL import ImageFont
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import pages

SPEC = pages.SPEC
LEFT, RIGHT, TOP, BOTTOM = (15, 152), (168, 304), 26, 288
BODY, MIN = 16, 12
KEEP = {"secretpage1_dutch"}
SKIP = {"secretpage2_dutch"}  # crossed-out name list: strike lines overlap the names on purpose
WANT = {"page_t2": 14}  # two step captions squeezed between the drawings  # poem / crossed-out name list: hand-laid line by line, only kept in bounds


def wrap(text, size, width):
    """Re-wrap to width; keeps breaks after sentence ends (. ! ?) and before an opening quote/parenthesis."""
    f = ImageFont.truetype(pages.FONT, int(round(size * 3)))
    out = []
    for para in re.split(r"(?<=[.!?])\n|\n(?=[\"(])", text):
        cur = ""
        for w in para.replace("\n", " ").split(" "):
            t = (cur + " " + w).strip()
            if cur and f.getlength(t) / 3 > width:
                out.append(cur); cur = w
            else:
                cur = t
        out.append(cur)
    if len(out) > 1 and len(out[-1].replace(" ", "")) <= 3 and " " in out[-2]:  # no 1-2 syllable orphan line
        head, last = out[-2].rsplit(" ", 1)
        out[-2:] = [head, last + " " + out[-1]]
    return "\n".join(out)


def ink_box(t):
    lay = pages.text_layer(t)
    b = lay.getchannel("A").point(lambda v: 255 if v > 40 else 0).getbbox()
    x, y = max(0, t["xy"][0]), max(0, t["xy"][1])
    return (x + b[0], y + b[1], x + b[2], y + b[3]) if b else None


def ink_mask(t, size=(320, 320)):
    from PIL import Image
    m = Image.new("L", size, 0)
    lay = pages.text_layer(t).getchannel("A").point(lambda v: 255 if v > 40 else 0)
    m.paste(lay, (max(0, t["xy"][0]), max(0, t["xy"][1])), lay)
    return m


def fits(t, zone, obstacles=None):
    b = ink_box(t)
    if b is not None and not (b[0] >= zone[0] - 1 and b[2] <= zone[1] and b[1] >= TOP - 2 and b[3] <= BOTTOM):
        return False
    if obstacles is not None:
        from PIL import ImageChops
        hit = ImageChops.multiply(ink_mask(t), obstacles).getbbox()
        return hit is None
    return True


def fit(t, zone, size, obstacles=None):
    raw = t["text"]
    for s in [x / 2 for x in range(int(size * 2), MIN * 2 - 1, -1)]:
        for width in range(int(zone[1] - t["xy"][0]), 40, -4):
            c = dict(t, size=s, text=wrap(raw, s, width * 0.95))
            if fits(c, zone, obstacles):
                return c
    return dict(t, size=MIN, text=wrap(raw, MIN, (zone[1] - t["xy"][0]) * 0.9))


def obstacles_for(n, d, upto):
    """Drawings left after erasing the English (dilated 2px) + the ink of the text blocks placed before."""
    from PIL import Image, ImageFilter
    im = Image.open(f"{pages.SRC}/{n}.png").convert("RGBA")
    for e in d.get("erase", []):
        if isinstance(e, list):
            x, y, w, h = e
            im.paste((0, 0, 0, 0), (x, y, x + w, y + h))
    m = im.getchannel("A").point(lambda v: 255 if v > 40 else 0)
    for t in d["texts"][:upto]:
        m = Image.composite(Image.new("L", m.size, 255), m, ink_mask(t))
    return m.filter(ImageFilter.MaxFilter(5))


def is_title(i, t):
    return i == 0 and (t.get("size", 0) >= 22 or re.match(r"^(규칙|사실|에필로그|고발|목차)", t["text"]) is not None) and "\n" not in t["text"]


def orphan(text):
    l = text.split("\n")
    return len(l) > 1 and len(l[-1].replace(" ", "")) <= 3


def main(names):
    for n in names:
        if n in SKIP:
            continue
        p = f"{SPEC}/{n}.json"; d = json.load(open(p, encoding="utf-8"))
        texts = d.get("texts", [])
        for i, t in enumerate(texts):
            zone = LEFT if t["xy"][0] < 160 else RIGHT
            title = is_title(i, t)
            want = WANT.get(n) or (t["size"] if n in KEEP else 26 if title else BODY)
            obs = None if title else obstacles_for(n, d, i)  # a title replaces the English title in place
            if fits(t, zone, obs) and t["size"] == want and not orphan(t["text"]):
                continue
            texts[i] = fit(t, zone, want, obs)
            print(n, i, t["size"], "->", texts[i]["size"])
        json.dump(d, open(p, "w", encoding="utf-8"), ensure_ascii=False, indent=1)


if __name__ == "__main__":
    main(sys.argv[1:] or sorted(f[:-5] for f in os.listdir(SPEC) if f.endswith(".json")))
