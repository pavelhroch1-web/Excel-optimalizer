-- Field Force Optimizer - local SQLite datastore (desktop app).
-- SQLite is the source of truth. Excel is import/export only.
--
-- Design goals baked in here:
--   * HISTORY everywhere (SalesApp reality, POS master changes, snapshots).
--   * PUBLISHED plans are IMMUTABLE - triggers hard-block UPDATE/DELETE, so
--     once a Tour Plan is published the system can never rewrite it; it can
--     only append reality (SalesApp) and build reports on top.
--   * A published SNAPSHOT keeps the exact engine state as a blob (byte-
--     identical resume, the guarantee we already rely on) AND we also
--     materialise normalised rows (published_plans) for querying/reporting.
--
-- PRAGMAs are set in db.py (foreign_keys=ON, journal_mode=WAL).

-- ---------------------------------------------------------------------------
-- Reference / master data
-- ---------------------------------------------------------------------------

CREATE TABLE IF NOT EXISTS technicians (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    name        TEXT NOT NULL UNIQUE,
    area        TEXT,
    active      INTEGER NOT NULL DEFAULT 1,
    created_at  TEXT NOT NULL DEFAULT (datetime('now')),
    updated_at  TEXT NOT NULL DEFAULT (datetime('now'))
);

-- Current POS master (one row per POS). Change history is in pos_master_history.
CREATE TABLE IF NOT EXISTS pos_master (
    pos_id                TEXT PRIMARY KEY,
    name                  TEXT,
    street                TEXT,
    house_number          TEXT,
    city                  TEXT,
    area                  TEXT,
    pos_area              TEXT,
    category              TEXT,
    market                TEXT,
    classification        TEXT,
    terminal_type         TEXT,
    ppt                   REAL,
    gps_x                 REAL,
    gps_y                 REAL,
    technician            TEXT,             -- assigned technician (name)
    manager_override_type TEXT,             -- FORCE_INCLUDE / FORCE_EXCLUDE / NULL
    active                INTEGER NOT NULL DEFAULT 1,
    first_seen            TEXT NOT NULL DEFAULT (datetime('now')),
    last_seen             TEXT NOT NULL DEFAULT (datetime('now')),
    updated_at            TEXT NOT NULL DEFAULT (datetime('now'))
);
CREATE INDEX IF NOT EXISTS ix_pos_master_tech ON pos_master(technician);
CREATE INDEX IF NOT EXISTS ix_pos_master_active ON pos_master(active);

-- Slowly-changing history of POS master (audit of what changed and when).
CREATE TABLE IF NOT EXISTS pos_master_history (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    pos_id      TEXT NOT NULL,
    changed_at  TEXT NOT NULL DEFAULT (datetime('now')),
    field       TEXT NOT NULL,
    old_value   TEXT,
    new_value   TEXT,
    source      TEXT                        -- 'import' / 'manual' / 'engine'
);
CREATE INDEX IF NOT EXISTS ix_pos_hist_pos ON pos_master_history(pos_id);

-- POS that are closed (imported list). Excluded from planning; kept for audit.
CREATE TABLE IF NOT EXISTS closed_pos (
    pos_id      TEXT PRIMARY KEY,
    closed_on   TEXT,
    reason      TEXT,
    source      TEXT,
    created_at  TEXT NOT NULL DEFAULT (datetime('now'))
);

-- ---------------------------------------------------------------------------
-- SalesApp history (REALITY: what technicians actually visited)
-- ---------------------------------------------------------------------------

CREATE TABLE IF NOT EXISTS salesapp_imports (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    filename    TEXT,
    sha256      TEXT,
    row_count   INTEGER,
    imported_at TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS salesapp_visits (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    uid           TEXT UNIQUE,              -- SalesApp UID; dedup key
    pos_id        TEXT,
    technician    TEXT,
    executor      TEXT,
    visit_date    TEXT,
    purpose       TEXT,
    los_activity  TEXT,
    lot_activity  TEXT,
    gps_x         REAL,
    gps_y         REAL,
    import_id     INTEGER REFERENCES salesapp_imports(id),
    created_at    TEXT NOT NULL DEFAULT (datetime('now'))
);
CREATE INDEX IF NOT EXISTS ix_visits_pos  ON salesapp_visits(pos_id);
CREATE INDEX IF NOT EXISTS ix_visits_date ON salesapp_visits(visit_date);
CREATE INDEX IF NOT EXISTS ix_visits_tech ON salesapp_visits(technician);

-- ---------------------------------------------------------------------------
-- Campaigns (Activity Plan)
-- ---------------------------------------------------------------------------

CREATE TABLE IF NOT EXISTS campaigns (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    kind         TEXT,                      -- terminal / market / mandatory ...
    name         TEXT,
    year         INTEGER,
    start_week   INTEGER,
    end_week     INTEGER,
    priority     INTEGER,
    override_gap INTEGER,
    estimate     TEXT,                      -- ODHAD (demand), kept as-is
    active       INTEGER NOT NULL DEFAULT 1,
    created_at   TEXT NOT NULL DEFAULT (datetime('now')),
    updated_at   TEXT NOT NULL DEFAULT (datetime('now'))
);

-- ---------------------------------------------------------------------------
-- Snapshots: immutable full engine state at publish (source of truth to
-- resume from). state_blob = the whole workbook state (xlsx bytes) so the
-- engine resumes byte-identically; normalised published rows are below.
-- ---------------------------------------------------------------------------

CREATE TABLE IF NOT EXISTS snapshots (
    id             TEXT PRIMARY KEY,        -- 'v0001', 'v0002', ...
    created_at     TEXT NOT NULL DEFAULT (datetime('now')),
    message        TEXT,
    published_week INTEGER,
    published_by   TEXT,
    engine_version TEXT,
    source_files   TEXT,                    -- JSON provenance
    state_blob     BLOB NOT NULL
);

-- The single mutable working draft (id always 'current'). Rebuilt on upload,
-- mutated by edits, frozen into a snapshot on publish.
CREATE TABLE IF NOT EXISTS drafts (
    id          TEXT PRIMARY KEY DEFAULT 'current',
    updated_at  TEXT NOT NULL DEFAULT (datetime('now')),
    meta        TEXT,                        -- JSON provenance
    state_blob  BLOB NOT NULL
);

-- ---------------------------------------------------------------------------
-- Tour Plan rows - normalised, queryable
-- ---------------------------------------------------------------------------

-- Published (immutable) plan rows, tied to the snapshot that froze them.
CREATE TABLE IF NOT EXISTS published_plans (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    snapshot_id  TEXT NOT NULL REFERENCES snapshots(id),
    year         INTEGER,
    week         INTEGER NOT NULL,
    plan_date    TEXT,
    day          TEXT,
    technician   TEXT,
    pos_id       TEXT,
    category     TEXT,
    name         TEXT,
    street       TEXT,
    house_number TEXT,
    city         TEXT,
    area         TEXT,
    pos_area     TEXT,
    ppt          REAL,
    reason       TEXT,
    day_group    INTEGER,
    created_at   TEXT NOT NULL DEFAULT (datetime('now'))
);
CREATE INDEX IF NOT EXISTS ix_pub_week ON published_plans(year, week);
CREATE INDEX IF NOT EXISTS ix_pub_tech ON published_plans(technician);
CREATE INDEX IF NOT EXISTS ix_pub_pos  ON published_plans(pos_id);

-- Current draft plan rows (mutable; replaced on regenerate).
CREATE TABLE IF NOT EXISTS draft_plans (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    year         INTEGER,
    week         INTEGER NOT NULL,
    plan_date    TEXT,
    day          TEXT,
    technician   TEXT,
    pos_id       TEXT,
    category     TEXT,
    name         TEXT,
    street       TEXT,
    house_number TEXT,
    city         TEXT,
    area         TEXT,
    pos_area     TEXT,
    ppt          REAL,
    reason       TEXT,
    day_group    INTEGER
);
CREATE INDEX IF NOT EXISTS ix_draft_week ON draft_plans(year, week);

-- Per-week lifecycle: once Published, that week is locked.
CREATE TABLE IF NOT EXISTS plan_lifecycle (
    year        INTEGER NOT NULL,
    week        INTEGER NOT NULL,
    status      TEXT NOT NULL,               -- Draft / Published
    snapshot_id TEXT REFERENCES snapshots(id),
    updated_at  TEXT NOT NULL DEFAULT (datetime('now')),
    PRIMARY KEY (year, week)
);

-- ---------------------------------------------------------------------------
-- Reports (generated artefacts / scorecards)
-- ---------------------------------------------------------------------------

CREATE TABLE IF NOT EXISTS reports (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    kind        TEXT NOT NULL,               -- 'coverage' / 'plan_vs_reality' / ...
    year        INTEGER,
    week        INTEGER,
    params      TEXT,                         -- JSON
    data        TEXT,                         -- JSON payload (small reports)
    blob        BLOB,                         -- optional exported file (xlsx)
    created_at  TEXT NOT NULL DEFAULT (datetime('now'))
);
CREATE INDEX IF NOT EXISTS ix_reports_kind ON reports(kind, year, week);

-- Free-form config / CONTROL key-values that don't warrant their own table.
CREATE TABLE IF NOT EXISTS config (
    key   TEXT PRIMARY KEY,
    value TEXT
);

-- ---------------------------------------------------------------------------
-- IMMUTABILITY: published plans & snapshots can never be modified in place.
-- ---------------------------------------------------------------------------

CREATE TRIGGER IF NOT EXISTS published_plans_no_update
BEFORE UPDATE ON published_plans
BEGIN SELECT RAISE(ABORT, 'published plan is immutable'); END;

CREATE TRIGGER IF NOT EXISTS published_plans_no_delete
BEFORE DELETE ON published_plans
BEGIN SELECT RAISE(ABORT, 'published plan is immutable'); END;

CREATE TRIGGER IF NOT EXISTS snapshots_no_update
BEFORE UPDATE ON snapshots
BEGIN SELECT RAISE(ABORT, 'snapshot is immutable'); END;

CREATE TRIGGER IF NOT EXISTS snapshots_no_delete
BEFORE DELETE ON snapshots
BEGIN SELECT RAISE(ABORT, 'snapshot is immutable'); END;
