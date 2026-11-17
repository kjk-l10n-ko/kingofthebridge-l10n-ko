"""Coons patch geometry shared by pagetools.  Coordinates are image pixels, origin top-left, y down.

A patch is {"top","bottom","left","right"}, each four cubic Bezier control points:
  top/bottom  left->right (P0..P3)
  left/right  top->bottom
Corners must coincide within 0.5 px.
patch(u,v): u left->right, v top->bottom, bilinearly blended Coons.
"""
import json
import numpy as np

KEYS = ("top", "bottom", "left", "right")


def bez(p, t):
    """Cubic Bezier eval; p=(4,2), t scalar -> (2,), array -> (...,2)."""
    p = np.asarray(p, dtype=float)
    t = np.asarray(t, dtype=float)[..., None]
    mt = 1.0 - t
    return mt ** 3 * p[0] + 3 * mt * mt * t * p[1] + 3 * mt * t * t * p[2] + t ** 3 * p[3]


def load(d):
    """Validate a patch dict / json str and return dict of (4,2) float arrays."""
    if isinstance(d, str):
        d = json.loads(d)
    p = {}
    for k in KEYS:
        try:
            a = np.asarray(d[k], dtype=float)
        except KeyError:
            raise ValueError(f"patch missing {k!r}")
        if a.shape != (4, 2) or not np.isfinite(a).all():
            raise ValueError(f"patch[{k!r}] must be 4 finite [x,y] points")
        p[k] = a
    # corner coincidence: top0=left0, top3=right0, bottom0=left3, bottom3=right3
    for a, b in ((p["top"][0], p["left"][0]), (p["top"][3], p["right"][0]),
                 (p["bottom"][0], p["left"][3]), (p["bottom"][3], p["right"][3])):
        if np.hypot(*(a - b)) > 0.5:
            raise ValueError(f"patch corners do not coincide: {a} vs {b}")
    return p


def eval_patch(patch, u, v):
    """Vectorized patch(u,v).  u,v arrays broadcast; returns (...,2)."""
    u = np.asarray(u, dtype=float)
    v = np.asarray(v, dtype=float)
    u, v = np.broadcast_arrays(u, v)
    top = bez(patch["top"], u)
    bot = bez(patch["bottom"], u)
    left = bez(patch["left"], v)
    right = bez(patch["right"], v)
    c00 = patch["top"][0]
    c10 = patch["top"][3]
    c01 = patch["bottom"][0]
    c11 = patch["bottom"][3]
    uu = u[..., None]
    vv = v[..., None]
    bil = (1 - vv) * ((1 - uu) * c00 + uu * c10) + vv * ((1 - uu) * c01 + uu * c11)
    return (1 - vv) * top + vv * bot + (1 - uu) * left + uu * right - bil


def _straight(a, b):
    a = np.asarray(a, dtype=float)
    b = np.asarray(b, dtype=float)
    return np.array([a, a + (b - a) / 3.0, a + 2.0 * (b - a) / 3.0, b])


def rect_patch(x, y, w, h, rot_deg=0.0):
    """Axis-aligned w x h box at (x,y), rotated by rot_deg (counter-clockwise positive)."""
    tl = np.array([x, y], float)
    tr = np.array([x + w, y], float)
    bl = np.array([x, y + h], float)
    br = np.array([x + w, y + h], float)
    if rot_deg:
        c = np.array([x + w / 2.0, y + h / 2.0])
        t = np.deg2rad(-rot_deg)  # y down: negate for counter-clockwise on screen
        R = np.array([[np.cos(t), -np.sin(t)], [np.sin(t), np.cos(t)]])
        tl, tr, bl, br = ((p - c) @ R.T + c for p in (tl, tr, bl, br))
    return {"top": _straight(tl, tr), "bottom": _straight(bl, br),
            "left": _straight(tl, bl), "right": _straight(tr, br)}


def to_json(patch):
    return {k: [[round(float(x), 3), round(float(y), 3)] for x, y in patch[k]] for k in KEYS}


def from_json(s):
    return load(json.loads(s) if isinstance(s, str) else s)


if __name__ == "__main__":
    r = rect_patch(10, 20, 30, 10)
    assert np.allclose(eval_patch(r, 0, 0), [10, 20])
    assert np.allclose(eval_patch(r, 1, 1), [40, 30])
    assert np.allclose(eval_patch(r, 0.5, 0.5), [25, 25])
    assert np.allclose(load(to_json(r))["top"], r["top"])
    assert np.allclose(bez(r["top"], [0, 1]), [[10, 20], [40, 20]])
    assert bez(r["top"], 0.5).shape == (2,)
    assert eval_patch(r, 0.5, 0.5).shape == (2,)
    assert eval_patch(r, np.zeros((3, 4)), np.ones((3, 4))).shape == (3, 4, 2)
    rr = load(rect_patch(0, 0, 10, 10, 90))
    # counter-clockwise on screen: top-left corner swings to bottom-left
    assert np.allclose(rr["top"][0], [0, 10], atol=1e-6)
    try:
        load({k: r[k] for k in KEYS[:-1]})
    except ValueError:
        pass
    else:
        raise AssertionError("missing-key patch accepted")
    bad = {k: r[k].copy() for k in KEYS}
    bad["right"][0] = [999, 999]
    try:
        load(bad)
    except ValueError:
        pass
    else:
        raise AssertionError("misplaced corner accepted")
    print("patch.py ok")
