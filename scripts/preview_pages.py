"""Game-like preview of the rendered rulebook pages (no game launch needed).
Dark ink on cream paper, point-sampled at 1.11x like the game's page sprite (Point filter, ~355px for 320px).
  python3 scripts/preview_pages.py            -> out/game_preview_pages_{1..n}.png (8 pages each) + out/game_preview_links.png
Open the PNGs; zoom is 1:1 so what you see ~ what the game shows. Re-run after `pages.py render` / `links` render."""
import os
from PIL import Image

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
PAPER, INK, SCALE = (249, 235, 190), (50, 50, 40), 1.11


def shown(path):
    im = Image.open(path).convert("RGBA")
    ink = Image.new("RGBA", im.size, INK + (255,)); ink.putalpha(im.getchannel("A"))
    paper = Image.new("RGBA", im.size, PAPER + (255,)); paper.alpha_composite(ink)
    w, h = int(im.width * SCALE), int(im.height * SCALE)
    return paper.resize((w, h), Image.NEAREST)


def sheet(files, cols, out, pad=8):
    ims = [shown(f) for f in files]
    w, h = ims[0].size
    rows = (len(ims) + cols - 1) // cols
    W = Image.new("RGBA", (cols * (w + pad) + pad, rows * (h + pad) + pad), (40, 60, 45, 255))
    for i, im in enumerate(ims):
        W.paste(im, (pad + (i % cols) * (w + pad), pad + (i // cols) * (h + pad)))
    W.save(out)


def key(n):
    return (0, int(n.split("_")[1])) if n.startswith("page_") and n.split("_")[1].isdigit() else (1, n)


if __name__ == "__main__":
    d = os.path.join(ROOT, "assets/pages")
    names = sorted((f[:-4] for f in os.listdir(d) if f.endswith(".png")), key=key)
    for i in range(0, len(names), 8):
        sheet([f"{d}/{n}.png" for n in names[i:i + 8]], 4, os.path.join(ROOT, f"out/game_preview_pages_{i // 8 + 1}.png"))
    l = os.path.join(ROOT, "assets/links")
    if os.path.isdir(l):
        ln = sorted(f for f in os.listdir(l) if f.endswith(".png"))
        sheet([f"{l}/{n}" for n in ln], 4, os.path.join(ROOT, "out/game_preview_links.png"))
    print("ok", len(names))
