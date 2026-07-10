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

-- People in the field. role distinguishes TECHNIK / OZ / OTHER so KPIs and
-- capacity can be tracked per role (the app manages the whole Field Force,
-- not just technicians). capacity_per_week feeds Planning; extra flexible
-- fields go in attributes (JSON) so new per-person data needs no migration.
CREATE TABLE IF NOT EXISTS technicians (
    id                INTEGER PRIMARY KEY AUTOINCREMENT,
    name              TEXT NOT NULL UNIQUE,
    role              TEXT NOT NULL DEFAULT 'TECHNIK',   -- TECHNIK | OZ | OTHER
    region            TEXT,
    area              TEXT,
    capacity_per_week INTEGER,
    active            INTEGER NOT NULL DEFAULT 1,
    attributes        TEXT,                              -- JSON escape hatch
    created_at        TEXT NOT NULL DEFAULT (datetime('now')),
    updated_at        TEXT NOT NULL DEFAULT (datetime('now'))
);

-- Region catalog for KPI aggregation (Dashboard/Reporting per region).
CREATE TABLE IF NOT EXISTS regions (
    code    TEXT PRIMARY KEY,
    name    TEXT,
    active  INTEGER NOT NULL DEFAULT 1
);

-- Current POS master (one row per POS). Change history is in pos_master_history.
CREATE TABLE IF NOT EXISTS pos_master (
    pos_id                TEXT PRIMARY KEY,
    terminal_id           TEXT,             -- links SalesApp Store UID -> POS
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
CREATE INDEX IF NOT EXISTS ix_pos_master_terminal ON pos_master(terminal_id);

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
    pos_id        TEXT,                     -- linked POS (best-effort; engine is authoritative)
    store_uid     TEXT,                     -- SalesApp Store UID (raw)
    store_name    TEXT,
    store_address TEXT,
    region        TEXT,                     -- Agency region
    technician    TEXT,                     -- Executor
    executor_uid  TEXT,
    visitor_role  TEXT,                     -- TECHNIK | OZ (derived from executor)
    visit_date    TEXT,
    started_at    TEXT,                     -- for VISIT ORDER within a day (route seq)
    finished_at   TEXT,
    real_duration REAL,
    seq           INTEGER,                  -- computed later: order within tech-day
    purpose       TEXT,                     -- ; -joined matched purpose columns
    los_activity  TEXT,
    lot_activity  TEXT,
    gps_x         REAL,                     -- usually NULL; join pos_master for GPS
    gps_y         REAL,
    import_id     INTEGER REFERENCES salesapp_imports(id),
    created_at    TEXT NOT NULL DEFAULT (datetime('now'))
);
CREATE INDEX IF NOT EXISTS ix_visits_pos   ON salesapp_visits(pos_id);
CREATE INDEX IF NOT EXISTS ix_visits_store ON salesapp_visits(store_uid);
CREATE INDEX IF NOT EXISTS ix_visits_date  ON salesapp_visits(visit_date);
CREATE INDEX IF NOT EXISTS ix_visits_tech  ON salesapp_visits(technician);

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
    objective_id INTEGER REFERENCES objectives(id),  -- campaign -> business objective
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
    day_group    INTEGER,               -- geo cluster within the day
    day_seq      INTEGER,               -- planned order within the day (for route km; filled later)
    gps_x        REAL,                  -- snapshotted with the plan (POS GPS may change later)
    gps_y        REAL,
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
    day_group    INTEGER,
    day_seq      INTEGER,
    gps_x        REAL,
    gps_y        REAL
);
CREATE INDEX IF NOT EXISTS ix_draft_week ON draft_plans(year, week);

-- Route analysis (Phase 2/3): one row per technician-day, for plan or reality.
-- Empty for now - the data model is ready so km/efficiency can be computed
-- later from published_plans (planned) and salesapp_visits (reality) without
-- any schema change. No maps/routing yet.
CREATE TABLE IF NOT EXISTS route_metrics (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    source        TEXT NOT NULL,            -- 'plan' | 'reality'
    snapshot_id   TEXT REFERENCES snapshots(id),
    technician    TEXT,
    year          INTEGER,
    week          INTEGER,
    visit_date    TEXT,
    stop_count    INTEGER,
    planned_km    REAL,
    optimal_km    REAL,
    actual_km     REAL,
    efficiency    REAL,                     -- optimal/actual
    computed_at   TEXT NOT NULL DEFAULT (datetime('now'))
);
CREATE INDEX IF NOT EXISTS ix_route_tech ON route_metrics(technician, year, week);

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
-- BUSINESS OBJECTIVES (Field Brain plans GOALS, not just visits)
--
-- A visit can satisfy several objectives at once (Cadence, Sportka, Losy,
-- Vánoce, Merchandising, Compliance, Audit, ...). Modelled as a catalog +
-- many-to-many links, so:
--   * adding a new objective = INSERT a row in `objectives` (no schema change)
--   * a planned stop can target several objectives (plan_stop_objectives)
--   * a real visit can fulfil several objectives (visit_objectives)
--   * "POS complete this week" = every due objective (pos_objectives) is
--     fulfilled -> a further visit has no business value. Computed by query
--     from these tables; no dedicated table needed.
-- ---------------------------------------------------------------------------

CREATE TABLE IF NOT EXISTS objectives (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    code        TEXT NOT NULL UNIQUE,        -- CADENCE / SPORTKA / LOSY / VANOCE / MERCH / COMPLIANCE / AUDIT ...
    name        TEXT,
    category    TEXT,
    description TEXT,
    params      TEXT,                        -- JSON (cadence weeks, campaign link, weights...)
    active      INTEGER NOT NULL DEFAULT 1,
    created_at  TEXT NOT NULL DEFAULT (datetime('now'))
);

-- What objectives are DUE at a POS, over a period. Field Brain reads this as
-- demand; flexible period (week_from/week_to) + priority + JSON params.
CREATE TABLE IF NOT EXISTS pos_objectives (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    pos_id       TEXT NOT NULL,
    objective_id INTEGER NOT NULL REFERENCES objectives(id),
    year         INTEGER,
    week_from    INTEGER,
    week_to      INTEGER,
    priority     INTEGER,
    status       TEXT DEFAULT 'due',         -- due | done | waived
    source       TEXT,                        -- cadence | campaign | manual
    params       TEXT,
    created_at   TEXT NOT NULL DEFAULT (datetime('now'))
);
CREATE INDEX IF NOT EXISTS ix_posobj_pos ON pos_objectives(pos_id);
CREATE INDEX IF NOT EXISTS ix_posobj_obj ON pos_objectives(objective_id);

-- Objectives a PLANNED stop is meant to fulfil (polymorphic: draft/published).
CREATE TABLE IF NOT EXISTS plan_stop_objectives (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    plan_kind    TEXT NOT NULL,               -- 'published' | 'draft'
    plan_id      INTEGER NOT NULL,            -- published_plans.id or draft_plans.id
    objective_id INTEGER NOT NULL REFERENCES objectives(id)
);
CREATE INDEX IF NOT EXISTS ix_planobj ON plan_stop_objectives(plan_kind, plan_id);

-- Objectives a REAL visit fulfilled (reality from SalesApp).
CREATE TABLE IF NOT EXISTS visit_objectives (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    visit_id     INTEGER NOT NULL REFERENCES salesapp_visits(id),
    objective_id INTEGER NOT NULL REFERENCES objectives(id),
    fulfilled    INTEGER NOT NULL DEFAULT 1,
    created_at   TEXT NOT NULL DEFAULT (datetime('now'))
);
CREATE INDEX IF NOT EXISTS ix_visitobj ON visit_objectives(visit_id);

-- ---------------------------------------------------------------------------
-- GENERIC time-series METRICS: one table for every KPI/scorecard over time,
-- for any entity (technician / OZ / region / pos / campaign / network /
-- field_brain). Dashboards & Reporting write and read here, so new KPIs need
-- no new tables - just a new metric_key.
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS metrics (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    entity_type TEXT NOT NULL,               -- technician | oz | region | pos | campaign | network | field_brain
    entity_id   TEXT,                        -- name/id of the entity (NULL for network-wide)
    metric_key  TEXT NOT NULL,               -- e.g. plan_fulfilment_pct, km_actual, risk_score
    year        INTEGER,
    week        INTEGER,
    period      TEXT,                         -- optional label: 'W29-2026', 'campaign:VANOCE'
    value_num   REAL,
    value_text  TEXT,
    computed_at TEXT NOT NULL DEFAULT (datetime('now'))
);
CREATE INDEX IF NOT EXISTS ix_metrics_e ON metrics(entity_type, entity_id, metric_key);
CREATE INDEX IF NOT EXISTS ix_metrics_p ON metrics(year, week);

-- ---------------------------------------------------------------------------
-- GENERIC events / audit log: any module can append without a schema change.
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS events (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    ts          TEXT NOT NULL DEFAULT (datetime('now')),
    kind        TEXT NOT NULL,               -- import | publish | recompute | override ...
    entity_type TEXT,
    entity_id   TEXT,
    payload     TEXT                          -- JSON
);
CREATE INDEX IF NOT EXISTS ix_events_kind ON events(kind);

-- ---------------------------------------------------------------------------
-- BUSINESS RULES: planning logic as DATA, not hardcoded Python.
--
-- Every planning rule (cadence, min gap between visits, campaign priority,
-- hold-back, OZ-coverage skip, max visits/week, neglected boost, GPS extras...)
-- is a row here: toggle `enabled`, edit `params` (JSON), or add a scoped
-- override - all without code changes. The Planning Engine only READS these
-- (the db_state layer translates enabled rules into the config the engine
-- already consumes; the engine's algorithm is unchanged).
--
-- Scoped overrides: several rows may share a `code` with different scope
-- (global < market < category < technician < pos); the loader merges them,
-- most-specific winning. Adding a rule = INSERT a row (no schema change).
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS business_rules (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    code        TEXT NOT NULL,               -- MIN_GAP, CADENCE, HOLDBACK, MAX_VISITS_WEEK ...
    name        TEXT,
    description TEXT,
    category    TEXT,                          -- cadence | spacing | campaign | capacity | holdback | coverage
    enabled     INTEGER NOT NULL DEFAULT 1,
    params      TEXT,                          -- JSON parameters
    scope       TEXT NOT NULL DEFAULT 'global',-- global | market | category | technician | pos
    scope_value TEXT,                          -- e.g. a market code / category / technician name
    priority    INTEGER NOT NULL DEFAULT 100,
    valid_from  TEXT,
    valid_to    TEXT,
    updated_at  TEXT NOT NULL DEFAULT (datetime('now')),
    UNIQUE (code, scope, scope_value)
);
CREATE INDEX IF NOT EXISTS ix_rules_code ON business_rules(code, enabled);

-- Seed the rule catalog with defaults matching today's engine behaviour.
-- (idempotent; params are editable from the app / API afterwards.)
INSERT OR IGNORE INTO business_rules (code, name, category, enabled, params) VALUES
    ('CADENCE',          'Pravidelná kadence (GECO/CORN/MANDATORY)', 'cadence', 1,
        '{"rules":[{"code":"GECO","match":"category","value":"1GECO","every_weeks":5},{"code":"CORN","match":"market","value":"CORN","every_weeks":4},{"code":"MANDATORY_9PODNIK","once_per_campaign":true}],"dedup_by":"address"}'),
    ('MIN_GAP',          'Minimální rozestup mezi návštěvami', 'spacing', 1, '{"weeks":8}'),
    ('NEGLECTED_AFTER',  'Bonus za dlouho nenavštívené POS', 'spacing', 1, '{"weeks":26}'),
    ('HOLDBACK',         'Smart hold-back před kampaní', 'holdback', 1, '{"lookahead_weeks":3,"tolerance_a":1,"tolerance_other":3}'),
    ('MAX_VISITS_WEEK',  'Maximální počet návštěv na technika/den', 'capacity', 1, '{"per_day":8}'),
    ('CAMPAIGN_PRIORITY','Priorita kampaní', 'campaign', 1, '{"source":"campaigns"}'),
    ('GPS_EXTRA',        'Extra návštěvy podle GPS clusteru', 'capacity', 0, '{"max_extra_visits":5}'),
    ('OZ_COVERAGE',      'Nenaplánovat POS pokryté nedávno OZ', 'coverage', 0, '{"skip_if_oz_within_weeks":4}');

-- ---------------------------------------------------------------------------
-- SETTINGS PLATFORM: everything configurable from the app, not the code.
--
-- Split of concerns so a generic admin UI can render any setting and adding a
-- new one is data-only:
--   * setting_definitions = the CATALOG (namespace, key, type, default, range/
--     options, UI group) -> drives the admin UI automatically.
--   * settings            = actual VALUES / overrides (with the same scope
--     mechanism as business_rules). Effective value = override else default.
--   * saved_views         = named dashboard/report/map views ("uložené pohledy").
--
-- Namespaces: planner, optimization, dashboard, report, map, scoring, general.
-- The Planning Engine / dashboards / reports / maps only READ effective
-- settings; the algorithm stays generic. New KPI/metric/weight = a new
-- definition row (+ optional value), never a code or schema change.
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS setting_definitions (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    namespace     TEXT NOT NULL,             -- planner | optimization | dashboard | report | map | scoring | general
    key           TEXT NOT NULL,
    label         TEXT,
    description   TEXT,
    value_type    TEXT NOT NULL DEFAULT 'string', -- number | bool | string | enum | json
    default_value TEXT,
    min_value     REAL,
    max_value     REAL,
    options       TEXT,                        -- JSON array for enum
    ui_group      TEXT,
    sort_order    INTEGER NOT NULL DEFAULT 100,
    active        INTEGER NOT NULL DEFAULT 1,
    UNIQUE (namespace, key)
);

CREATE TABLE IF NOT EXISTS settings (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    namespace   TEXT NOT NULL,
    key         TEXT NOT NULL,
    value       TEXT,                          -- stored as text; typed per definition
    scope       TEXT NOT NULL DEFAULT 'global',
    scope_value TEXT,
    updated_at  TEXT NOT NULL DEFAULT (datetime('now')),
    UNIQUE (namespace, key, scope, scope_value)
);
CREATE INDEX IF NOT EXISTS ix_settings_ns ON settings(namespace);

CREATE TABLE IF NOT EXISTS saved_views (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    namespace   TEXT NOT NULL,                 -- dashboard | report | map
    name        TEXT NOT NULL,
    definition  TEXT,                          -- JSON (widgets/columns/layers/filters)
    is_default  INTEGER NOT NULL DEFAULT 0,
    created_at  TEXT NOT NULL DEFAULT (datetime('now')),
    updated_at  TEXT NOT NULL DEFAULT (datetime('now')),
    UNIQUE (namespace, name)
);

-- Seed setting definitions (defaults match current behaviour; edit anytime).
INSERT OR IGNORE INTO setting_definitions
    (namespace, key, label, value_type, default_value, min_value, max_value, options, ui_group, sort_order) VALUES
    -- planner
    ('planner','max_visits_per_day','Max. návštěv/den','number','8',1,20,NULL,'Kapacita',10),
    ('planner','work_hours_per_day','Pracovní doba (h)','number','8',1,12,NULL,'Kapacita',20),
    ('planner','max_km_per_day','Max. km/den','number','250',0,1000,NULL,'Kapacita',30),
    ('planner','planning_horizon_weeks','Plánovací horizont (týdnů)','number','5',1,12,NULL,'Horizont',40),
    ('planner','default_mode','Výchozí režim','enum','vyvazeny',NULL,NULL,'["dojezd","kampan","vyvazeny","cela_sit"]','Režim',50),
    ('planner','workday_start','Začátek dne','string','08:00',NULL,NULL,NULL,'Kapacita',60),
    ('planner','workday_end','Konec dne','string','16:30',NULL,NULL,NULL,'Kapacita',70),
    -- optimization (weights that feed scoring)
    ('optimization','weight_campaign','Váha kampaní','number','1.0',0,10,NULL,'Váhy',10),
    ('optimization','weight_cadence','Váha cadence','number','1.0',0,10,NULL,'Váhy',20),
    ('optimization','weight_neglected','Váha zanedbání','number','1.0',0,10,NULL,'Váhy',30),
    ('optimization','weight_distance','Váha vzdálenosti','number','1.0',0,10,NULL,'Váhy',40),
    ('optimization','weight_workload','Váha vytížení','number','1.0',0,10,NULL,'Váhy',50),
    ('optimization','weight_ppt','Váha PPT','number','1.0',0,10,NULL,'Váhy',60),
    ('optimization','objective_priority','Priorita cílů','json','["COMPLIANCE","CADENCE","SPORTKA","LOSY","VANOCE","MERCH","AUDIT"]',NULL,NULL,NULL,'Cíle',70),
    -- scoring (POS + technician score building blocks)
    ('scoring','core_bonus','Bonus CORE','number','100000000',0,NULL,NULL,'POS skóre',10),
    ('scoring','category_a_bonus','Bonus kategorie A','number','10000000',0,NULL,NULL,'POS skóre',20),
    ('scoring','ppt_weight','Váha PPT ve skóre','number','1',0,NULL,NULL,'POS skóre',30),
    ('scoring','neglected_bonus','Bonus za zanedbání','number','50000',0,NULL,NULL,'POS skóre',40),
    ('scoring','min_gap_penalty','Penalizace pod min. rozestup','number','-1000000',NULL,0,NULL,'POS skóre',50),
    ('scoring','technician_score','Vzorec skóre technika (JSON)','json','{"visits":0.4,"km_efficiency":0.3,"campaign_fulfilment":0.3}',NULL,NULL,NULL,'Technik skóre',60),
    -- dashboard
    ('dashboard','default_kpis','Výchozí KPI','json','["plan_fulfilment_pct","visits","km","route_efficiency","campaign_status"]',NULL,NULL,NULL,'KPI',10),
    ('dashboard','refresh_seconds','Obnovení (s)','number','0',0,3600,NULL,'Obecné',20),
    ('dashboard','chart_theme','Téma grafů','enum','auto',NULL,NULL,'["auto","light","dark"]','Vzhled',30),
    -- report
    ('report','default_export_format','Výchozí formát exportu','enum','xlsx',NULL,NULL,'["xlsx","csv","pdf"]','Export',10),
    ('report','sections','Sekce reportu','json','["technicians","oz","pos","campaigns","regions","performance"]',NULL,NULL,NULL,'Obsah',20),
    -- map
    ('map','heatmap_enabled','Heatmapa zapnutá','bool','true',NULL,NULL,NULL,'Vrstvy',10),
    ('map','layers','Vrstvy mapy','json','["planned_route","actual_route","pos","heatmap"]',NULL,NULL,NULL,'Vrstvy',20),
    ('map','color_scheme','Barevné schéma','enum','viridis',NULL,NULL,'["viridis","turbo","cividis"]','Vzhled',30),
    ('map','default_filters','Výchozí filtry','json','{"role":"TECHNIK"}',NULL,NULL,NULL,'Filtry',40);

-- Seed the default business objectives (idempotent; add more anytime).
INSERT OR IGNORE INTO objectives (code, name, category) VALUES
    ('CADENCE',    'Pravidelná návštěva (cadence)', 'cadence'),
    ('SPORTKA',    'Sportka',                        'campaign'),
    ('LOSY',       'Stírací losy',                   'campaign'),
    ('VANOCE',     'Vánoční kampaň',                 'campaign'),
    ('MERCH',      'Merchandising',                  'merchandising'),
    ('COMPLIANCE', 'Compliance',                     'compliance'),
    ('AUDIT',      'Audit',                          'audit');

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
