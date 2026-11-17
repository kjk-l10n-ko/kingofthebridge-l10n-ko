"""TextBox: fit/wrap Korean (or mixed) text into a w x h box, render it flat, and
edit it WYSIWYG in a browser served from here.

  textbox.py fit --w 130 --h 90 --text '...' --font assets/fonts/Gaegu-Bold.ttf \
                 --out out/box.png --json out/box.json
  textbox.py serve [--port 8765]        # http://127.0.0.1:8765/ -> editor.html
  textbox.py --selftest

Layout box json (= "text layout box" contract), all coords in px:
  {"w":int,"h":int,"font":path,"size":float,"lines":[str],"align":"left|center|right",
   "line":1.15,"png":path,          # extras, all optional on input:
   "bold":int,"spacing":float,"padding":int,"valign":"top|center"}
`render(box)` is the importable entry used by the warping tool.
"""
import argparse, json, os, sys, tempfile, threading, urllib.parse, urllib.request
from http.server import ThreadingHTTPServer, BaseHTTPRequestHandler

from PIL import Image, ImageDraw, ImageFilter, ImageFont

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
HERE = os.path.dirname(os.path.abspath(__file__))
FONT = os.path.join(ROOT, "assets/fonts/Gaegu-Bold.ttf")
FIXTURES = os.path.join(HERE, "wrap_fixtures.json")
PAGE_SRC = os.path.join(ROOT, "assets/pages_src")
BOXDIR = os.path.join(ROOT, "assets/pages_box")

PUNCT = ".,!?:;)]}、。…·%"
WORDISH = set("abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789'’-")


def _w(font, s, spacing=0.0):
    return font.getlength(s) + (spacing * (len(s) - 1) if s else 0.0)


def wrap(font, text, maxw, spacing=0.0):
    """Greedy wrap by measured width. Breaks at spaces; if that is impossible the
    last fitting character, backing off so a Latin word/number is not split and a
    line never starts with punctuation. Explicit '\\n' is honoured.
    Mirrored exactly in editor.html (wrap_fixtures.json keeps the two in sync)."""
    lines = []
    for para in text.split("\n"):
        s = para.strip()
        if not s:
            lines.append("")
            continue
        while s:
            if _w(font, s, spacing) <= maxw:
                lines.append(s)
                break
            k = 0
            while k < len(s) and _w(font, s[: k + 1], spacing) <= maxw:
                k += 1
            cut = k or 1
            if cut < len(s):
                j = s.rfind(" ", cut // 2, cut + 1)
                if j > 0:
                    cut = j
                elif s[cut - 1] in WORDISH and s[cut] in WORDISH:
                    b = cut
                    while b > 0 and s[b - 1] in WORDISH:
                        b -= 1
                    e = cut
                    while e < len(s) and s[e] in WORDISH:
                        e += 1
                    cut = b if b > 0 else e
                while 1 < cut < len(s) and s[cut] in PUNCT:
                    cut -= 1
            lines.append(s[:cut].strip())
            s = s[cut:].strip()
        # trailing empty para from a final "\n" is kept: [""] only if text empty
    return lines


def _block(font, n, size, line):
    """(line_height, block_height, ascent) for n lines."""
    asc, desc = font.getmetrics()
    lh = size * line
    return lh, (lh * (n - 1) if n else 0) + (asc + desc), asc


def _try(font, text, size, iw, ih, line, spacing):
    f = ImageFont.truetype(font, size)
    lines = wrap(f, text, iw, spacing)
    return lines, _block(f, len(lines), size, line)[1] <= ih and all(
        _w(f, l, spacing) <= iw for l in lines)


def layout(text, w, h, font=FONT, size=None, min_size=6, max_size=200, align="center",
           line=1.15, spacing=0.0, padding=0, valign="center", bold=0):
    """Fit (size=None: largest 0.5pt step that fits) or force `size`.
    Returns the layout box dict plus fits/overflow info."""
    iw, ih = w - 2 * padding, h - 2 * padding
    if size is None:
        # binary search on the 0.5pt grid; ~10 font loads instead of a linear scan
        size, lo, hi = float(min_size), float(min_size), float(max_size)
        while lo <= hi:
            mid = round((lo + hi) / 2 * 2) / 2
            if _try(font, text, mid, iw, ih, line, spacing)[1]:
                size, lo = mid, mid + 0.5
            else:
                hi = mid - 0.5
    size = float(size)
    lines, ok = _try(font, text, size, iw, ih, line, spacing)
    return {"w": int(w), "h": int(h), "font": font, "size": size, "lines": lines,
            "align": align, "line": float(line), "bold": int(bold),
            "spacing": float(spacing), "padding": int(padding), "valign": valign,
            "fits": bool(ok)}


def render(box):
    """Layout box dict -> flat RGBA image (w x h, white text on transparent).
    Also accepts an authoring box from the editor ({"text", "size": None, ...}) and refits."""
    if not box.get("lines") or box.get("size") is None:
        box = layout(box.get("text", ""), box["w"], box["h"], box.get("font") or FONT,
                     box.get("size"), align=box.get("align", "center"),
                     line=box.get("line", 1.15), spacing=box.get("spacing", 0.0),
                     padding=box.get("padding", 0), valign=box.get("valign", "center"),
                     bold=box.get("bold", 0))
    w, h = int(box["w"]), int(box["h"])
    size = float(box["size"])
    f = ImageFont.truetype(box["font"], size)
    lines = box["lines"]
    align, valign = box.get("align", "center"), box.get("valign", "center")
    spacing, pad = float(box.get("spacing", 0.0)), int(box.get("padding", 0))
    bold = int(box.get("bold", 0))
    lh, bh, asc = _block(f, len(lines), size, float(box.get("line", 1.15)))
    y0 = pad if valign == "top" else pad + (h - 2 * pad - bh) / 2
    im = Image.new("RGBA", (w, h), (0, 0, 0, 0))
    dr = ImageDraw.Draw(im)
    for i, l in enumerate(lines):
        if not l:
            continue
        lw = _w(f, l, spacing)
        x = pad if align == "left" else (w - pad - lw if align == "right" else (w - lw) / 2)
        y = y0 + asc + i * lh
        if spacing:
            for ch in l:
                dr.text((x, y), ch, font=f, fill=(255, 255, 255, 255), anchor="ls")
                x += f.getlength(ch) + spacing
        else:
            dr.text((x, y), l, font=f, fill=(255, 255, 255, 255), anchor="ls")
    if bold:
        im.putalpha(im.getchannel("A").filter(ImageFilter.MaxFilter(2 * bold + 1)))
    return im


# ---------------------------------------------------------------- server

SOURCES = ["out", "out/shapes", "assets/pages_spec", "assets/pages_shape"]


def _json_files():
    out = []
    for d in SOURCES:
        d = os.path.join(ROOT, d)
        if os.path.isdir(d):
            for n in sorted(os.listdir(d)):
                if n.endswith(".json"):
                    out.append(os.path.relpath(os.path.join(d, n), ROOT).replace(os.sep, "/"))
    return out


class Handler(BaseHTTPRequestHandler):
    BOXDIR = BOXDIR

    def log_message(self, *a):
        pass

    def _send(self, code, body, ctype="application/json; charset=utf-8"):
        if isinstance(body, str):
            body = body.encode()
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _js(self, obj):
        self._send(200, json.dumps(obj, ensure_ascii=False))

    def _file(self, path, ctype):
        path = os.path.realpath(path)
        if not (path == ROOT or path.startswith(ROOT + os.sep)) or not os.path.isfile(path):
            return self._send(404, "not found", "text/plain")
        with open(path, "rb") as fh:
            self._send(200, fh.read(), ctype)

    def do_GET(self):
        p = urllib.parse.unquote(self.path.split("?")[0])
        ctype = {".html": "text/html; charset=utf-8", ".json": "application/json; charset=utf-8",
                 ".png": "image/png", ".ttf": "font/ttf"}.get(os.path.splitext(p)[1])
        if p in ("/", "/editor.html"):
            return self._file(os.path.join(HERE, "editor.html"), "text/html; charset=utf-8")
        if p == "/wrap_fixtures.json":
            return self._file(FIXTURES, "application/json; charset=utf-8")
        if p == "/fonts":
            return self._js(sorted(n for n in os.listdir(os.path.join(ROOT, "assets/fonts"))
                                   if n.endswith((".ttf", ".otf"))))
        if p.startswith("/fonts/"):
            return self._file(os.path.join(ROOT, "assets/fonts", os.path.basename(p)), "font/ttf")
        if p == "/pages":
            return self._js(sorted(n for n in os.listdir(PAGE_SRC) if n.endswith(".png")))
        if p.startswith("/pages/"):
            return self._file(os.path.join(PAGE_SRC, os.path.basename(p)), "image/png")
        if p == "/files":
            return self._js(_json_files())
        if p.startswith("/file/"):
            rel = p[len("/file/"):]
            return self._file(os.path.join(ROOT, rel), ctype or "application/json")
        if p == "/boxes":
            bd = self.BOXDIR
            return self._js(sorted(n for n in os.listdir(bd) if n.endswith(".json"))
                            if os.path.isdir(bd) else [])
        if p.startswith("/boxes/"):
            return self._file(os.path.join(self.BOXDIR, os.path.basename(p)), "application/json")
        self._send(404, "not found", "text/plain")

    def do_POST(self):
        p = urllib.parse.urlsplit(self.path).path
        if p != "/save":
            return self._send(404, "not found", "text/plain")
        try:
            n = int(self.headers.get("Content-Length") or 0)
            data = json.loads(self.rfile.read(n) or b"{}")
            name = "".join(c for c in str(data["name"]) if c.isalnum() or c in "_-.") or "untitled"
            os.makedirs(self.BOXDIR, exist_ok=True)
            path = os.path.join(self.BOXDIR, name + ".json")
            with open(path, "w", encoding="utf-8") as fh:
                json.dump({"name": name, "boxes": data.get("boxes", [])}, fh,
                          ensure_ascii=False, indent=1)
        except (ValueError, KeyError, OSError) as e:
            return self._send(400, json.dumps({"error": str(e)}))
        self._js({"saved": os.path.relpath(path, ROOT).replace(os.sep, "/"), "n": len(data.get("boxes", []))})


def make_server(port=8765, boxdir=None):
    cls = type("H", (Handler,), {"BOXDIR": boxdir or BOXDIR})
    os.makedirs(cls.BOXDIR, exist_ok=True)
    return ThreadingHTTPServer(("127.0.0.1", port), cls)


# ---------------------------------------------------------------- selftest

SAMPLES = [
    ("다리 위에서 서로 마주 보세요. 이 게임은 신뢰에 관한 이야기입니다.", 120, 20),
    ("한 번에 한 칸씩만 움직일 수 있습니다.", 100, 18),
    ("카드를 버리면 다시 뽑을 수 없습니다. 신중하게 고르세요!", 130, 20),
    ("상대방의 패를 볼 수 없습니다", 90, 18),
    ("Bridge of Trust: 3번째 라운드", 110, 18),
    ("규칙을 모두 읽은 뒤 시작하세요.", 150, 22),
    ("가위바위보에서 이긴 사람이 먼저 시작합니다", 100, 20),
    ("점수는 10점 단위로 계산됩니다.", 110, 18),
    ("이 카드는 버릴 수 없습니다", 70, 16),
    ("\n\n여러 줄짜리\n텍스트도 됩니다", 120, 20),
]


def selftest():
    font = ImageFont.truetype(FONT, 20)
    t = "다리 위에서 서로 마주 보세요. 이 게임은 신뢰에 관한 이야기입니다."
    assert wrap(font, t, 120) == wrap(font, t, 120)
    assert all(_w(font, l) <= 120 for l in wrap(font, t, 120)), wrap(font, t, 120)
    assert wrap(font, "a\n\nb", 500) == ["a", "", "b"]
    assert wrap(font, "Bridge of Trust", 60) == ["Bridge", "of", "Trust"], wrap(font, "Bridge of Trust", 60)
    assert not any(l[:1] in PUNCT for l in wrap(font, t, 60))

    b = layout("가나다라마바사아자차카타파하" * 3, 130, 90, max_size=60)
    assert 0 < b["size"] <= 60 and b["fits"], b
    assert b["lines"] == wrap(ImageFont.truetype(FONT, b["size"]), "가나다라마바사아자차카타파하" * 3, 130)
    im = render({**b, "bold": 1})
    assert im.size == (130, 90) and im.getchannel("A").getbbox(), "empty render"
    x0, y0, x1, y1 = im.getchannel("A").getbbox()
    assert 0 <= x0 and x1 <= 130 and 0 <= y0 and y1 <= 90, (x0, y0, x1, y1)
    forced = layout("가" * 200, 40, 30, size=40)
    assert not forced["fits"]
    assert layout("짧음", 60, 20, font=FONT)["size"] >= 4
    ed = render({"w": 80, "h": 40, "text": "테스트 문장입니다", "size": None})
    assert ed.size == (80, 40) and ed.getchannel("A").getbbox(), "editor box render failed"
    assert render({"w": 20, "h": 10, "text": ""}).getchannel("A").getbbox() is None

    # wrap fixtures consumed by editor.html to detect a JS/python divergence
    fx = []
    for text, maxw, size in SAMPLES:
        f = ImageFont.truetype(FONT, size)
        fx.append({"text": text, "maxw": maxw, "size": size, "font": os.path.relpath(FONT, ROOT),
                   "spacing": 0.0, "lines": wrap(f, text, maxw)})
    with open(FIXTURES, "w", encoding="utf-8") as fh:
        json.dump(fx, fh, ensure_ascii=False, indent=1)

    # server endpoints
    tmp = tempfile.mkdtemp()
    httpd = make_server(0, tmp)
    port = httpd.server_address[1]
    th = threading.Thread(target=httpd.serve_forever, daemon=True)
    th.start()
    base = f"http://127.0.0.1:{port}"
    try:
        html = urllib.request.urlopen(base + "/").read().decode()
        assert "wrap_fixtures.json" in html and "<canvas" in html
        ttf = urllib.request.urlopen(base + "/fonts/Gaegu-Bold.ttf").read()
        assert ttf[:4] == b"\x00\x01\x00\x00" and len(ttf) > 10000
        assert len(json.loads(urllib.request.urlopen(base + "/pages").read())) > 0
        assert len(json.loads(urllib.request.urlopen(base + "/wrap_fixtures.json").read())) == len(fx)
        req = urllib.request.Request(base + "/save", method="POST",
                                     data=json.dumps({"name": "selftest", "boxes": [{"x": 1}]}).encode(),
                                     headers={"Content-Type": "application/json"})
        r = json.loads(urllib.request.urlopen(req).read())
        assert r["saved"].endswith("selftest.json"), r
        assert json.load(open(os.path.join(tmp, "selftest.json"), encoding="utf-8"))["boxes"] == [{"x": 1}]
    finally:
        httpd.shutdown()
    print(f"selftest ok: fit {b['size']}pt {len(b['lines'])} lines, {len(fx)} wrap fixtures, "
          f"server on :{port} ok")


# ---------------------------------------------------------------- cli

def main(argv=None):
    if "--selftest" in (argv or sys.argv)[1:]:
        return selftest() or 0
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    sub = ap.add_subparsers(dest="cmd", required=True)

    f = sub.add_parser("fit", help="fit text into w x h and write png/json")
    f.add_argument("--w", type=int, required=True)
    f.add_argument("--h", type=int, required=True)
    f.add_argument("--text", required=True)
    f.add_argument("--font", default=FONT)
    f.add_argument("--size", type=float, help="force this size instead of auto-fitting")
    f.add_argument("--min-size", type=float, default=6)
    f.add_argument("--max-size", type=float, default=200)
    f.add_argument("--align", choices=["left", "center", "right"], default="center")
    f.add_argument("--valign", choices=["top", "center"], default="center")
    f.add_argument("--line", type=float, default=1.15)
    f.add_argument("--spacing", type=float, default=0.0)
    f.add_argument("--padding", type=int, default=0)
    f.add_argument("--bold", type=int, default=0)
    f.add_argument("--out")
    f.add_argument("--json")

    s = sub.add_parser("serve", help="WYSIWYG editor over http")
    s.add_argument("--port", type=int, default=8765)
    s.add_argument("--boxdir", default=BOXDIR)

    a = ap.parse_args(argv)
    if a.cmd == "serve":
        httpd = make_server(a.port, a.boxdir)
        print(f"editor: http://127.0.0.1:{httpd.server_address[1]}/  (boxes -> {a.boxdir})")
        try:
            httpd.serve_forever()
        except KeyboardInterrupt:
            pass
        return 0

    box = layout(a.text, a.w, a.h, a.font, a.size, a.min_size, a.max_size, a.align,
                 a.line, a.spacing, a.padding, a.valign, a.bold)
    im = render(box)
    png = a.out or os.path.join(ROOT, "out", "box.png")
    os.makedirs(os.path.dirname(os.path.abspath(png)), exist_ok=True)
    im.save(png)
    box["png"] = os.path.relpath(os.path.abspath(png), ROOT).replace(os.sep, "/")
    if a.json:
        os.makedirs(os.path.dirname(os.path.abspath(a.json)), exist_ok=True)
        with open(a.json, "w", encoding="utf-8") as fh:
            json.dump(box, fh, ensure_ascii=False, indent=1)
    print(f"{box['size']:.1f}pt {len(box['lines'])} lines -> {box['png']}"
          + ("" if box["fits"] else "  OVERFLOW"))
    for l in box["lines"]:
        print("  |" + l)
    return 0 if box["fits"] else 2


if __name__ == "__main__":
    sys.exit(main())
