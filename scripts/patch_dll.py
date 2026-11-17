#!/usr/bin/env python3
"""Patch Assembly-CSharp.dll so Steam achievement names/descriptions display in Korean.

Steam's achievement schema ships only English, and the game reads it at runtime via
Facepunch (Achievement.Name / Achievement.Description). The two getters in
SteamAchievements are prefixed with a static id->Korean lookup (strings embedded in the
dll); unknown ids fall through to the untouched Steam code path.

Usage:
  python3 scripts/patch_dll.py                 # game dll -> out/King of the Bridge_Data/Managed/
  python3 scripts/patch_dll.py --src X --dst Y
Needs docker (mcr.microsoft.com/dotnet/sdk:8.0) on first run; nuget restore is cached
inside the container image layer only, so ~10s per run.
"""
from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
GAME = Path("/mnt/s/Installation/Steam/steamapps/common/King of the Bridge")
REL = Path("King of the Bridge_Data/Managed/Assembly-CSharp.dll")
TABLE = ROOT / "locales/achievements.json"
OUT = ROOT / "out" / REL
IMAGE = "mcr.microsoft.com/dotnet/sdk:8.0"


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--src", type=Path, help="pristine Assembly-CSharp.dll")
    ap.add_argument("--dst", type=Path, default=OUT)
    ap.add_argument("--table", type=Path, default=TABLE)
    a = ap.parse_args()

    src = a.src or (GAME / REL)
    if not src.exists():
        raise SystemExit(f"missing {src}")
    # never feed an already patched dll back in
    orig = src.with_suffix(".dll.orig")
    if orig.exists():
        src = orig
    print(f"src: {src}")

    rows = json.loads(a.table.read_text(encoding="utf-8"))
    assert len(rows) == 37, len(rows)
    assert len({r["id"] for r in rows}) == 37
    a.dst.parent.mkdir(parents=True, exist_ok=True)

    with tempfile.TemporaryDirectory() as td:
        work = Path(td)
        sub = work / "src"
        sub.mkdir()
        shutil.copytree(ROOT / "scripts/dll", sub / "dll",
                        ignore=shutil.ignore_patterns("bin", "obj"))
        shutil.copy(src, sub / "in.dll")
        shutil.copy(a.table, sub / "achievements.json")
        cmd = [
            "docker", "run", "--rm", "-v", f"{sub}:/w", "-w", "/w",
            IMAGE, "bash", "-lc",
            "dotnet run --project /w/dll -c Release -v q -- "
            f"/w/in.dll /w/out.dll /w/achievements.json; rc=$?; chown -R {os.getuid()}:{os.getgid()} /w; exit $rc",
        ]
        r = subprocess.run(cmd, text=True)
        if r.returncode != 0:
            return r.returncode
        shutil.copy(sub / "out.dll", a.dst)
    print(f"dst: {a.dst} ({a.dst.stat().st_size} bytes)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
