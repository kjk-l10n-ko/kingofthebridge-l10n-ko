"""English Unity Localization string tables -> locales/game_ko_approved.po.

msgctxt = "<Table>/<m_Id>", msgid = English, Dutch text kept as a translator comment.
Existing msgstr in --out is preserved (re-extract after a game update is safe).
usage: python3 scripts/extract.py --game "<game dir>" [--out locales/game_ko_approved.po]
"""
import argparse, os
import polib, UnityPy

AA = "King of the Bridge_Data/StreamingAssets/aa/StandaloneWindows64"


def tables(path):
    """{table name without locale suffix: [(id, text), ...]} in bundle order."""
    out = {}
    for o in UnityPy.load(path).objects:
        if o.type.name == "MonoBehaviour":
            t = o.read_typetree()
            out[t["m_Name"].rsplit("_", 1)[0]] = [(r["m_Id"], r["m_Localized"]) for r in t["m_TableData"]]
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--game", required=True)
    ap.add_argument("--out", default="locales/game_ko_approved.po")
    a = ap.parse_args()
    aa = os.path.join(a.game, AA)
    en = tables(os.path.join(aa, "localization-string-tables-english(en)_assets_all.bundle"))
    nl = tables(os.path.join(aa, "localization-string-tables-dutch(nl)_assets_all.bundle"))
    old = {e.msgctxt: e for e in polib.pofile(a.out)} if os.path.exists(a.out) else {}
    po = polib.POFile(wrapwidth=0)
    po.metadata = {"Content-Type": "text/plain; charset=UTF-8", "Language": "ko"}
    for name, rows in en.items():
        nld = dict(nl.get(name, []))
        for i, text in rows:
            if not text:
                continue
            ctx = f"{name}/{i}"
            prev = old.get(ctx)
            po.append(polib.POEntry(
                msgctxt=ctx, msgid=text,
                msgstr=prev.msgstr if prev and prev.msgid == text else "",
                comment=f"nl: {nld[i]}" if nld.get(i) else "",
                flags=list(prev.flags) if prev and prev.msgid == text else [],
            ))
    po.save(a.out)
    print(f"{a.out}: {len(po)} entries")


if __name__ == "__main__":
    main()
