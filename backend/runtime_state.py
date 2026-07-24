"""Runtime engine state, assembled from SQLite — the single source of truth.

Before this module the Planning Engine read its POS data from a separate
workbook ``state`` snapshot blob (store.snapshot_temp -> state_xlsx), while the
rest of the app read the same facts straight from SQLite. Those two
representations could diverge: an imported DB (or the bundled seed) populates
the SQLite tables but never necessarily writes a ``state`` snapshot, so the
planner would fail with ``POS_MASTER does not exist``.

This module removes the divergence. It rebuilds the engine's data sheets from
the SQLite tables (``pos_master`` + derived last-visit facts from
``salesapp_visits``), then assembles the full engine state with the *existing*
``pipeline.build_state`` (config sheets from the config store, empty ledgers for
the rest). The engine itself is unchanged — it still reads the same POS_MASTER
columns and config sheets; only the *source* of the data moves to SQLite.

The immutable published-version snapshots (snapshot_store) stay exactly as they
are — they remain the record for reproducibility — but they are no longer the
read source for "the current planning state".
"""
from __future__ import annotations

import datetime

import config_store
import db
import pipeline

# The canonical POS_MASTER sheet header the Planning / Import engines use
# (desktop_client/engines/import_engine.py). Order matters: the engine indexes
# columns by name, so we emit exactly this header and fill it from SQLite.
POS_MASTER_HEADER = [
    "posId", "terminalId", "market", "category", "terminalType", "classification",
    "nazev", "area", "posArea", "street", "houseNumber", "city", "gpsX", "gpsY",
    "assignedTechnician", "ppt", "status", "closedSinceWeek", "closedSinceYear",
    "currentLosActivity", "currentLotActivity", "targetLosActivity", "targetLotActivity",
    "lastRealVisitDate", "lastRealVisitWeek", "lastPlannedVisitDate",
    "weeksSinceLastVisit", "visitCountThisCampaign", "businessScore",
    "plannerStatus", "assignedWeek", "assignedDay", "gpsGroup",
    "managerOverrideType", "managerOverridePriority", "managerOverrideTechnician",
    "plannerNotes", "importedAt", "updatedAt",
]


def _last_visits() -> tuple[dict, str | None]:
    """pos_id -> latest visit_date (any role, ISO date), plus the earliest
    visit date across the whole dataset (the observation window floor)."""
    last: dict[str, str] = {}
    earliest: str | None = None
    for r in db.get("SELECT pos_id, visit_date FROM salesapp_visits "
                    "WHERE pos_id IS NOT NULL AND visit_date IS NOT NULL"):
        pid = str(r["pos_id"]); d = str(r["visit_date"])[:10]
        if pid not in last or d > last[pid]:
            last[pid] = d
        if earliest is None or d < earliest:
            earliest = d
    return last, earliest


def _parse_cs_date(v: str) -> str | None:
    """Parse a Czech-formatted plan date ("20. 7. 2026" = D. M. YYYY) into an
    ISO date string, so plan dates and salesapp visit dates become comparable.
    Returns None for anything unparseable (never raises)."""
    try:
        parts = [p.strip() for p in str(v).replace("\xa0", " ").split(".") if p.strip()]
        if len(parts) < 3:
            return None
        d, m, y = int(parts[0]), int(parts[1]), int(parts[2])
        return datetime.date(y, m, d).isoformat()
    except (ValueError, TypeError):
        return None


def _last_planned() -> dict:
    """pos_id -> latest ISO date this POS appeared on the PUBLISHED tourplan.

    The engine otherwise derives recency only from salesapp_visits, so when a
    technician was sent to a POS on last week's tourplan but salesapp never
    recorded that visit, the engine would happily re-send them the very next
    run (product owner, 2026-07-24: "když tam už byl minulý týden i v rozporu
    se salesapp, aby ho to tam neposílalo znova"). Treating the published plan
    as a recency source closes that double-visit gap and lets the planner
    genuinely continue from its own previous tourplan, not just from salesapp.
    """
    today_iso = datetime.date.today().isoformat()
    planned: dict[str, str] = {}
    for r in db.get("SELECT pos_id, plan_date FROM published_plans "
                    "WHERE pos_id IS NOT NULL AND plan_date IS NOT NULL"):
        pid = str(r["pos_id"])
        iso = _parse_cs_date(r["plan_date"])
        # Only PAST plan dates count as "was recently there". A future planned
        # date isn't a visit yet — those commitments are handled separately by
        # the engine's locked-week carry-over, not by recency.
        if iso and iso <= today_iso and (pid not in planned or iso > planned[pid]):
            planned[pid] = iso
    return planned


def _weeks_between(iso_a: str, day_b: datetime.date) -> int:
    try:
        a = datetime.date.fromisoformat(iso_a[:10])
    except (ValueError, TypeError):
        return 0
    return max(0, (day_b - a).days // 7)


def _count_planned_as_visited() -> bool:
    """Whether a POS on the previous PUBLISHED tourplan counts toward recency
    (so it isn't re-sent next run). On by default; UI-toggleable."""
    try:
        import settings
        v = settings.get("planner", "count_planned_as_visited")
        if v is None:
            return True
        return str(v).strip() not in ("0", "false", "False", "no", "")
    except Exception:  # noqa: BLE001
        return True


def build_pos_master() -> list[list]:
    """POS_MASTER sheet (header + rows) rebuilt from the pos_master table, with
    last-visit facts derived from salesapp_visits — the same derivation the live
    views use (a never-visited POS is treated as overdue across the whole
    observed window, i.e. maximally urgent)."""
    today = datetime.date.today()
    last, earliest = _last_visits()
    planned = _last_planned() if _count_planned_as_visited() else {}
    never_weeks = _weeks_between(earliest, today) if earliest else 260  # window floor

    rows: list[list] = [list(POS_MASTER_HEADER)]
    for r in db.get(
        "SELECT pos_id, terminal_id, market, category, terminal_type, classification, "
        "name, area, pos_area, street, house_number, city, gps_x, gps_y, technician, "
        "ppt, active, manager_override_type, updated_at FROM pos_master"):
        pid = str(r["pos_id"])
        real_lv = last.get(pid)          # last EXECUTED visit (salesapp)
        plan_lv = planned.get(pid)       # last PUBLISHED tourplan date
        # Recency = the more recent of "actually visited" and "already on the
        # published plan", so a POS scheduled last week isn't re-sent this run
        # even if salesapp missed that visit.
        eff_lv = max([d for d in (real_lv, plan_lv) if d], default=None)
        if eff_lv:
            wsl = _weeks_between(eff_lv, today)
            try:
                iso_week = datetime.date.fromisoformat(eff_lv).isocalendar()[1]
            except (ValueError, TypeError):
                iso_week = ""
        else:
            wsl = never_weeks
            iso_week = ""
        row = [""] * len(POS_MASTER_HEADER)
        def put(col, val):
            row[POS_MASTER_HEADER.index(col)] = "" if val is None else val
        put("posId", pid)
        put("terminalId", r["terminal_id"])
        put("market", r["market"])
        put("category", r["category"])
        put("terminalType", r["terminal_type"])
        put("classification", r["classification"])
        put("nazev", r["name"])
        put("area", r["area"])
        put("posArea", r["pos_area"])
        put("street", r["street"])
        put("houseNumber", r["house_number"])
        put("city", r["city"])
        put("gpsX", r["gps_x"])
        put("gpsY", r["gps_y"])
        put("assignedTechnician", r["technician"])
        put("ppt", r["ppt"])
        put("status", "Active" if r["active"] else "Closed")
        put("lastRealVisitDate", real_lv or "")
        put("lastPlannedVisitDate", plan_lv or "")
        put("lastRealVisitWeek", iso_week)
        put("weeksSinceLastVisit", wsl)
        put("managerOverrideType", r["manager_override_type"])
        put("updatedAt", r["updated_at"])
        rows.append(row)
    return rows


def build_activity_plan() -> list[list]:
    """ACTIVITY_PLAN sheet from the campaigns table. The engine reads it by
    position: [activityType(LOS/LOT), activity(name), startWeek, endWeek]."""
    rows: list[list] = [["activityType", "activity", "startWeek", "endWeek",
                         "priority", "overrideGap", "year"]]
    for r in db.get("SELECT kind, name, start_week, end_week, priority, override_gap, year "
                    "FROM campaigns WHERE active=1 ORDER BY start_week, name"):
        rows.append([r["kind"] or "", r["name"] or "", r["start_week"], r["end_week"],
                     r["priority"], r["override_gap"], r["year"]])
    return rows


def build(config_state: dict | None = None) -> dict[str, list[list]]:
    """Assemble the full engine state from SQLite. POS_MASTER comes from the DB;
    config sheets from the config store; every other ledger from its empty
    header (a fresh, unlocked base — callers overlay plan/lifecycle as needed).

    This is the base a hypothetical replan (simulate/assess/what-if) runs over:
    POS + config reality, no prior plan or week locks, so the whole horizon is
    planned fresh."""
    cfg = config_state if config_state is not None else config_store.load_config_state()
    # ACTIVITY_PLAN is a config sheet the scaffold ships empty; the real
    # campaigns live in SQLite, so inject them here.
    cfg = dict(cfg)
    cfg["ACTIVITY_PLAN"] = build_activity_plan()
    snapshot = {"POS_MASTER": build_pos_master()}
    return pipeline.build_state(cfg, [], [], snapshot=snapshot)


def to_temp_xlsx() -> str:
    """Build the runtime state and materialise it to a temp .xlsx, returning the
    path (caller deletes). For the few code paths that still take a workbook
    path (e.g. brain.preflight) rather than a state dict."""
    import tempfile

    import state_xlsx
    fd, path = tempfile.mkstemp(suffix=".xlsx")
    import os
    os.close(fd)
    state_xlsx.save_state(build(), path)
    return path
