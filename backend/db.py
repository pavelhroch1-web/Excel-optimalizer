"""SQLite datastore for the local desktop app - the single source of truth.

One file (fieldforce.db) in the user's data dir. Excel is import/export only;
everything the app works over lives here. The engine still runs on its
in-memory 'state' (sheet-shaped dicts); store.py bridges that to SQLite
(snapshot/draft state kept as a blob for byte-identical resume, plus
normalised rows for querying and reporting).

Data dir (override with FFO_DATA_DIR):
  Windows -> %LOCALAPPDATA%\\FieldForceOptimizer
  else    -> ~/.fieldforce
"""
from __future__ import annotations

import os
import sqlite3
import sys

_SCHEMA = os.path.join(os.path.dirname(os.path.abspath(__file__)), "schema.sql")


def data_dir() -> str:
    override = os.environ.get("FFO_DATA_DIR")
    if override:
        d = override
    elif getattr(sys, "frozen", False):
        # Portable .exe: keep the database next to the executable, so the whole
        # app (exe + FieldForceData/) can live in one folder / on a USB stick
        # and needs no installation. Falls back to LOCALAPPDATA if not writable.
        exe_dir = os.path.dirname(sys.executable)
        d = os.path.join(exe_dir, "FieldForceData")
        try:
            os.makedirs(d, exist_ok=True)
            testfile = os.path.join(d, ".write_test")
            with open(testfile, "w") as f:
                f.write("x")
            os.remove(testfile)
        except OSError:
            base = os.environ.get("LOCALAPPDATA") or os.path.expanduser("~")
            d = os.path.join(base, "FieldForceOptimizer")
    elif os.name == "nt":
        base = os.environ.get("LOCALAPPDATA") or os.path.expanduser("~")
        d = os.path.join(base, "FieldForceOptimizer")
    else:
        d = os.path.join(os.path.expanduser("~"), ".fieldforce")
    os.makedirs(d, exist_ok=True)
    return d


def db_path() -> str:
    return os.environ.get("FFO_DB_PATH") or os.path.join(data_dir(), "fieldforce.db")


def _schema_sql() -> str:
    # In a PyInstaller bundle schema.sql sits next to this module in _MEIPASS.
    path = _SCHEMA
    if not os.path.exists(path):
        import sys
        base = getattr(sys, "_MEIPASS", os.path.dirname(os.path.abspath(__file__)))
        path = os.path.join(base, "schema.sql")
    with open(path, encoding="utf-8") as f:
        return f.read()


def connect() -> sqlite3.Connection:
    conn = sqlite3.connect(db_path())
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    conn.execute("PRAGMA journal_mode = WAL")
    return conn


def init_db() -> None:
    """Create tables/triggers if missing (idempotent)."""
    conn = connect()
    try:
        conn.executescript(_schema_sql())
        # Lightweight additive migrations (CREATE TABLE IF NOT EXISTS can't add
        # a column to a table that already exists on an older DB).
        cols = {r[1] for r in conn.execute("PRAGMA table_info(cadence_overrides)")}
        if "priority" not in cols:
            conn.execute("ALTER TABLE cadence_overrides ADD COLUMN priority INTEGER")
        mcols = {r[1] for r in conn.execute("PRAGMA table_info(metrics)")}
        for col, decl in (("period_type", "TEXT"), ("period_key", "TEXT"),
                          ("dims", "TEXT"), ("source_kind", "TEXT"),
                          ("source_id", "INTEGER")):
            if col not in mcols:
                conn.execute(f"ALTER TABLE metrics ADD COLUMN {col} {decl}")
        conn.commit()
    finally:
        conn.close()


def get(query: str, params: tuple = ()) -> list[sqlite3.Row]:
    conn = connect()
    try:
        return conn.execute(query, params).fetchall()
    finally:
        conn.close()


def run(query: str, params: tuple = ()) -> None:
    conn = connect()
    try:
        conn.execute(query, params)
        conn.commit()
    finally:
        conn.close()
