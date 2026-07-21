#!/usr/bin/env python3
"""Vercel build step: sync static UI + ensure demo DB exists."""
from __future__ import annotations

import shutil
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SRC = ROOT / "dashboard" / "frontend" / "index.html"
PUBLIC = ROOT / "public"
DEST = PUBLIC / "index.html"
DEMO_DB = ROOT / "deploy" / "demo_ledger.db"


def main() -> None:
    PUBLIC.mkdir(parents=True, exist_ok=True)
    if not SRC.is_file():
        raise SystemExit(f"Missing frontend: {SRC}")
    shutil.copy2(SRC, DEST)
    print(f"Copied {SRC} → {DEST}")

    if not DEMO_DB.is_file() or DEMO_DB.stat().st_size < 1024:
        print("Building demo SQLite…")
        subprocess.check_call([sys.executable, str(ROOT / "deploy" / "build_demo_db.py")])
    else:
        print(f"Demo DB ready: {DEMO_DB} ({DEMO_DB.stat().st_size} bytes)")


if __name__ == "__main__":
    main()
