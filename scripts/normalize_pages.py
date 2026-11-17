"""One-shot: make rulebook page text consistent (titles 26px, body <=16px with the same line spacing).
Body is re-wrapped to the width of the erase rect it replaces; the size drops below 16 only if the wrapped block would
not fit that rect's height. Sentence ends ('.', '!', '?', '다.') keep their line break. Skips poem/list pages."""
import json, os, re, sys
from PIL import ImageFont

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SPEC = os.path.join(ROOT, "assets/pages_spec")
FONT = os.path.join(ROOT, "assets/fonts/Gaegu-Bold.ttf")
SKIP = {"page_31", "page_contents0", "page_contents1", "secretpage1_dutch", "secretpage2_dutch", "page_t2", "page_19", "page_25"}
TITLE, BODY, LINE = 26, 16, 1.3


def wrap(text, size, width):
    f = ImageFont.truetype(FONT, size)
    out = []
    for para in re.split(r"(?<=[.!?])\n", text):
        words = para.replace("\n", " ").split(" ")
        cur = ""
        for w in words:
            t = (cur + " " + w).strip()
            if cur and f.getlength(t) > width:
                out.append(cur); cur = w
            else:
                cur = t
        out.append(cur)
    return out


def rect_for(erase, xy):
    c = [r for r in erase if isinstance(r, list) and r[0] - 10 <= xy[0] <= r[0] + r[2] and r[1] - 10 <= xy[1] <= r[1] + r[3]]
    return c


def main(names):
    for n in names:
        if n in SKIP:
            continue
        p = f"{SPEC}/{n}.json"; d = json.load(open(p, encoding="utf-8"))
        texts = d.get("texts", [])
        for i, t in enumerate(texts):
            if i == 0 and t["size"] >= 22:
                t["size"] = TITLE; t.pop("line", None); continue
            rs = rect_for(d.get("erase", []), t["xy"])
            if not rs:
                continue
            w = max(r[2] for r in rs) * 0.97
            h = sum(r[3] for r in rs if r[0] <= t["xy"][0] + 4) or rs[0][3]
            for s in (16, 15, 14, 13, 12):
                lines = wrap(t["text"], s, w)
                if len(lines) * s * LINE <= h * 1.4 + 6:
                    break
            t["size"], t["line"], t["text"] = s, LINE, "\n".join(lines)
        json.dump(d, open(p, "w", encoding="utf-8"), ensure_ascii=False, indent=1)


if __name__ == "__main__":
    main(sys.argv[1:] or sorted(f[:-5] for f in os.listdir(SPEC) if f.endswith(".json")))
