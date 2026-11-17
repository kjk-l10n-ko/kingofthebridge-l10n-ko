"""Rulebook table-of-contents entry textures (sharedassets2.assets, link_<id>_<l|r>, 160x51 RGBA white on transparent).
Each entry is a bracket + number + short English summary; the _l / _r variants are the left/right page versions
(tilt mirrors). 68 textures: e1,e2 (extras), r0..r29 (rules), t1,t2 (tutorial).

  export    dump originals to assets/links_src/<name>.png
Render with scripts/pages.py using the env dirs (same spec format):
  PAGES_SRC_DIR=assets/links_src PAGES_SPEC_DIR=assets/links_spec PAGES_OUT_DIR=assets/links \\
  PAGES_PREV_DIR=out/preview_links python3 scripts/pages.py render [name ...]
build_patch.py applies assets/links/<name>.png onto the textures of the same name.
"""
import os, sys
import UnityPy

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
GAME = "/mnt/s/Installation/Steam/steamapps/common/King of the Bridge"
SA2 = f"{GAME}/King of the Bridge_Data/sharedassets2.assets"

if __name__ == "__main__" and sys.argv[1] == "export":
    p = SA2 + ".orig" if os.path.exists(SA2 + ".orig") else SA2
    out = os.path.join(ROOT, "assets/links_src"); os.makedirs(out, exist_ok=True)
    n = 0
    for o in UnityPy.load(p).objects:
        if o.type.name == "Texture2D":
            d = o.read()
            if d.m_Name.startswith("link_"):
                d.image.convert("RGBA").save(f"{out}/{d.m_Name}.png"); n += 1
    print("exported", n)
