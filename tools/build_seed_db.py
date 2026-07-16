#!/usr/bin/env python3
"""Build the bundled default database (seed/fieldforce.db) from export files.

Runs the SAME import pipeline the app uses at runtime (auto_import.import_file),
so the seeded DB is byte-for-byte what a user would get by importing the files
themselves — no bypass, fully deterministic. The build then bundles the result
so a freshly downloaded app is usable immediately, while the git *source* stays
free of real data (the exports and the seed DB are inputs/outputs, gitignored).

Usage:
    python tools/build_seed_db.py <export.xlsx> [<export.xlsx> ...]
    # e.g.  python tools/build_seed_db.py exports/*.xlsx

Order does not matter for detection, but POS master should precede SalesApp so
visits link; the script sorts known types into the correct order automatically.
Output: seed/fieldforce.db (override with --out).
"""
from __future__ import annotations

import argparse
import glob
import os
import sys
import tempfile

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(REPO, "backend"))

# Import order so cross-references resolve (POS before visits before plan).
_ORDER = {"pos_master": 0, "salesapp": 1, "activity_plan": 2, "tourplan": 3, "workbook": 4}


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("files", nargs="+", help="export .xlsx files (globs ok)")
    ap.add_argument("--out", default=os.path.join(REPO, "seed", "fieldforce.db"))
    args = ap.parse_args()

    paths: list[str] = []
    for pat in args.files:
        paths.extend(sorted(glob.glob(pat)) or [pat])
    paths = [p for p in paths if os.path.exists(p)]
    if not paths:
        print("No input files found.", file=sys.stderr)
        return 2

    # Fresh, isolated DB in a temp dir — never touch the developer's runtime DB.
    workdir = tempfile.mkdtemp(prefix="seed_build_")
    os.environ["FFO_LOCAL"] = "1"
    os.environ["FFO_DATA_DIR"] = workdir
    os.environ["FFO_DB_PATH"] = os.path.join(workdir, "fieldforce.db")

    import db
    import auto_import
    db.bootstrap_db()  # no-op (no seed yet); keeps parity with runtime
    db.init_db()

    # Detect types, then import in dependency order.
    typed = []
    for p in paths:
        det = auto_import.detect(p)
        typed.append((_ORDER.get(det.get("type"), 9), p, det.get("type")))
    typed.sort(key=lambda x: x[0])

    for _, p, _t in typed:
        r = auto_import.import_file(p, os.path.basename(p))
        counts = {k: v for k, v in r.get("counts", {}).items() if not str(k).endswith("diff")}
        print(f"  imported {os.path.basename(p):45s} -> {r.get('detected')}: {counts}")

    os.makedirs(os.path.dirname(os.path.abspath(args.out)), exist_ok=True)
    # Consolidate WAL into the main file, then copy out a clean single file.
    conn = db.connect()
    conn.execute("PRAGMA wal_checkpoint(TRUNCATE)")
    conn.close()
    import shutil
    shutil.copyfile(os.environ["FFO_DB_PATH"], args.out)
    size = os.path.getsize(args.out)
    print(f"\nSeed DB written: {args.out} ({size/1e6:.1f} MB)")
    print("Bundle it with the build (build-desktop.yml already adds seed/ if present).")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
