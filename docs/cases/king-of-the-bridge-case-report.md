# King of the Bridge (Unity) — case report

- Game: Steam 2523120, v1.024 (build 13785153). Unity 2022.3.5f1, Mono. Repo `kingofthebridge-l10n-ko`, track A (`zstd_delta` installer), no pipeline adapter (`scripts/extract.py`, `scripts/build_patch.py`).
- No Korean locale (en + nl only). Decision (user): Korean overwrites the **Dutch (nl)** slot; rulebook page images redrawn in Korean.

## Mechanism
- Unity Localization + Addressables: `StreamingAssets/aa/StandaloneWindows64/localization-string-tables-<lang>_assets_all.bundle` (MonoBehaviour per table, `m_TableData[{m_Id,m_Localized}]`). 327 rows, 6 tables; en/nl share the same `m_Id` set. `RulePageTable`/`SecretPageTable` hold sprite refs, not text.
- Rulebook = 42 Texture2D (320x320 RGBA, white strokes on transparent) in `localization-assets-<lang>_assets_all.bundle`. Names differ per locale (en `page2`/`page_contents_0`/`page_tutorial_1`/`secretpage0_eng` vs nl `page_2`/`page_contents0`/`page_t1`/`secretpage0_dutch`); `scripts/pages.py nl_name` maps them. Title logo is an image, left as is.
- Language row in-game: Options > Text > Language; nl label is MenuTable `DUTCH` -> translated "한국어".

## Pitfalls
- **Release builds strip MonoBehaviour typetrees**: UnityPy needs `TypeTreeGeneratorAPI` (`pip install --user --break-system-packages`) + `TypeTreeGenerator("2022.3.5f1").load_local_game(<install root>)` (root dir, not `_Data`).
- **Addressables CRC**: modified bundle -> `CRC Mismatch ... Will not load AssetBundle`, all keys show "No translation found". `catalog.json` is plain JSON but `m_ExtraDataString` is base64 of a binary blob with UTF-16 JSON; zero `m_Crc` by regex on the **bytes** (`m\0_\0C\0r\0c\0"\0:\0` + digits), keeping length (`0` + UTF-16 spaces). A string-level decode misses records (alignment/length prefixes).
- **Fonts**: every text object uses static `alagard SDF` (sharedassets0, atlas 512, 99 glyphs, no Hangul). A fallback in `TMP Settings`/a fallback asset did not work (the shipped dynamic `LiberationSans SDF - Fallback` atlas is 0x0). Working fix: turn `alagard SDF` itself into a dynamic asset (`m_AtlasPopulationMode=1`, `m_SourceFontFile` -> Font `LiberationSans` in resources.assets now carrying Galmuri11-Bold bytes), clear glyph/character/kerning tables, blank readable 2048 Alpha8 atlas, material `_TextureWidth/_Height/_GradientScale`, and add `resources.assets` to sharedassets0's externals. Result: 0 "character not found" log lines.
- Windows installer (`tools/build/installer/installer.py` `delta`): mmapping the backup while opening `dst` for write failed with `Errno 22` on this ReFS-like drive. Small originals (<256 MB) are now read as bytes; mmap only for big ones. Do not test write access by opening a game file with `open(p,"wb")` (truncates it).
- Steam must be logged in or a login dialog covers the game; screenshots via `PrintWindow`; clicks need the window in foreground (SendInput after `Alt` keypress).

## Rulebook page tools (`scripts/pagetools/`)
`shapes.py` (ink -> Coons patch with cubic Bezier edges / outline), `textbox.py` + `editor.html` (fit + wrap Korean into a box, WYSIWYG), `warp.py` (flat text -> patch), `pages.py` `boxes`/patch `erase`. Page specs: `assets/pages_spec/*.json` (+ `.txt` EN/KO). Body size is 15-18 px (Gaegu Bold); Korean is wider than English caps.

## Verification
- PO: 327 entries, validate OK. Build: `pages replaced: 42`; 0 TMP glyph warnings, 0 exceptions in `Player.log`.
- In game: Korean menu (시작/추가/설정/종료), tutorial screen with Korean rulebook page ("고발" page, 3 steps) and "[ SPACE ] 고발".
- Installer exe: install -> all 5 files `sha256 == sha256_patched`; `--uninstall` -> all `sha256_original`, `kor_bak` removed.
- Not checked in game: every rulebook page individually, secret pages, credits.
