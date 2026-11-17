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

## Table-of-contents entries and achievements (found after first review)
- TOC entries are not in the page textures: 68 textures `link_<id>_<l|r>` (160x51, e1/e2/r0..r29/t1/t2) in `sharedassets2.assets`, English bracket + number + summary. `scripts/links.py export`, specs in `assets/links_spec`, render with `pages.py` env dirs, applied by `build_patch.patch_links`. Tilted 12-13px text: `Image.rotate` makes 1px white strokes dark (un-premultiplied RGBA); render 4x-supersampled flat text and warp via `rect_patch` instead.
- Output page/link PNGs must keep RGB=255 under the alpha (dilation/antialias leave black RGB -> dark fringes); `pages.py` forces it.
- Achievement names/descriptions come from Steam's schema at runtime (`Achievement.Name/Description` via Facepunch; the local `appcache/stats/UserGameStatsSchema_<appid>.bin` has only `english`). Patched by IL edit of `SteamAchievements.TryGetNameOfAchievement/TryGetDescriptionOfAchievement` (id -> Korean `switch` prepended, falls through to Steam): `scripts/patch_dll.py` (docker dotnet sdk + Cecil, `scripts/dll/`), table `locales/achievements.json`. The Steam overlay unlock toast stays English.
- Page text: game shows page sprites with Point filter; 1px antialiased strokes break up. Text is 1px dilated by default (`texts[].bold`), titles 26px, body <=16px (`scripts/normalize_pages.py`). `scripts/preview_pages.py` makes game-like contact sheets without launching the game.

## Rulebook page tools (`scripts/pagetools/`)
`shapes.py` (ink -> Coons patch with cubic Bezier edges / outline), `textbox.py` + `editor.html` (fit + wrap Korean into a box, WYSIWYG), `warp.py` (flat text -> patch), `pages.py` `boxes`/patch `erase`. Page specs: `assets/pages_spec/*.json` (+ `.txt` EN/KO). Body size is 15-18 px (강부장님체); Korean is wider than English caps.

## Verification
- PO: 327 entries, validate OK. Build: `pages replaced: 42`; 0 TMP glyph warnings, 0 exceptions in `Player.log`.
- In game: Korean menu (시작/추가/설정/종료), tutorial screen with Korean rulebook page ("고발" page, 3 steps) and "[ SPACE ] 고발".
- Installer exe: install -> all 5 files `sha256 == sha256_patched`; `--uninstall` -> all `sha256_original`, `kor_bak` removed.
- Not checked in game: every rulebook page individually, secret pages, credits.

## Font / typography decision (user review of in-game screenshots)
- Bold/dilation clogs Hangul at 11-16px in game (blobs); Gaegu Bold was replaced by 강부장님체 (NAVER, `assets/fonts/KangBujangnim.ttf`), no bold. Page text: 3x supersampled + alpha gamma 0.6 (`pages.py`), TOC entries: one unified renderer `scripts/links_render.py` (same font/size/tilt for all 68; geometry for two-line entries copied from entry 00, per-agent specs only supply erase rects and text). Different per-agent techniques gave visibly different weights per entry — render every entry of a set with ONE script.
