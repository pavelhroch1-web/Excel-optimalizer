#!/usr/bin/env python3
"""Fail the build if the bundled seed DB's schema has drifted from the app's.

Single source of truth for the schema is backend/schema.sql (+ the idempotent
migrations in db.init_db). This check builds a fresh, empty canonical DB from
exactly that, then compares every table and column against seed/fieldforce.db.
If the seed is missing any table or column the current app expects, the seed is
stale and the build stops — regenerate it with tools/build_seed_db.py.

Exit codes: 0 = seed matches (or no seed present -> nothing to check); 1 = drift.

Usage:  python tools/verify_seed_db.py [path/to/seed.db]
"""
from __future__ import annotations

import os
import sqlite3
import sys
import tempfile

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(REPO, "backend"))


def _schema(conn: sqlite3.Connection) -> dict[str, set[str]]:
    """{table_name: {column, ...}} for every user table."""
    out: dict[str, set[str]] = {}
    tables = [r[0] for r in conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'")]
    for t in tables:
        out[t] = {r[1] for r in conn.execute(f"PRAGMA table_info('{t}')")}
    return out


def canonical_schema() -> dict[str, set[str]]:
    work = tempfile.mkdtemp(prefix="seed_verify_")
    os.environ["FFO_LOCAL"] = "1"
    os.environ["FFO_DATA_DIR"] = work
    os.environ["FFO_DB_PATH"] = os.path.join(work, "canonical.db")
    os.environ.pop("FFO_SEED_DB", None)
    import db
    db.init_db()  # schema.sql + migrations — the exact runtime schema
    conn = sqlite3.connect(os.environ["FFO_DB_PATH"])
    try:
        return _schema(conn)
    finally:
        conn.close()


def main() -> int:
    seed = sys.argv[1] if len(sys.argv) > 1 else os.path.join(REPO, "seed", "fieldforce.db")
    if not os.path.exists(seed):
        print(f"verify_seed_db: no seed at {seed} — build ships empty, nothing to verify.")
        return 0

    want = canonical_schema()
    conn = sqlite3.connect(seed)
    try:
        have = _schema(conn)
    finally:
        conn.close()

    problems: list[str] = []
    for table, cols in want.items():
        if table not in have:
            problems.append(f"  missing TABLE: {table}")
            continue
        missing = cols - have[table]
        if missing:
            problems.append(f"  table {table} missing COLUMNS: {sorted(missing)}")

    if problems:
        print("SEED DB SCHEMA DRIFT — the bundled seed is stale:\n" + "\n".join(problems))
        print("\nRegenerate it:  python tools/build_seed_db.py <exports>*.xlsx")
        return 1

    extra_t = set(have) - set(want)
    print(f"verify_seed_db: OK — seed matches current schema "
          f"({len(want)} tables checked"
          + (f", seed has {len(extra_t)} extra table(s), harmless" if extra_t else "") + ").")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
