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

## Roadmap
- **Fáze 0 (hotovo):** portable `.exe`, SQLite datastore, current Planning
  Engine local, existing UI, immutable publish.
- **Fáze 1:** Strategy Advisor, Field Brain, KPI dashboard, horizon simulation.
- **Fáze 2:** plan vs. reality from SalesApp, real driven-km from visit order,
  route map, efficiency evaluation.
- **Fáze 3:** route-optimisation recommendations, plan-vs-reality comparison,
  business scorecard.
- **Maps/routing:** open-source only (OpenStreetMap + Leaflet + OSRM/
  GraphHopper). No Google Maps API, no paid dependency.

## Running
- Dev: `python3 desktop_app.py` (needs `pip install -r
  desktop_client/requirements-desktop.txt`).
- Prod: double-click `FieldForceOptimizer.exe`.

`FFO_LOCAL=1` selects the local runtime (SQLite store + auth bypass + frontend
served by FastAPI). The old cloud code paths (`gh.py`, GitHub Actions) remain
in the repo but are not used in production.
