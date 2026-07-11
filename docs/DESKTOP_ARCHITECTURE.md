# Field Force Optimizer — Local Desktop Architecture (production direction)

**Status: this is the production architecture.** Render and GitHub Actions are
no longer the production runtime — they were only workarounds for a 512 MB
host. The product is a **portable desktop application** that runs entirely on
the user's PC, uses its RAM, and stores everything in **SQLite**. Excel is
**import/export only**, never the working database.

Confirmed constraints:
- Portable `.exe`, no installation (verified: the company PC runs portable
  apps like Notepad++ Portable).
- Fully local: no mandatory cloud, no hosting, no server RAM limit.
- SQLite is the single source of truth.
- Keep the current UI and the Python engine — reuse, don't rewrite.

## Target architecture

```
  Web UI (current HTML/JS; React later if desired)
        |
  FastAPI on 127.0.0.1  (bundled, started by the app)
        |
  Planning Engine · Field Brain · Advisor · Reporting   (unchanged Python)
        |
  SQLite  (fieldforce.db)                Excel = import/export only
```

The app is one Python process: `desktop_app.py` starts FastAPI on a random
localhost port in a background thread and shows the existing UI in a native
window via **pywebview** (Windows uses the built-in Edge WebView2 — nothing
heavy bundled). Same origin for UI + API, so auth is bypassed (single user,
localhost).

### Why pywebview (not Electron/Tauri)
The engine and Field Brain are Python. pywebview lets Python own the whole
app — no Node sidecar, no bundled Chromium. If a full React SPA is wanted
later, we can move to Tauri; the FastAPI boundary stays the same either way.

## Data: SQLite (`backend/schema.sql`)

One file, `fieldforce.db`, in **`FieldForceData/` next to the `.exe`** (truly
portable; falls back to `%LOCALAPPDATA%` if that location is read-only).
Override with `FFO_DATA_DIR`.

Tables (history-first, designed for the whole roadmap):

| Table | Purpose |
|---|---|
| `technicians` | field team |
| `pos_master` (+ `pos_master_history`) | current POS + audit of changes |
| `closed_pos` | imported closed-POS list, excluded from planning |
| `salesapp_imports`, `salesapp_visits` | SalesApp reality, deduped by UID |
| `campaigns` | Activity Plan |
| `snapshots` | **immutable** full engine state (blob) at each publish |
| `drafts` | the single mutable working draft (state blob) |
| `published_plans` | **immutable** normalised published Tour Plan rows |
| `draft_plans` | current draft Tour Plan rows (mutable) |
| `plan_lifecycle` | per-week Draft/Published lock |
| `reports` | generated scorecards / exports |
| `config` | CONTROL key/values |

**Immutability is enforced in the database**: triggers hard-block
`UPDATE`/`DELETE` on `snapshots` and `published_plans`. Once a Tour Plan is
published it can never be rewritten — the system may only append reality
(SalesApp) and build reports over the plan.

### How the engine bridges to SQLite
The engine keeps working on its in-memory `state` (sheet-shaped dicts). A
snapshot/draft is stored as the full workbook state (xlsx bytes) in a blob, so
the engine **resumes byte-identically** — the guarantee we already rely on. On
publish we additionally materialise normalised rows into `published_plans` and
lock the weeks, so history and future dashboards query SQL, not xlsx.

## Excel = import/export only
- **Import:** POS Master, SalesApp, Activity Plan (openpyxl → SQLite).
- **Export:** Tour Plan, reports (SQLite → xlsx).
Nothing "works" inside a spreadsheet anymore.

## Build / distribution
- `desktop_client/build_desktop_exe.bat` → PyInstaller `--onefile` →
  `dist/FieldForceOptimizer.exe` (portable). Bundles `web/`, `schema.sql`, and
  the scaffold snapshot.
- Later, optionally an Inno Setup installer for a nicer first run.

### Planning Engine reads config from the DB (db_state)
`db_state.configure(state, mode, start_week, length)` maps the enabled
`business_rules` + `settings` onto the exact CONTROL keys the engine already
reads (STANDARD_VISIT_GAP, NEGLECTED_AFTER_WEEKS, HOLDBACK_*, TARGET_VISITS_*,
GPS_EXTRA_*, CAMPAIGN_*) and applies the strategy mode (dojezd / kampan /
vyvazeny / cela_sit). The importer syncs those rule params from the imported
CONTROL, so the DB is the source of truth. **Regression-verified: with default
config the plan is byte-identical to the pre-DB baseline (5117 rows); modes
diverge (dojezd 8019 vs kampan 7181).** The engine's algorithm is untouched —
editing a rule/setting changes planning with no code change.

## Platform vision (north star) — the full lifecycle

Not just a route generator: a decision & control platform for planning,
publishing, tracking and evaluating field work. Lifecycle:

**Import → Generate scenario → Check → Publish TourPlan → Track reality →
Evaluate → Re-plan.**

- **Published TourPlan = the active plan / source of truth** until a new one is
  generated and published. Immutable (DB triggers). New SalesApp only writes
  reality against it, never edits it.
- **Automatic import**: drop an Excel (or a watched folder); the system detects
  the file type (POS Master / SalesApp / Activity Plan), imports it, and
  recomputes the affected metrics. No manual mapping.
- **Automatic planner**: set capacity (35/40/45 POS per technician-week); the
  config-driven engine selects the best POS by all rules (PPT, cadence, CORE,
  campaigns, geography, history, hold-back, blacklist) and splits them across
  technicians and days. The manager reviews and handles exceptions only.
- **Predictions & simulations**: POS served, weeks to cover the network, effect
  of capacity/technician-count changes, when capacity runs out, when POS start
  falling out of cadence, campaign impact (e.g. Vánoce priority shifts).
- **Plan vs. reality (from SalesApp)**: which POS were actually visited, when,
  in the right cadence interval, time since last visit, next due; time spent per
  POS, visit order, driven km, travel time, on-POS time, extra visits, deviations
  from the published plan.
- **Long-term history** (months/years): per technician and per POS, trends over
  time, period comparison. Not just current state.
- **Automatic anomaly alerts** — the system surfaces what deserves attention,
  the manager doesn't hunt for it: a technician driving far fewer km than peers,
  unusually long on-POS time, many extra visits, chronically under plan, POS
  repeatedly unserved, behaviour that differs markedly from other technicians.
- **Real route map**: visit order is known (SalesApp start/finish times), so the
  actual driven route is drawn on a map (OSM + Leaflet; OSRM/GraphHopper later),
  with km and travel time.
- **Vacation-aware catch-up**: a technician with 0 visits in a period (vacation)
  leaves their POS un-served → those POS accrue neglect and the engine promotes
  them as priority catch-up in the next plan (already the engine's neglect
  behaviour; will be made explicit).

### The data model already supports this (additively)
- reality + order + times: `salesapp_visits` (started_at/finished_at, pos_id,
  technician, purpose); linkage Store UID → terminal_id → posId.
- route/km/efficiency: `route_metrics` (per technician-day; plan vs reality).
- any KPI over time (trends, history, period comparison): generic `metrics`.
- alerts/audit: generic `events`; immutable plans: `snapshots` +
  `published_plans` (+ triggers). New modules add rows/queries, not schema.

### Additional SalesApp tracking worth adding (proposed)
The SalesApp export also carries fields we don't use yet:
- **Scheduled vs Real duration** → on-POS time efficiency (planned vs actual).
- **Gap between one visit's finish and the next visit's start** → real travel
  time between POS (no routing engine needed).
- **Visit State** (completed / cancelled) → completion rate.
- **Visit purpose mix** (the many "Účel návštevy" columns) → what was actually
  done per POS/technician (supply, campaign launch, lottery pickup, small
  terminal, other), and compliance flags (responsible-gaming card, training
  certificate check, "no-betting" notice photographed).
- **First/last visit time of day, days worked, idle days** → working patterns,
  vacation detection.
- **GPS distance of the visit vs the POS location** → data-quality / suspicious
  visits (visited far from the POS).
- **Repeat visits to the same POS within a short window** → inefficiency.
- **Weekend / after-hours visits**, **chain/partner coverage**, **region mix**.

## Campaign coverage — definitions from the product owner
- **Target (how many)** = the Activity Plan **ODHAD** (`ODHAD_NAVSTEV_ZA_KAMPAN`).
  It is often empty in the Excel, so the app owns it: `campaigns.target_visits`
  is editable in-app (seeded from ODHAD when present).
- **Scope (which POS)** = the Activity Plan **TYPE**, exactly as the engine
  already matches campaigns to POS: every POS carries Lottery (LOS) + scratch
  (LOT), so LOS/LOT campaigns effectively span the network; market/terminal
  campaigns are a subset (`market_ok` / `terminal_ok`). Coverage must reuse the
  engine's `ActivityPlanWindow` + matching, not a divergent reimplementation.
- Coverage % = matching POS visited during the campaign window / target.

## Route Planner (module) — decisions from the product owner
The Route Planner is the **long-term technician visit plan** (who visits which
POS, when), driven by the agreed business rules via the engine. Confirmed
workflow:
- **Order within a day is left to the technician** — the app only decides WHICH
  POS belong to a day (geographic grouping); no intra-day sequencing.
- **Km are supportive information only** (efficiency indicator), never an
  optimisation goal; no fixed technician start point.
- The manager's need is **review & control** (see the plan per technician over
  weeks, verify coverage of goals/campaigns/cadence, regenerate with different
  rules) — not manual editing yet.
- Typical horizon **4–6 weeks**.

Implemented as a read model over the engine's output: `route_planner.py`
(materialise `draft_plans` + per-technician week→day view with supportive km),
endpoints `/api/planner/technicians` + `/api/planner/route`, and a Route
Planner review card in the UI. No planning logic is duplicated here.

## Roadmap
- **Fáze 0 (hotovo):** portable `.exe`, SQLite datastore, Planning Engine reads
  config from SQLite (db_state), existing UI, immutable publish.
- **Fáze 1:** Strategy Advisor, Field Brain, KPI dashboard, horizon simulation.
- **Fáze 2:** plan vs. reality from SalesApp, real driven-km from visit order,
  route map, efficiency evaluation.
- **Fáze 3:** route-optimisation recommendations, plan-vs-reality comparison,
  business scorecard.
- **Maps/routing:** open-source only (OpenStreetMap + Leaflet + OSRM/
  GraphHopper). No Google Maps API, no paid dependency.

## Full Field Force Management system — module-ready data model

This is not just a planner. The SQLite model is designed so every planned
module is **additive (new rows / new queries), never a schema migration**.
Three extensibility pillars make that true:

1. **Catalogs + link tables** — the relational core, incl. **Business
   Objectives**. Field Brain plans *goals*, not just visits: a visit can
   satisfy several objectives (Cadence, Sportka, Losy, Vánoce, Merchandising,
   Compliance, Audit…). New objective = one row in `objectives`.
2. **Generic `metrics` time-series** — any KPI for any entity (technician /
   OZ / region / POS / campaign / network / field_brain) over time. New KPI =
   new `metric_key`, no new table. Powers Dashboard + Reporting + Scorecard +
   route efficiency.
3. **Generic `events` + JSON `params`/`attributes`** — audit and flexible
   per-entity fields without migrations.

Module → tables it already has:

| Module | Backed by |
|---|---|
| Dashboard (KPI tech/OZ/region, campaign & network status, risks, Field Brain scorecard) | `metrics`, `technicians.role`, `regions`, `campaigns`, `pos_master` |
| POS card (last tech/OZ visit, full history, purposes, active campaigns, future/published plan, publish history, closure, compliance) | `pos_master`, `salesapp_visits`, `pos_objectives`, `published_plans`, `plan_lifecycle`, `closed_pos` |
| Planning (draft/published, multi-week, modes, tech+OZ capacity, simulations, Field Brain) | `drafts`, `snapshots`, `draft_plans`, `published_plans`, `campaigns`, `technicians`, `plan_stop_objectives` |
| SalesApp Analytics (visit order, km, travel/POS time, efficiency, plan vs reality, maps) | `salesapp_visits` (start/finish times), `route_metrics`, `published_plans` (gps/day_seq) |
| Reporting (history of tech/OZ/POS/campaign/region, performance over time) | `metrics`, `events`, all history tables |
| Field Brain (business objectives, dedup visits, value per visit, "POS complete") | `objectives`, `pos_objectives` (due), `visit_objectives` (done), `plan_stop_objectives` |
| Publish (immutable) | `snapshots`, `published_plans` (+ DB triggers) |

**"POS complete this week"** = every due objective (`pos_objectives`) is
fulfilled (`visit_objectives`) → a further visit has no business value.
Computed by query from these tables; Field Brain uses it to avoid duplicate
visits and maximise the business value of each visit.

People carry a **`role` (TECHNIK / OZ / OTHER)** and `capacity_per_week`, so
KPIs and capacity are tracked per role across the whole Field Force.

### OZ = informational only (never planned)
Field Brain plans **only technicians**. OZ are an information/control layer:
the planner and POS Explorer can see that an OZ already covered a POS (when,
what, how many visits) so a technician does not re-drive it without business
value. This needs no separate model — it falls out of `salesapp_visits.
visitor_role` + `visit_objectives`. Helper: `pos_insights.pos_visit_summary()`
/ `GET /api/pos/{id}/visits` returns last technician visit, last OZ visit,
per-role counts, and recent visits.

### Configuration platform (admin-configurable, not code)
The system is driven by configuration, so the single admin changes behaviour
without touching Python or the schema. Two layers:

- **`business_rules`** — planning rules (toggle/params/scope), see below.
- **Settings platform** — `setting_definitions` (catalog: namespace, key, type,
  default, range/options, UI group → drives a generic admin UI) + `settings`
  (values/overrides, scoped) + `saved_views` (named dashboard/report/map views).
  Namespaces: **planner** (max visits/day, work hours, km/day, horizon, modes),
  **optimization** (weights: campaign/cadence/neglected/distance/workload/ppt,
  objective priority), **scoring** (POS + technician score building blocks),
  **dashboard** (KPIs, charts), **report** (sections, export), **map** (heatmap,
  layers, colors, filters). Effective value = override else typed default.

Adding a new KPI / weight / metric / rule = **a new definition row (or one
INSERT)**, never an algorithm change. Managed via `settings.py` /
`business_rules.py` and `GET/PUT /api/settings/*`, `/api/rules/business`,
`/api/views/*`. The engine, dashboards, reports and maps only READ effective
config.

### Business Rules = data, not code
Planning logic is configurable from the database, not hardcoded. `business_rules`
is a typed catalog — one row per rule (CADENCE, MIN_GAP, NEGLECTED_AFTER,
HOLDBACK, MAX_VISITS_WEEK, CAMPAIGN_PRIORITY, GPS_EXTRA, OZ_COVERAGE, …) with
`enabled` + JSON `params` + optional **scope** (global < market < category <
technician < pos, most-specific wins). Toggle a rule, change its parameters, or
add a scoped override — no code change; adding a rule = one INSERT.

The **Planning Engine only reads** these: the db_state layer (Priority 2) calls
`business_rules.effective()` and maps the enabled rules into the config the
engine already consumes, so the algorithm is unchanged. Managed via
`business_rules.py` and `GET/PUT /api/rules/business`.

### SalesApp visit → POS linkage (must stay stable)
A SalesApp visit's **`Store UID` == `pos_master.terminal_id` → `pos_id`**
(the same mapping the engine uses). The importer resolves it, so
`salesapp_visits.pos_id` is the real POS (≈70% of visits link; the rest are
SalesApp stores not in POS_MASTER, e.g. "jiné POS"). All POS-level reporting
and the "POS already covered" logic depend on this link.

## Running
- Dev: `python3 desktop_app.py` (needs `pip install -r
  desktop_client/requirements-desktop.txt`).
- Prod: double-click `FieldForceOptimizer.exe`.

`FFO_LOCAL=1` selects the local runtime (SQLite store + auth bypass + frontend
served by FastAPI). The old cloud code paths (`gh.py`, GitHub Actions) remain
in the repo but are not used in production.
