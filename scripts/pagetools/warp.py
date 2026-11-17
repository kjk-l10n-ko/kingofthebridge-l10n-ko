"""Warp a flat text render (white on transparent) into a Coons-patch region, and back.

  apply   --flat out/box.png --patch p.json --canvas 320x320 --out out/w.png [--ss 4] [--bold N]
  unwarp  --image page.png --patch p.json --size 120x40 --out out/flat.png

Destination pixels are inverse-mapped onto the patch with Newton iterations, so the
mesh has no holes or seams: every dest pixel independently finds its (u,v).
"""
import argparse, json, os, sys

import numpy as np
from PIL import Image

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import patch as P

try:
    import cv2
except ImportError:  # pragma: no cover
    cv2 = None


def _ev(patch, u, v):
    """eval_patch -> (x, y) arrays broadcast to u/v shape."""
    p = P.eval_patch(patch, u, v)
    if isinstance(p, tuple):
        return p
    p = np.asarray(p)
    return p[..., 0], p[..., 1]


def _inverse(patch, X, Y, iters=16):
    """(u, v, inside) for canvas points X, Y (full 2-D arrays, continuous coords)."""
    t, b = np.asarray(patch["top"], float), np.asarray(patch["bottom"], float)
    src = np.float32([t[0], t[3], b[3], b[0]])
    dst = np.float32([[0, 0], [1, 0], [1, 1], [0, 1]])
    if cv2 is not None:
        H = cv2.getPerspectiveTransform(src, dst)
        ones = np.ones_like(X)
        q = H @ np.stack([X.ravel(), Y.ravel(), ones.ravel()])
        u = (q[0] / q[2]).reshape(X.shape)
        v = (q[1] / q[2]).reshape(X.shape)
    else:
        u = (X - src[0, 0]) / max(1e-6, src[1, 0] - src[0, 0])
        v = (Y - src[0, 1]) / max(1e-6, src[2, 1] - src[0, 1])
    u = u.astype(np.float64); v = v.astype(np.float64)
    eps = 1e-3
    for it in range(iters):
        x, y = _ev(patch, u, v)
        ex, ey = x - X, y - Y
        cand = (np.abs(u) < 1.2) & (np.abs(v) < 1.2)
        if not cand.any() or np.hypot(ex[cand], ey[cand]).max() < 0.02:
            break
        x1, y1 = _ev(patch, u + eps, v)
        x2, y2 = _ev(patch, u, v + eps)
        a, c = (x1 - x) / eps, (y1 - y) / eps
        bb, d = (x2 - x) / eps, (y2 - y) / eps
        det = a * d - bb * c
        det = np.where(np.abs(det) < 1e-9, 1e-9, det)
        du = np.clip((-ex * d + ey * bb) / det, -0.25, 0.25)
        dv = np.clip((-a * ey + c * ex) / det, -0.25, 0.25)
        u += du; v += dv
        bad = ~(np.isfinite(u) & np.isfinite(v))
        if bad.any():
            u[bad] = -9.0; v[bad] = -9.0
    x, y = _ev(patch, u, v)
    res = np.hypot(x - X, y - Y)
    inside = (u > -1e-3) & (u < 1 + 1e-3) & (v > -1e-3) & (v < 1 + 1e-3) & (res < 1.0)
    return u, v, inside


def _remap_alpha(alpha, map_x, map_y, interp=None):
    """Sample an alpha plane; outside = transparent."""
    if cv2 is not None:
        if interp is None:
            interp = cv2.INTER_LINEAR
        return cv2.remap(alpha, map_x.astype(np.float32), map_y.astype(np.float32),
                         interp, borderMode=cv2.BORDER_CONSTANT, borderValue=0)
    # numpy bilinear fallback: ponytail: 4-tap gather, no cv2 needed for small canvases
    h, w = alpha.shape
    x0 = np.floor(map_x).astype(int); y0 = np.floor(map_y).astype(int)
    fx = map_x - x0; fy = map_y - y0
    acc = np.zeros(map_x.shape, np.float64)
    for dy, wy in ((0, 1 - fy), (1, fy)):
        for dx, wx in ((0, 1 - fx), (1, fx)):
            xx = np.clip(x0 + dx, 0, w - 1); yy = np.clip(y0 + dy, 0, h - 1)
            ok = (x0 + dx >= 0) & (x0 + dx < w) & (y0 + dy >= 0) & (y0 + dy < h)
            acc += np.where(ok, alpha[yy, xx].astype(np.float64) * wx * wy, 0)
    return acc


def warp(flat, patch, canvas_size=(320, 320), supersample=4, bold=0):
    """Flat RGBA text image -> full-canvas RGBA with the text warped into `patch`."""
    W, H = int(canvas_size[0]), int(canvas_size[1])
    patch = patch if isinstance(patch.get("top"), np.ndarray) else P.load(patch)
    ss = max(1, int(supersample))
    # dest sample points in continuous canvas coords (pixel centers), supersampled
    X = (np.arange(W * ss, dtype=np.float64) + 0.5) / ss
    Y = (np.arange(H * ss, dtype=np.float64) + 0.5) / ss
    X, Y = np.meshgrid(X, Y)
    U = np.full(X.shape, -9.0)
    V = np.full(X.shape, -9.0)
    inside = np.zeros(X.shape, bool)
    if cv2 is not None:
        # Newton only on the boundary polygon (plus a pixel): the rest is outside
        pts = []
        ts = np.linspace(0, 1, 25)
        for k, rev in (("top", False), ("right", False), ("bottom", True), ("left", True)):
            e = P.bez(patch[k], ts)
            pts.append(e[::-1] if rev else e)
        poly = (np.concatenate(pts) * ss - 0.5) * 16
        m = cv2.fillPoly(np.zeros((H * ss, W * ss), np.uint8),
                         [np.round(poly).astype(np.int32)], 255, shift=4,
                         lineType=cv2.LINE_8)
        m = cv2.dilate(m, np.ones((3, 3), np.uint8))
        ys, xs = np.nonzero(m)
        u, v, ok = _inverse(patch, (xs + 0.5) / ss, (ys + 0.5) / ss)
        U[ys, xs] = u; V[ys, xs] = v; inside[ys, xs] = ok
    else:  # ponytail: no cv2 -> full-canvas Newton, slow but correct
        U, V, inside = _inverse(patch, X, Y)
    # patched point = flat point (u*fw, v*fh); cv2 index = coord - 0.5
    fw, fh = flat.size
    a = np.asarray(flat.convert("RGBA").getchannel("A"), np.float64)
    map_x = U * fw - 0.5
    map_y = V * fh - 0.5
    # nearest at supersampled points: box-downsampling then IS the area filter, so
    # identity/90deg warps stay pixel-exact instead of picking up resampling blur
    interp = cv2.INTER_NEAREST if (cv2 is not None and ss > 1) else None
    A = _remap_alpha(a, map_x, map_y, interp)
    A = np.where(inside, A, 0.0)
    A = np.clip(A, 0, 255).astype(np.uint8)
    if cv2 is not None:
        A = cv2.resize(A, (W, H), interpolation=cv2.INTER_AREA)
    else:
        A = A.reshape(H, ss, W, ss).mean(axis=(1, 3)).clip(0, 255).astype(np.uint8)
    if bold:
        if cv2 is not None:
            k = cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (2 * int(bold) + 1,) * 2)
            A = cv2.dilate(A, k)
        else:
            from PIL import ImageFilter
            A = np.asarray(Image.fromarray(A).filter(ImageFilter.MaxFilter(2 * int(bold) + 1)))
    out = Image.new("RGBA", (W, H), (255, 255, 255, 0))
    out.putalpha(Image.fromarray(A, "L"))
    return out


def unwarp(image, patch, w, h):
    """Canvas (page) -> flat w x h: sample the patch region back into a rectangle."""
    w, h = int(w), int(h)
    patch = patch if isinstance(patch.get("top"), np.ndarray) else P.load(patch)
    u = (np.arange(w, dtype=np.float64) + 0.5) / w
    v = (np.arange(h, dtype=np.float64) + 0.5) / h
    u, v = np.meshgrid(u, v)
    x, y = _ev(patch, u, v)
    src = np.asarray(image.convert("RGBA"))
    mx, my = (x - 0.5).astype(np.float32), (y - 0.5).astype(np.float32)
    if cv2 is not None:
        out = cv2.remap(src, mx, my, cv2.INTER_LINEAR, borderMode=cv2.BORDER_CONSTANT,
                        borderValue=(0, 0, 0, 0))
    else:
        chans = [_remap_alpha(src[:, :, c], x - 0.5, y - 0.5) for c in range(4)]
        out = np.stack(chans, -1).astype(np.uint8)
    return Image.fromarray(out, "RGBA")


# ---------------------------------------------------------------- selftest

def _grid(w, h):
    im = Image.new("RGBA", (w, h), (255, 255, 255, 0))
    a = np.zeros((h, w), np.uint8)
    a[::4, :] = 255; a[:, ::4] = 255          # grid lines
    a[:2, :] = 255; a[-2:, :] = 255            # solid frame
    a[:, :2] = 255; a[:, -2:] = 255
    im.putalpha(Image.fromarray(a, "L"))
    return im


def selftest():
    W = H = 160
    flat = _grid(64, 40)
    fa = np.asarray(flat.getchannel("A"), float)

    # (1) identity rect patch == input
    p = P.rect_patch(30, 40, 64, 40)
    out = warp(flat, p, (W, H), supersample=4)
    got = np.asarray(out)[40:80, 30:94, 3].astype(float)
    err = np.abs(got - fa).mean()
    assert err < 4, f"identity mean abs err {err:.2f}"

    # (2) 90 deg rotation vs PIL rotate: place PIL's result at the patch corner
    p2 = P.rect_patch(30, 40, 64, 40, 90)
    o2 = np.asarray(warp(flat, p2, (W, H), supersample=4).getchannel("A"), float)
    ref = np.asarray(flat.rotate(90, expand=True).getchannel("A"), float)
    cs = np.concatenate([p2[k] for k in P.KEYS])
    x0, y0 = int(round(cs[:, 0].min())), int(round(cs[:, 1].min()))
    place = o2[y0:y0 + ref.shape[0], x0:x0 + ref.shape[1]]
    assert place.shape == ref.shape, f"90deg extent {place.shape} vs {ref.shape}"
    rot_err = np.abs(place - ref).mean()
    assert rot_err < 4, f"90deg vs PIL rotate err {rot_err:.2f}"

    # (3) arc patch: flat points land where eval_patch says; no holes inside
    arc = {"top": [[40, 30], [70, 6], [110, 6], [140, 30]],
           "bottom": [[40, 110], [70, 118], [110, 118], [140, 110]],
           "left": [[40, 30], [34, 56], [34, 84], [40, 110]],
           "right": [[140, 30], [146, 56], [146, 84], [140, 110]]}
    dots = Image.new("RGBA", (64, 40), (255, 255, 255, 0))
    da = np.zeros((40, 64), np.uint8)
    for (dx, dy) in ((3, 3), (60, 3), (3, 36), (60, 36)):
        da[dy, dx] = 255
    dots.putalpha(Image.fromarray(da, "L"))
    od = np.asarray(warp(dots, arc, (W, H), supersample=4).getchannel("A"), float)
    for (fx, fy) in ((3, 3), (60, 3), (3, 36), (60, 36)):
        ex, ey = _ev(arc, np.array([(fx + 0.5) / 64]), np.array([(fy + 0.5) / 40]))
        ex, ey = float(ex[0]), float(ey[0])
        y0, y1 = int(max(0, ey - 4)), int(min(H, ey + 5))
        x0, x1 = int(max(0, ex - 4)), int(min(W, ex + 5))
        win = od[y0:y1, x0:x1]
        assert win.max() > 60, f"corner dot missing near {(ex, ey)}"
    # full white flat through arc: interior must be solid (no holes)
    white = Image.new("RGBA", (64, 40), (255, 255, 255, 255))
    ow = np.asarray(warp(white, arc, (W, H), supersample=4).getchannel("A"))
    inside_px = ow[40:100, 50:130]
    assert (inside_px > 0).mean() > 0.98, "holes inside warped region"
    assert inside_px.min() > 60, f"transparent hole inside, min {inside_px.min()}"

    # (4) unwarp(warp(x)) ~= x on a smooth glyph-like shape
    from PIL import ImageDraw
    sm = Image.new("L", (64, 40), 0)
    ImageDraw.Draw(sm).ellipse((8, 4, 56, 36), fill=255)
    smooth = Image.new("RGBA", (64, 40), (255, 255, 255, 0))
    smooth.putalpha(sm)
    sa = np.asarray(sm, float)
    back = np.asarray(unwarp(warp(smooth, arc, (W, H)), arc, *smooth.size).getchannel("A"), float)
    m = np.zeros_like(sa, bool); m[2:-2, 2:-2] = True
    e = np.abs(back - sa)[m].mean()
    assert e < 6, f"unwarp roundtrip err {e:.2f}"
    print(f"warp selftest ok (identity {err:.2f}, rot90 {rot_err:.2f}, roundtrip {e:.2f})")
    return 0


# ---------------------------------------------------------------- cli

def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--selftest", action="store_true")
    sub = ap.add_subparsers(dest="cmd")
    a = sub.add_parser("apply")
    a.add_argument("--flat"); a.add_argument("--patch"); a.add_argument("--canvas", default="320x320")
    a.add_argument("--out", required=True); a.add_argument("--ss", type=int, default=4)
    a.add_argument("--bold", type=int, default=0)
    un = sub.add_parser("unwarp")
    un.add_argument("--image"); un.add_argument("--patch"); un.add_argument("--size", required=True)
    un.add_argument("--out", required=True)
    args = ap.parse_args(argv)
    if args.selftest or not args.cmd:
        return selftest()
    patch = P.load(json.load(open(args.patch, encoding="utf-8")))
    if args.cmd == "apply":
        W, H = (int(v) for v in args.canvas.lower().split("x"))
        im = warp(Image.open(args.flat).convert("RGBA"), patch, (W, H), args.ss, args.bold)
    else:
        W, H = (int(v) for v in args.size.lower().split("x"))
        im = unwarp(Image.open(args.image).convert("RGBA"), patch, W, H)
    os.makedirs(os.path.dirname(os.path.abspath(args.out)), exist_ok=True)
    im.save(args.out)
    return 0


if __name__ == "__main__":
    sys.exit(main())
