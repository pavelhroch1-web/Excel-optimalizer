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


def _seed_db_path() -> str | None:
    """Bundled default DB used to seed a fresh install. Configurable via
    FFO_SEED_DB; otherwise looked up in the PyInstaller bundle or the repo at
    seed/fieldforce.db. Returns None when no seed is available (empty start)."""
    override = os.environ.get("FFO_SEED_DB")
    if override is not None:  # explicitly set — honor it; "" disables seeding
        return override if (override and os.path.exists(override)) else None
    candidates = []
    base = getattr(sys, "_MEIPASS", None)
    if base:
        candidates.append(os.path.join(base, "seed", "fieldforce.db"))
    here = os.path.dirname(os.path.abspath(__file__))
    candidates.append(os.path.join(os.path.dirname(here), "seed", "fieldforce.db"))
    return next((c for c in candidates if os.path.exists(c)), None)


def bootstrap_db() -> bool:
    """First-run seed: if there is no runtime DB yet AND a bundled seed DB is
    available, copy it into the data dir so the app is usable immediately after
    download. NEVER overwrites an existing runtime DB (so a later user import is
    preserved). Returns True only when a copy actually happened.

    This only changes how the runtime DB is *initialised* — every endpoint still
    reads exclusively from the runtime DB, and the import pipeline still fully
    rebuilds it from new exports."""
    target = os.environ.get("FFO_DB_PATH") or os.path.join(data_dir(), "fieldforce.db")
    seed = _seed_db_path()
    import shutil
    if os.path.exists(target):
        # Normally never overwrite an existing runtime DB (a user import wins).
        # EXCEPTION: a *broken/stale* DB left by an older build — e.g. missing a
        # core table (the "no such table: technicians" crash) or empty of POS —
        # is non-functional, so replace it from the bundled seed and keep the old
        # file as a timestamped backup (nothing is ever lost).
        if seed and _db_incomplete(target):
            import time
            bak = f"{target}.stale-{time.strftime('%Y%m%d-%H%M%S')}.bak"
            try:
                shutil.copyfile(target, bak)
            except OSError:
                pass
            tmp = target + ".seedtmp"
            shutil.copyfile(seed, tmp)
            os.replace(tmp, target)
            for ext in ("-wal", "-shm"):          # stale WAL/SHM would corrupt the new file
                try:
                    os.remove(target + ext)
                except OSError:
                    pass
            return True
        return False
    if not seed:
        return False
    tmp = target + ".seedtmp"
    shutil.copyfile(seed, tmp)
    os.replace(tmp, target)  # atomic: no half-copied DB on a crash mid-copy
    return True


def _db_incomplete(path: str) -> bool:
    """True if an existing runtime DB is broken/stale enough to replace from the
    seed: unreadable, missing a core table, or holding no POS at all."""
    try:
        c = sqlite3.connect(path)
        try:
            for tbl in ("technicians", "pos_master", "salesapp_visits"):
                if not c.execute("SELECT name FROM sqlite_master WHERE type='table' AND name=?",
                                 (tbl,)).fetchone():
                    return True
            return c.execute("SELECT COUNT(*) FROM pos_master").fetchone()[0] == 0
        finally:
            c.close()
    except Exception:  # noqa: BLE001 - unreadable/corrupt -> replace
        return True


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
    # timeout + busy_timeout: FastAPI runs sync endpoints in a threadpool, so
    # several requests hit SQLite at once (the dashboard alone fires ~8 parallel
    # calls, some of which write/recompute). Without a busy timeout a concurrent
    # write raises "database is locked" immediately -> HTTP 500. WAL lets readers
    # not block the writer; the timeout makes writers WAIT instead of failing.
    conn = sqlite3.connect(db_path(), timeout=30.0)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    conn.execute("PRAGMA journal_mode = WAL")
    conn.execute("PRAGMA busy_timeout = 30000")
    conn.execute("PRAGMA synchronous = NORMAL")
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
        scols = {r[1] for r in conn.execute("PRAGMA table_info(snapshots)")}
        if "kind" not in scols:
            conn.execute("ALTER TABLE snapshots ADD COLUMN kind TEXT NOT NULL DEFAULT 'state'")
        capcols = {r[1] for r in conn.execute("PRAGMA table_info(capacity_standard)")}
        if capcols and "productive_p90" not in capcols:
            conn.execute("ALTER TABLE capacity_standard ADD COLUMN productive_p90 REAL")
        ttcols = {r[1] for r in conn.execute("PRAGMA table_info(task_types)")}
        if ttcols and "category" not in ttcols:
            conn.execute("ALTER TABLE task_types ADD COLUMN category TEXT DEFAULT 'other'")
        techcols = {r[1] for r in conn.execute("PRAGMA table_info(technicians)")}
        if techcols and "excluded" not in techcols:
            conn.execute("ALTER TABLE technicians ADD COLUMN excluded INTEGER NOT NULL DEFAULT 0")
        # business_rules: the table's UNIQUE(code, scope, scope_value) does NOT
        # dedupe global rules because SQLite treats NULL scope_value as distinct,
        # so repeated seeding inserted duplicate rows (effective() masked it by
        # deduping on code). Collapse duplicates to one row per logical key and
        # add an expression-unique index (NULL == '') so it can't recur.
        brcols = {r[1] for r in conn.execute("PRAGMA table_info(business_rules)")}
        if brcols:
            conn.execute(
                "DELETE FROM business_rules WHERE id NOT IN "
                "(SELECT MIN(id) FROM business_rules "
                " GROUP BY code, scope, IFNULL(scope_value, ''))")
            conn.execute(
                "CREATE UNIQUE INDEX IF NOT EXISTS ux_business_rules_scope "
                "ON business_rules(code, scope, IFNULL(scope_value, ''))")
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
