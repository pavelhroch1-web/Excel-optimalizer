"""Bridge: DB configuration -> the engine's CONTROL knobs.

Priority 2. The Planning Engine stays generic and only READS its CONTROL/
config sheets. This module maps the DB-configured business_rules + settings
onto the exact CONTROL keys the engine already reads, and applies the strategy
mode. No algorithm change: with config synced from the imported CONTROL, the
overlay reproduces the baseline plan; editing a rule/setting in the DB then
changes planning with zero code changes.

Mode is config-only (campaign windows on/off), reusing brain.apply_mode.
"""
from __future__ import annotations

import brain
import business_rules
import settings

# strategy modes exposed to the app (brain has dojezd/kampan/vyvazeny;
# "cela_sit" = whole-network dojezd: campaigns off, fill by neglect/score).
MODES = {
    "dojezd": "Dojezd sítě",
    "kampan": "Kampaňový režim",
    "vyvazeny": "Vyvážený režim",
    "cela_sit": "Celá síť",
}


def _apply_business_rules(state: dict) -> None:
    """Enabled business rules -> existing engine CONTROL keys."""
    eff = business_rules.effective()

    def rule(code):
        r = eff.get(code)
        return (r["params"] if r and r["enabled"] else None)

    p = rule("MIN_GAP")
    if p and "weeks" in p:
        brain._set_control(state, "STANDARD_VISIT_GAP", p["weeks"])

    p = rule("NEGLECTED_AFTER")
    if p and "weeks" in p:
        brain._set_control(state, "NEGLECTED_AFTER_WEEKS", p["weeks"])

    hb = rule("HOLDBACK")
    if hb:
        brain._set_control(state, "HOLDBACK_LOOKAHEAD_WEEKS", hb.get("lookahead_weeks", 0))
        if "tolerance_a" in hb:
            brain._set_control(state, "HOLDBACK_TOLERANCE_A_WEEKS", hb["tolerance_a"])
        if "tolerance_other" in hb:
            brain._set_control(state, "HOLDBACK_TOLERANCE_OTHER_WEEKS", hb["tolerance_other"])
    elif "HOLDBACK" in eff:            # rule present but disabled -> no deferral
        brain._set_control(state, "HOLDBACK_LOOKAHEAD_WEEKS", 0)

    cap = rule("MAX_VISITS_WEEK")
    if cap:
        if "per_week" in cap:          # only if the admin set a flat weekly cap
            brain._set_control(state, "TARGET_VISITS_WEEK", cap["per_week"])
        if "per_day" in cap:
            brain._set_control(state, "TARGET_VISITS_DAY", cap["per_day"])

    gps = rule("GPS_EXTRA")
    if gps:
        brain._set_control(state, "GPS_EXTRA_ENABLED", 1)
        if "max_extra_visits" in gps:
            brain._set_control(state, "GPS_EXTRA_MAX_VISITS", gps["max_extra_visits"])
    elif "GPS_EXTRA" in eff:
        brain._set_control(state, "GPS_EXTRA_ENABLED", 0)


def _apply_planner_settings(state: dict) -> None:
    """Planner settings override overlapping capacity knobs."""
    pl = settings.effective("planner")
    if pl.get("max_visits_per_day"):
        brain._set_control(state, "TARGET_VISITS_DAY", pl["max_visits_per_day"])


def _apply_exclusions(state: dict) -> int:
    """Inject the manager's hard-excluded POS into the engine's BLACKLIST so
    they are never planned (engine rejects them: 'Na blacklistu')."""
    import db
    ids = [str(r["pos_id"]) for r in db.get("SELECT pos_id FROM pos_exclusions")]
    if not ids:
        return 0
    bl = state.get("BLACKLIST")
    if not bl:
        bl = [["posId"]]
        state["BLACKLIST"] = bl
    existing = {str(r[0]).strip() for r in bl[1:] if r and r[0] not in (None, "")}
    for pid in ids:
        if pid not in existing:
            bl.append([pid])
    return len(ids)


def _apply_reassignments(state: dict) -> int:
    """Temporary POS reassignments (vacation cover) -> managerOverrideTechnician,
    so the engine plans those POS under the covering technician."""
    import datetime as _dt
    import db
    today = _dt.date.today().isoformat()
    rows = db.get(
        "SELECT from_technician, pos_id, to_technician FROM pos_reassignments "
        "WHERE active=1 AND (valid_from IS NULL OR valid_from<=?) "
        "AND (valid_to IS NULL OR valid_to>=?)", (today, today))
    if not rows:
        return 0
    pm = state.get("POS_MASTER")
    if not pm:
        return 0
    h = {n: i for i, n in enumerate(pm[0])}
    ai, oi, pi = h.get("assignedTechnician"), h.get("managerOverrideTechnician"), h.get("posId")
    if oi is None or pi is None:
        return 0
    whole = {str(r["from_technician"]): r["to_technician"] for r in rows if r["from_technician"]}
    per_pos = {str(r["pos_id"]): r["to_technician"] for r in rows if r["pos_id"]}
    n = 0
    for row in pm[1:]:
        pid = str(row[pi]) if pi < len(row) else ""
        if pid in per_pos:
            row[oi] = per_pos[pid]; n += 1
        elif ai is not None and str(row[ai]) in whole:
            row[oi] = whole[str(row[ai])]; n += 1
    return n


def _apply_priority(state: dict) -> int:
    """OZ-campaign-prep POS -> managerOverrideType=FORCE_INCLUDE, so the engine
    guarantees them a slot (bypasses filters + the min-gap penalty). This is the
    highest planning priority achievable without touching the algorithm."""
    import db
    ids = {str(r["pos_id"]) for r in db.get(
        "SELECT pos_id FROM pos_priority WHERE active=1")}
    if not ids:
        return 0
    pm = state.get("POS_MASTER")
    if not pm:
        return 0
    h = {n: i for i, n in enumerate(pm[0])}
    pi, ti = h.get("posId"), h.get("managerOverrideType")
    if pi is None or ti is None:
        return 0
    n = 0
    for row in pm[1:]:
        pid = str(row[pi]) if pi < len(row) else ""
        if pid in ids and str(row[ti]).upper() != "FORCE_EXCLUDE":  # explicit exclude wins
            row[ti] = "FORCE_INCLUDE"; n += 1
    return n


def configure(state: dict, mode: str, start_week: int, length: int,
              visits_per_tech_week: float | None = None) -> dict:
    """Apply all DB config + mode to `state` in place, then set the planning
    window. Returns mode meta. Precedence: business_rules -> planner settings
    -> explicit capacity override -> mode."""
    _apply_business_rules(state)
    _apply_planner_settings(state)
    _apply_exclusions(state)
    _apply_priority(state)
    _apply_reassignments(state)
    if visits_per_tech_week:
        brain.apply_capacity(state, visits_per_tech_week)
    # "cela_sit" behaves like dojezd for campaign windows (whole-network sweep).
    brain_mode = "dojezd" if mode == "cela_sit" else mode
    meta = brain.apply_mode(state, brain_mode)
    brain._set_control(state, "CAMPAIGN_START_WEEK", start_week)
    brain._set_control(state, "CAMPAIGN_LENGTH", length)
    return {"mode": mode, "label": MODES.get(mode, meta.get("label", mode))}


def default_horizon() -> int:
    v = settings.get("planner", "planning_horizon_weeks")
    return int(v) if v else 5


def default_mode() -> str:
    return settings.get("planner", "default_mode") or "vyvazeny"
