"""Build + install the King of the Bridge Korean patch.

Korean replaces the Dutch (nl) locale:
- nl string tables  <- locales/game_ko_approved.po (English rows, Korean text)
- nl rulebook pages <- assets/pages/page_*.png (redrawn, optional: only files present)
- TMP: every text object uses "alagard SDF" (static, no Hangul). It becomes a dynamic font asset whose source
  font is the Font object LiberationSans (resources.assets) carrying Galmuri11-Bold bytes; the glyph cache is
  cleared and the atlas replaced by a blank readable page, so TMP rasterizes all glyphs at runtime.
  sharedassets0.assets gets a new external -> resources.assets for that reference.
- catalog.json: m_Crc zeroed (same-width, UTF-16 padded) so Addressables accepts rewritten bundles.

Outputs go to out/ at game-relative paths, pristine originals to backup/ (installer zstd delta),
optional install into the game (originals kept as *.orig).
usage: python3 scripts/build_patch.py [--game DIR] [--no-install]
"""
import argparse, base64, json, os, re, shutil, sys
import polib, UnityPy
from UnityPy.helpers.TypeTreeGenerator import TypeTreeGenerator
from UnityPy.files.SerializedFile import FileIdentifier

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
D = "King of the Bridge_Data"
AA = f"{D}/StreamingAssets/aa"
BUN = f"{AA}/StandaloneWindows64"
TABLES = f"{BUN}/localization-string-tables-dutch(nl)_assets_all.bundle"
PAGES = f"{BUN}/localization-assets-dutch(nl)_assets_all.bundle"
EN_TABLES = f"{BUN}/localization-string-tables-english(en)_assets_all.bundle"
CATALOG = f"{AA}/catalog.json"
RES, SHARED0, SHARED2 = f"{D}/resources.assets", f"{D}/sharedassets0.assets", f"{D}/sharedassets2.assets"
DLL = f"{D}/Managed/Assembly-CSharp.dll"
TARGETS = (TABLES, PAGES, CATALOG, RES, SHARED0, SHARED2, DLL)
FONT = os.path.join(ROOT, "assets", "fonts", "Galmuri11-Bold.ttf")
PO = os.path.join(ROOT, "locales", "game_ko_approved.po")
PAGE_DIR = os.path.join(ROOT, "assets", "pages")
LINK_DIR = os.path.join(ROOT, "assets", "links")  # table-of-contents entry textures (link_<id>_<l|r>)
ALAGARD_PATH_ID = 155   # "alagard SDF" in sharedassets0.assets
ATLAS, POINT, PAD = 2048, 33, 5  # blank dynamic atlas size, sample size (3x Galmuri 11px), SDF padding
FONT_PATH_ID = 23       # Font "LiberationSans" in resources.assets


def src(game, rel):
    """Pristine file: the .orig backup if we already installed once."""
    p = os.path.join(game, rel)
    return p + ".orig" if os.path.exists(p + ".orig") else p


def assets_env(game, rel):
    """Release builds strip MonoBehaviour typetrees; regenerate them from Managed/."""
    env = UnityPy.load(src(game, rel))
    g = TypeTreeGenerator("2022.3.5f1")
    g.load_local_game(game)
    env.typetree_generator = g
    return env


def write(out, rel, data):
    os.makedirs(os.path.dirname(os.path.join(out, rel)), exist_ok=True)
    open(os.path.join(out, rel), "wb").write(data)


def patch_tables(game, out):
    ko = {e.msgctxt: e.msgstr for e in polib.pofile(PO)}
    assert all(ko.values()), "untranslated entries in PO"
    en = {}
    for o in UnityPy.load(src(game, EN_TABLES)).objects:
        if o.type.name == "MonoBehaviour":
            t = o.read_typetree()
            en[t["m_Name"].rsplit("_", 1)[0]] = t["m_TableData"]
    env = UnityPy.load(src(game, TABLES))
    n = 0
    for o in env.objects:
        if o.type.name != "MonoBehaviour":
            continue
        t = o.read_typetree()
        name = t["m_Name"].rsplit("_", 1)[0]
        rows = {e["m_Id"]: e for e in t["m_TableData"]}
        for e in en[name]:  # same key set as English
            row = rows.setdefault(e["m_Id"], {"m_Id": e["m_Id"], "m_Localized": "", "m_Metadata": {"m_Items": []}})
            row["m_Localized"] = ko.get(f"{name}/{e['m_Id']}", e["m_Localized"])
            n += f"{name}/{e['m_Id']}" in ko
        t["m_TableData"] = list(rows.values())
        o.save_typetree(t)
    assert n == len(ko), f"{n} rows applied, PO has {len(ko)}"
    write(out, TABLES, env.file.save(packer="lz4"))


def patch_pages(game, out):
    from PIL import Image
    env = UnityPy.load(src(game, PAGES))
    hit = 0
    for o in env.objects:
        if o.type.name in ("Texture2D", "Sprite"):
            d = o.read()
            f = os.path.join(PAGE_DIR, d.m_Name + ".png")
            if o.type.name == "Texture2D" and os.path.exists(f):
                d.image = Image.open(f).convert("RGBA")
                d.save()
                hit += 1
    print("pages replaced:", hit)
    write(out, PAGES, env.file.save(packer="lz4"))


def patch_links(game, out):
    """Rulebook table-of-contents entries: textures link_* in sharedassets2.assets."""
    from PIL import Image
    env = UnityPy.load(src(game, SHARED2))
    hit = 0
    for o in env.objects:
        if o.type.name == "Texture2D":
            d = o.read()
            f = os.path.join(LINK_DIR, d.m_Name + ".png")
            if d.m_Name.startswith("link_") and os.path.exists(f):
                im = Image.open(f).convert("RGBA")
                assert im.size == (d.m_Width, d.m_Height), (d.m_Name, im.size)
                d.image = im
                d.save()
                hit += 1
    print("link textures replaced:", hit)
    write(out, SHARED2, env.file.save())


def patch_dll(game, out):
    """Steam achievement names/descriptions -> Korean (IL patch of Assembly-CSharp.dll, see scripts/patch_dll.py)."""
    import subprocess
    subprocess.run([sys.executable, os.path.join(ROOT, "scripts", "patch_dll.py"), "--src", src(game, DLL),
                    "--dst", os.path.join(out, DLL)], check=True)


def patch_resources(game, out):
    env = assets_env(game, RES)
    o = next(o for o in env.objects if o.path_id == FONT_PATH_ID)
    assert o.type.name == "Font"
    t = o.read_typetree()
    t["m_FontData"] = list(open(FONT, "rb").read())
    t["m_FontNames"] = ["Galmuri11"]
    o.save_typetree(t)
    write(out, RES, env.file.save())


def patch_shared0(game, out):
    from fontTools.ttLib import TTFont
    tt = TTFont(FONT)
    upm, hh, os2 = tt["head"].unitsPerEm, tt["hhea"], tt["OS/2"]
    env = assets_env(game, SHARED0)
    sf = env.assets[0]
    ext = next((i for i, x in enumerate(sf.externals) if x.path == "resources.assets"), None)
    if ext is None:
        x = FileIdentifier.__new__(FileIdentifier)
        x.path, x.guid, x.type, x.temp_empty = "resources.assets", b"\0" * 16, 0, ""
        sf.externals.append(x)
        ext = len(sf.externals) - 1
    byid = {o.path_id: o for o in env.objects}
    t = byid[ALAGARD_PATH_ID].read_typetree()
    assert t["m_Name"] == "alagard SDF"
    s = POINT / upm
    asc, desc = hh.ascent * s, hh.descent * s
    t["m_SourceFontFile"] = {"m_FileID": ext + 1, "m_PathID": FONT_PATH_ID}
    t["m_AtlasPopulationMode"] = 1
    t["m_AtlasWidth"] = t["m_AtlasHeight"] = ATLAS
    t["m_AtlasPadding"] = PAD
    t["m_FaceInfo"].update(m_FamilyName="Galmuri11", m_StyleName="Bold", m_PointSize=POINT, m_UnitsPerEM=upm,
                           m_LineHeight=(hh.ascent - hh.descent + hh.lineGap) * s, m_AscentLine=asc, m_DescentLine=desc,
                           m_CapLine=os2.sCapHeight * s, m_MeanLine=os2.sxHeight * s,
                           m_SuperscriptOffset=asc, m_SubscriptOffset=desc,
                           m_UnderlineOffset=tt["post"].underlinePosition * s,
                           m_UnderlineThickness=tt["post"].underlineThickness * s)
    for k in ("m_GlyphTable", "m_CharacterTable", "m_UsedGlyphRects", "m_glyphInfoList", "m_KerningTable"):
        if k in t:
            t[k] = [] if not isinstance(t[k], dict) else {**t[k], **{kk: [] for kk, vv in t[k].items() if isinstance(vv, list)}}
    t["m_FontFeatureTable"]["m_GlyphPairAdjustmentRecords"] = []
    t["m_FreeGlyphRects"] = [{"m_X": 0, "m_Y": 0, "m_Width": ATLAS - 1, "m_Height": ATLAS - 1}]
    byid[ALAGARD_PATH_ID].save_typetree(t)
    tid = t["m_AtlasTextures"][0]["m_PathID"]
    tx = byid[tid].read_typetree()
    tx.update(m_Width=ATLAS, m_Height=ATLAS, m_CompleteImageSize=ATLAS * ATLAS, m_IsReadable=True,
              m_StreamData={"offset": 0, "size": 0, "path": ""})
    tx["image data"] = bytes(ATLAS * ATLAS)
    byid[tid].save_typetree(tx)
    mo = byid[t["material"]["m_PathID"]]
    m = mo.read_typetree()
    want = {"_TextureWidth": ATLAS, "_TextureHeight": ATLAS, "_GradientScale": PAD + 1}
    m["m_SavedProperties"]["m_Floats"] = [(k, float(want.get(k, v))) for k, v in m["m_SavedProperties"]["m_Floats"]]
    mo.save_typetree(m)
    write(out, SHARED0, env.file.save())


def patch_catalog(game, out):
    """Zero every m_Crc (0 = skip check). Same character width so extra-data offsets stay valid."""
    c = json.load(open(src(game, CATALOG), encoding="utf-8"))
    ex = base64.b64decode(c["m_ExtraDataString"])
    # records are length-prefixed binary around UTF-16 JSON, so match on bytes (alignment varies)
    pat = re.compile(rb'(m\x00_\x00C\x00r\x00c\x00"\x00:\x00)((?:\d\x00)+)')
    ex2, n = pat.subn(lambda m: m.group(1) + b"0\x00" + b" \x00" * (len(m.group(2)) // 2 - 1), ex)
    assert n >= 8 and len(ex2) == len(ex), f"{n} m_Crc records"
    c["m_ExtraDataString"] = base64.b64encode(ex2).decode()
    write(out, CATALOG, json.dumps(c, separators=(",", ":")).encode("utf-8"))


def stage_pristine(game, dst):
    for rel in TARGETS:
        d = os.path.join(dst, rel)
        if not os.path.exists(d):
            os.makedirs(os.path.dirname(d), exist_ok=True)
            shutil.copy2(src(game, rel), d)


def install(game, out):
    for rel in TARGETS:
        dst = os.path.join(game, rel)
        if not os.path.exists(dst + ".orig"):
            shutil.copy2(dst, dst + ".orig")
        shutil.copyfile(os.path.join(out, rel), dst)
        print("installed", dst)


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--game", default="/mnt/s/Installation/Steam/steamapps/common/King of the Bridge")
    ap.add_argument("--out", default=os.path.join(ROOT, "out"))
    ap.add_argument("--pristine", default=os.path.join(ROOT, "backup"))
    ap.add_argument("--no-install", action="store_true")
    a = ap.parse_args()
    patch_tables(a.game, a.out)
    patch_pages(a.game, a.out)
    patch_links(a.game, a.out)
    patch_dll(a.game, a.out)
    patch_resources(a.game, a.out)
    patch_shared0(a.game, a.out)
    patch_catalog(a.game, a.out)
    stage_pristine(a.game, a.pristine)
    if not a.no_install:
        install(a.game, a.out)
