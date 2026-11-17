"""Extract text-region Coons patches (or ink outlines) from a rulebook page image.

  shapes.py list IMAGE [--out overlay.png] [--ink alpha|light] [--region x,y,w,h]
                      [--gap N] [--min-area N] [--max-area N] [--all] [--json]
  shapes.py extract IMAGE --block N [--straight] [--out patch.json] [--overlay png]
  shapes.py extract IMAGE --outline [--block N|--region ...] [--eps 1.0] [--out path]
  shapes.py overlay IMAGE PATCH.json --out png
  shapes.py --selftest

patch JSON: four cubic Bezier edges (top/bottom left->right, left/right top->bottom), corners
coincide.  list prints blocks (bbox, ink area, line count, tilt) and writes an indexed overlay.
"""
import argparse
import json
import os
import sys

import cv2
import numpy as np
from PIL import Image

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import patch as P

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
FONT = os.path.join(ROOT, "assets", "fonts", "Gaegu-Bold.ttf")
BG = (34, 40, 40, 255)


# ---------------------------------------------------------------- image / mask
def load_ink(path, ink="auto", region=None):
    """Bool ink mask (H,W).  ink: auto -> alpha when the image has useful alpha, else light."""
    im = Image.open(path).convert("RGBA")
    a = np.asarray(im)
    if ink == "auto":
        ink = "alpha" if a[..., 3].min() < 128 and (a[..., 3] > 40).any() else "light"
    if ink == "alpha":
        m = a[..., 3] > 40
    else:
        bg = np.median(a[..., :3].reshape(-1, 3), axis=0)
        m = np.abs(a[..., :3].astype(float) - bg).sum(2) > 90 if a[..., 3].max() == 255 \
            else a[..., :3].mean(2) > 128
    if region:
        x, y, w, h = parse_region(region) if isinstance(region, str) else region
        keep = np.zeros_like(m)
        keep[max(0, y):y + h, max(0, x):x + w] = True
        m &= keep
    return m


def parse_region(s):
    v = [int(t) for t in s.split(",")]
    if len(v) != 4:
        raise argparse.ArgumentTypeError("region must be x,y,w,h")
    return v


def merge(ink, gap):
    k = 2 * int(gap) + 1
    dil = cv2.dilate(ink.astype(np.uint8), cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (k, k)))
    return dil.astype(bool)


def stroke_width(ink):
    """Approx. stroke thickness (px) from the distance transform."""
    d = cv2.distanceTransform(ink.astype(np.uint8), cv2.DIST_L2, 3)
    v = d[d > 0]
    return 2.0 * float(np.median(v)) if len(v) else 2.0


# ---------------------------------------------------------------- geometry helpers
def _tilt(xs, ys):
    """Reading-direction angle (deg): maximizes sharpness of horizontal line bands.

    PCA is misled by ragged/curved multi-line blocks; de-rotating until the ink rows
    concentrate into thin bands (peaky row histogram) finds the text direction instead.
    """
    dx = xs - xs.mean()
    dy = ys - ys.mean()
    n2 = float(len(xs)) ** 2

    def score(deg):
        t = np.deg2rad(deg)
        v = -dx * np.sin(t) + dy * np.cos(t)
        v = np.floor(v).astype(int)
        v -= v.min()
        return float(np.square(np.bincount(v)).sum()) / n2

    coarse = max((score(d), d) for d in np.arange(-40.0, 40.01, 1.0))[1]
    return max((score(d), d) for d in np.arange(coarse - 1.0, coarse + 1.001, 0.1))[1]


def _frame(ys, xs):
    """Reading frame: u along the text direction (left->right), v top->bottom."""
    cx, cy = xs.mean(), ys.mean()
    t = np.deg2rad(_tilt(xs, ys)) if len(xs) >= 8 else 0.0
    c, s = np.cos(t), np.sin(t)
    u = (xs - cx) * c + (ys - cy) * s
    v = -(xs - cx) * s + (ys - cy) * c
    return u, v, c, s, (cx, cy)


def bands(vals, empty=1.0):
    """Runs of occupied integer rows in a projection -> [(lo,hi), ...] inclusive."""
    lo = int(np.floor(vals.min()))
    hi = int(np.ceil(vals.max()))
    occ = np.zeros(hi - lo + 2, bool)
    occ[np.clip((vals - lo).astype(int), 0, len(occ) - 1)] = True
    idx = np.flatnonzero(occ)
    if len(idx) == 0:
        return []
    run = np.split(idx, np.flatnonzero(np.diff(idx) > empty) + 1)
    return [(lo + int(r[0]), lo + int(r[-1])) for r in run]


def _envelope(u, v, agg="min"):
    """Per-column (floor u) min/max v -> (cols, vals), only columns with ink."""
    b = np.floor(u).astype(int)
    b0 = int(b.min())
    out = np.full(int(b.max()) - b0 + 1, np.inf if agg == "min" else -np.inf)
    fn = np.minimum.at if agg == "min" else np.maximum.at
    fn(out, b - b0, v)
    ok = np.isfinite(out)
    return (np.arange(len(out)) + b0)[ok], out[ok]


def _robust_poly(x, y, deg=3, iters=2):
    x = np.asarray(x, float)
    y = np.asarray(y, float)
    deg = int(min(deg, max(1, len(x) - 1)))
    keep = np.ones(len(x), bool)
    c = np.polyfit(x, y, deg)
    for _ in range(iters):
        r = y - np.polyval(c, x)
        med = np.median(r[keep])
        mad = np.median(np.abs(r[keep] - med))
        keep = np.abs(r - med) <= max(3.0 * mad, 0.75)
        if keep.sum() < deg + 2:
            keep[:] = True
            break
        c = np.polyfit(x[keep], y[keep], deg)
    return c


def _clamped(coef, lo, hi):
    """Polynomial evaluated in [lo,hi], held flat outside (no wild extrapolation)."""
    return lambda x: np.polyval(coef, np.clip(x, lo, hi))


def _chord(a, fa, b, fb):
    return lambda x: fa + (fb - fa) * (np.asarray(x, float) - a) / max(b - a, 1e-9)


def _bez_fit(f, a, b, p0=None, p3=None, n=32):
    """Cubic Bezier approximating (t, f(t)); control points at thirds of the axis.

    Only the dependent coordinate is fitted (2 unknowns), so x stays monotone and smooth.
    """
    t = np.linspace(0, 1, n)
    xs = a + (b - a) * t
    ys = np.asarray(f(xs), float)
    if p0 is not None:
        ys[0] = p0
    if p3 is not None:
        ys[-1] = p3
    b1 = 3 * (1 - t) ** 2 * t
    b2 = 3 * (1 - t) * t ** 2
    rhs = ys - (1 - t) ** 3 * ys[0] - t ** 3 * ys[-1]
    y1, y2 = np.linalg.lstsq(np.stack([b1, b2], 1), rhs, rcond=None)[0]
    third = (b - a) / 3.0
    return np.array([[a, ys[0]], [a + third, y1], [b - third, y2], [b, ys[-1]]])


def _medfilt(a, k=5):
    """Running median of a 1-D array (edges padded)."""
    if len(a) < 3 or k < 3:
        return a
    p = np.pad(a, k // 2, mode="edge")
    return np.median(np.lib.stride_tricks.sliding_window_view(p, k), axis=1)


def fit_block(ink, deg=3, straight=False):
    """Coons patch hugging the ink of one text block (bool mask)."""
    ys, xs = np.nonzero(ink)
    if len(xs) < 4:
        raise ValueError("block needs at least 4 ink pixels")
    u, v, c, s, (cx, cy) = _frame(ys, xs)

    bl = bands(v)
    top_hi = bl[0][1] if bl else v.max()
    bot_lo = bl[-1][0] if bl else v.min()
    tsel = v <= top_hi + 1e-6
    bsel = v >= bot_lo - 1e-6

    x0, x1 = float(u.min()), float(u.max())
    colx, coly = _envelope(u[tsel], v[tsel], "min")
    ctop = _robust_poly(colx, coly, deg)
    topf = _clamped(ctop, colx.min(), colx.max())
    colx, coly = _envelope(u[bsel], v[bsel], "max")
    cbot = _robust_poly(colx, coly, deg)
    botf = _clamped(cbot, colx.min(), colx.max())
    if straight:
        topf = _chord(x0, topf(x0), x1, topf(x1))
        botf = _chord(x0, botf(x0), x1, botf(x1))
    vtl, vtr = float(topf(x0)), float(topf(x1))
    vbl, vbr = float(botf(x0)), float(botf(x1))

    # left/right: per-row extremes over all ink (drop sparse rows, median-smooth)
    rowv = np.floor(v).astype(int)
    r0 = int(rowv.min())
    n = int(rowv.max()) - r0 + 1
    lmin = np.full(n, np.inf)
    rmax = np.full(n, -np.inf)
    cnt = np.zeros(n)
    np.minimum.at(lmin, rowv - r0, u)
    np.maximum.at(rmax, rowv - r0, u)
    np.add.at(cnt, rowv - r0, 1)
    rows = np.arange(n) + r0
    ok = np.isfinite(lmin) & (cnt >= max(3.0, 0.35 * np.median(cnt[cnt > 0])))
    if ok.sum() < 4:
        ok = np.isfinite(lmin)
    edeg = min(deg, 2)   # short, ragged spans: quadratic is safer
    lum = _medfilt(np.where(ok, lmin, np.median(lmin[ok])))
    rum = _medfilt(np.where(ok, rmax, np.median(rmax[ok])))
    cl = _clamped(_robust_poly(rows[ok], lum[ok], edeg), rows[ok].min(), rows[ok].max())
    cr = _clamped(_robust_poly(rows[ok], rum[ok], edeg), rows[ok].min(), rows[ok].max())
    # a smooth lateral edge cannot enclose ragged line ends: fall back to the straight
    # local-frame side (still encloses all ink, keeps the warped layout undistorted)
    if np.any(lmin[ok] < cl(rows[ok]) - 0.5):
        cl = lambda y: np.full(np.shape(y), x0, float)
    if np.any(rmax[ok] > cr(rows[ok]) + 0.5):
        cr = lambda y: np.full(np.shape(y), x1, float)
    if straight:
        cl = lambda y: np.full(np.shape(y), x0, float)
        cr = lambda y: np.full(np.shape(y), x1, float)

    edges = {
        "top": _bez_fit(topf, x0, x1),
        "bottom": _bez_fit(botf, x0, x1),
        "left": _bez_fit(cl, vtl, vbl, p0=x0, p3=x0)[:, ::-1],
        "right": _bez_fit(cr, vtr, vbr, p0=x1, p3=x1)[:, ::-1],
    }
    # local (u,v) -> image coords
    R = np.array([[c, -s], [s, c]])
    T = np.array([cx, cy])
    return {k: e @ R.T + T for k, e in edges.items()}


def outline_paths(ink, eps=1.0, min_area=6):
    """Ink contours -> simplified closed cubic Bezier paths: [[ [p,p,p,p], ... ], ...]."""
    cs, _ = cv2.findContours(ink.astype(np.uint8), cv2.RETR_LIST, cv2.CHAIN_APPROX_NONE)
    paths = []
    for c in cs:
        if cv2.contourArea(c) < min_area and len(c) < 8:
            continue
        ap = cv2.approxPolyDP(c, eps, True).reshape(-1, 2).astype(float)
        if len(ap) < 2:
            continue
        segs = []
        n = len(ap)
        for i in range(n):
            p0, p1 = ap[(i - 1) % n], ap[i]
            p2, p3 = ap[(i + 1) % n], ap[(i + 2) % n]
            segs.append(np.array([p1, p1 + (p2 - p0) / 6.0, p2 - (p3 - p1) / 6.0, p2]).round(3).tolist())
        paths.append(segs)
    return paths


# ---------------------------------------------------------------- blocks
def _ncomp(pin):
    """Number of letter-sized connected ink components in a block."""
    n, _, st, _ = cv2.connectedComponentsWithStats(pin.astype(np.uint8))
    a = st[1:, cv2.CC_STAT_AREA]
    return int((a >= 3).sum())


def run_count(pin):
    """Median number of vertical ink runs per occupied column ~= number of text lines."""
    xs = np.nonzero(pin.any(0))[0]
    if not len(xs):
        return 0.0
    cols = pin[:, xs]
    d = np.diff(np.vstack([np.zeros((1, len(xs)), bool), cols,
                           np.zeros((1, len(xs)), bool)]).astype(np.int8), axis=0)
    runs = (d == 1).sum(0)
    runs = runs[runs > 0]
    return float(np.median(runs)) if len(runs) else 0.0


def find_blocks(ink, gap=None, min_area=40, max_area=6000, keep_all=False):
    """Dilate-merge ink -> labelled blocks (drawing-like ones skipped unless keep_all)."""
    if gap is None:                      # adaptive kernel: bridge ~1.5 stroke widths
        gap = max(2, int(round(1.5 * stroke_width(ink))))
    dil = merge(ink, gap)
    n, lab = cv2.connectedComponents(dil.astype(np.uint8))
    blocks = []
    for i in range(1, n):
        pin = ink & (lab == i)
        area = int(pin.sum())
        if area < min_area or area > max_area:
            continue
        ys, xs = np.nonzero(pin)
        u, v, c, s, _ = _frame(ys, xs)
        h = int(ys.max() - ys.min() + 1)
        if not keep_all:
            # drawing-like: one/few big strokes filling a tall or large region
            if _ncomp(pin) < 5 and (h > 28 or area > 900):
                continue
        blocks.append({
            "label": i, "area": area,
            "bbox": (int(xs.min()), int(ys.min()), int(xs.max() - xs.min() + 1), h),
            "lines": max(run_count(pin), float(len(bands(v)))),
            "tilt": float(np.degrees(np.arctan2(s, c))),
            "mask": pin,
        })
    return blocks


# ---------------------------------------------------------------- drawing
def _bgr(path):
    im = Image.open(path).convert("RGBA")
    bg = Image.new("RGBA", im.size, BG)
    bg.alpha_composite(im)
    return np.ascontiguousarray(np.asarray(bg.convert("RGB"))[:, :, ::-1])


def draw_patch(img, patch, color=(60, 180, 255), mesh=True, thick=1):
    for k in ("top", "bottom", "left", "right"):
        t = np.linspace(0, 1, 60)[:, None]
        pts = P.bez(patch[k], t).round().astype(np.int32)
        cv2.polylines(img, [pts], False, color, thick, cv2.LINE_AA)
    if mesh:
        u = np.linspace(0, 1, 7)
        v = np.linspace(0, 1, 5)
        for uu in u:
            cv2.polylines(img, [P.eval_patch(patch, np.full(41, uu), np.linspace(0, 1, 41)).round().astype(np.int32)],
                          False, (90, 90, 160), 1, cv2.LINE_AA)
        for vv in v:
            cv2.polylines(img, [P.eval_patch(patch, np.linspace(0, 1, 41), np.full(41, vv)).round().astype(np.int32)],
                          False, (90, 90, 160), 1, cv2.LINE_AA)


def _label(img, text, xy, color=(80, 255, 120)):
    f = cv2.FONT_HERSHEY_SIMPLEX
    (w, h), _ = cv2.getTextSize(text, f, 0.5, 1)
    x, y = max(0, xy[0]), max(12, xy[1])
    cv2.rectangle(img, (x, y - h - 3), (x + w + 4, y + 2), (0, 0, 0), -1)
    cv2.putText(img, text, (x + 2, y - 2), f, 0.5, color, 1, cv2.LINE_AA)


# ---------------------------------------------------------------- commands
def cmd_list(a):
    ink = load_ink(a.image, a.ink, a.region)
    blocks = find_blocks(ink, a.gap, a.min_area, a.max_area, a.all)
    img = _bgr(a.image)
    out = []
    for idx, b in enumerate(blocks):
        try:
            patch = fit_block(b["mask"])
        except ValueError:
            continue
        draw_patch(img, patch)
        _label(img, str(idx), (b["bbox"][0], b["bbox"][1]))
        x, y, w, h = b["bbox"]
        row = {"block": idx, "bbox": [x, y, w, h], "area": b["area"],
               "lines": b["lines"], "tilt": round(b["tilt"], 1)}
        out.append(row)
        print(f"{idx:3d}  bbox=({x:3d},{y:3d},{w:3d},{h:3d})  area={b['area']:4d}  "
              f"lines={b['lines']:.0f}  tilt={b['tilt']:+.1f}")
    if not blocks:
        print("no blocks; lower --min-area / raise --gap / use --all", file=sys.stderr)
    if a.out:
        os.makedirs(os.path.dirname(os.path.abspath(a.out)), exist_ok=True)
        cv2.imwrite(a.out, img)
        print(f"overlay -> {a.out}", file=sys.stderr)
    if a.json:
        print(json.dumps(out, indent=1))


def cmd_extract(a):
    ink = load_ink(a.image, a.ink, a.region)
    if a.outline:
        m = ink
        if a.block is not None:
            blocks = find_blocks(ink, a.gap, a.min_area, a.max_area, a.all)
            if a.block >= len(blocks):
                sys.exit(f"--block {a.block} out of range ({len(blocks)} blocks)")
            m = blocks[a.block]["mask"]
        res = {"paths": outline_paths(m, a.eps)}
        text = json.dumps(res, indent=1)
    else:
        if a.block is None:
            sys.exit("extract needs --block N (see `list`) or --outline")
        blocks = find_blocks(ink, a.gap, a.min_area, a.max_area, a.all)
        if a.block >= len(blocks):
            sys.exit(f"--block {a.block} out of range ({len(blocks)} blocks)")
        patch = fit_block(blocks[a.block]["mask"], straight=a.straight)
        text = json.dumps(P.to_json(patch), indent=1)
    if a.out:
        os.makedirs(os.path.dirname(os.path.abspath(a.out)), exist_ok=True)
        open(a.out, "w", encoding="utf-8").write(text + "\n")
        print(f"wrote {a.out}", file=sys.stderr)
    else:
        print(text)
    if a.overlay:
        img = _bgr(a.image)
        if not a.outline:
            draw_patch(img, patch)
        cv2.imwrite(a.overlay, img)
        print(f"overlay -> {a.overlay}", file=sys.stderr)


def cmd_overlay(a):
    patch = P.load(json.load(open(a.patch, encoding="utf-8")))
    img = _bgr(a.image)
    draw_patch(img, patch)
    os.makedirs(os.path.dirname(os.path.abspath(a.out)), exist_ok=True)
    cv2.imwrite(a.out, img)
    print(f"overlay -> {a.out}")


# ---------------------------------------------------------------- selftest
def _arc_text_image(size=(640, 480), radius=300, text="IIIIIIIIII"):
    """Text bent onto a known circle; returns (ink, center, r_top, r_bot)."""
    from PIL import ImageDraw, ImageFont
    font = ImageFont.truetype(FONT, 26)
    strip = Image.new("L", (500, 80), 0)
    ImageDraw.Draw(strip).text((8, 8), text, font=font, fill=255)
    flat = np.asarray(strip) > 40
    ys, xs = np.nonzero(flat)
    x0 = (xs.min() + xs.max()) / 2.0
    ybase = float(ys.max())
    cx, cy = size[0] / 2.0, size[1] + 120.0
    ang = (xs - x0) / radius
    rho = radius + ybase - ys            # glyph top -> larger radius (upper arc)
    px = np.round(cx + rho * np.sin(ang)).astype(int)
    py = np.round(cy - rho * np.cos(ang)).astype(int)
    ink = np.zeros((size[1], size[0]), bool)
    ok = (px >= 0) & (px < size[0]) & (py >= 0) & (py < size[1])
    ink[py[ok], px[ok]] = True
    return ink, (cx, cy), float(radius + ybase - ys.min()), float(radius)


def selftest():
    r = P.rect_patch(10, 20, 30, 10)
    assert np.allclose(P.eval_patch(r, 0.5, 0.5), [25, 25])
    assert np.allclose(P.bez(r["top"], 1), [40, 20])

    ink, ctr, r_top, r_bot = _arc_text_image()
    assert ink.sum() > 200, ink.sum()
    patch = P.load(P.to_json(fit_block(ink)))
    for name, rho in (("top", r_top), ("bottom", r_bot)):
        pts = P.bez(patch[name], np.linspace(0, 1, 41))
        d = np.hypot(pts[:, 0] - ctr[0], pts[:, 1] - ctr[1])
        err = np.abs(d - rho).max()
        assert err <= 3.0, f"{name} edge off circle by {err:.2f}px"
    # left/right edges stay on the ink's angular span
    lu = P.bez(patch["left"], [0, 1])
    ru = P.bez(patch["right"], [0, 1])
    assert np.hypot(lu[1, 0] - lu[0, 0], lu[1, 1] - lu[0, 1]) > 5
    assert np.hypot(ru[1, 0] - ru[0, 0], ru[1, 1] - ru[0, 1]) > 5
    # --straight really is straight
    sp = fit_block(ink, straight=True)
    t = sp["top"]
    assert np.allclose(t[1], t[0] + (t[3] - t[0]) / 3, atol=1e-6)
    assert np.allclose(t[2], t[0] + 2 * (t[3] - t[0]) / 3, atol=1e-6)
    # outline: a filled disc -> one closed bezier path hugging the disc
    disk = np.zeros((80, 80), bool)
    cv2.circle(disk.view(np.uint8).reshape(80, 80), (40, 40), 20, 1, -1)
    paths = outline_paths(disk)
    assert paths and all(len(s) == 4 for p in paths for s in p)
    pts = np.array([s[0] for s in paths[0]])
    assert np.abs(np.hypot(pts[:, 0] - 40, pts[:, 1] - 40) - 20).max() <= 2.5
    print("shapes.py selftest ok  (arc top/bottom within 3px, straight + outline ok)")


# ---------------------------------------------------------------- cli
def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--selftest", action="store_true")
    sp = ap.add_subparsers(dest="cmd")

    def common(p):
        p.add_argument("image")
        p.add_argument("--ink", default="auto", choices=("auto", "alpha", "light"))
        p.add_argument("--region", type=parse_region, help="x,y,w,h")
        p.add_argument("--gap", type=int, help="bridge gaps up to ~2*N px (default: auto from stroke width)")
        p.add_argument("--min-area", type=int, default=40)
        p.add_argument("--max-area", type=int, default=6000)
        p.add_argument("--all", action="store_true", help="keep drawing-like blocks too")
        p.add_argument("--out")

    pl = sp.add_parser("list")
    common(pl)
    pl.add_argument("--json", action="store_true")
    pl.set_defaults(fn=cmd_list)

    pe = sp.add_parser("extract")
    common(pe)
    pe.add_argument("--block", type=int)
    pe.add_argument("--straight", action="store_true")
    pe.add_argument("--outline", action="store_true")
    pe.add_argument("--eps", type=float, default=1.0)
    pe.add_argument("--overlay")
    pe.set_defaults(fn=cmd_extract)

    po = sp.add_parser("overlay")
    po.add_argument("image")
    po.add_argument("patch")
    po.add_argument("--out", required=True)
    po.set_defaults(fn=cmd_overlay)

    a = ap.parse_args(argv)
    if a.selftest:
        return selftest()
    if not a.cmd:
        ap.print_help()
        return 1
    if a.cmd == "list" and not a.out:
        a.out = "out/shapes_overlay.png"
    return a.fn(a)


if __name__ == "__main__":
    main()
