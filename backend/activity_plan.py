"""Activity-plan intelligence: segment urgency + network-coverage forecast.

Two read-only questions a manager plans against:
  • "How long since we were on the big terminals / small terminals / B branches /
     post offices?" — per segment, how many are overdue past a configurable gap
     (default 3 months), so urgency is visible before it hurts.
  • "How much of the network can I realistically cover, and in how many weeks?" —
     a capacity forecast from the real active-technician count and per-week visits.

Deterministic, read-only over SQLite. The gap threshold is UI-configurable
(settings namespace `activity`, key `overdue_gap_weeks`). No engine change.
"""
from __future__ import annotations

import db

# (label, SQL predicate over pos_master p). Segments the manager reasons about.
_SEGMENTS = [
    ("Velké terminály", "p.terminal_type='VELKY TERMINAL'"),
    ("Malé terminály", "p.terminal_type IN ('SMALL TERMINAL','LI')"),
    ("Béčkové pobočky (klasifikace B)", "p.classification='B'"),
    ("Pošty", "p.classification='P'"),
]
_DEFAULT_GAP_WEEKS = 13   # ~3 months


def _gap_weeks() -> float:
    try:
        import settings
        v = settings.get("activity", "overdue_gap_weeks")
        return float(v) if v is not None else _DEFAULT_GAP_WEEKS
    except Exception:  # noqa: BLE001
        return _DEFAULT_GAP_WEEKS


def segment_urgency(gap_weeks: float | None = None) -> dict:
    """Per segment: total active POS, how many were visited within the gap, how
    many are overdue (last visit older than the gap or never), and the worst gap.
    Any TECHNIK or OZ visit counts as coverage (same rule as the POS table)."""
    gap = float(gap_weeks) if gap_weeks is not None else _gap_weeks()
    out = []
    for label, pred in _SEGMENTS:
        rows = db.get(
            "SELECT p.pos_id, v.lv, "
            "  CASE WHEN v.lv IS NULL THEN NULL "
            "       ELSE (julianday('now') - julianday(v.lv)) / 7.0 END wk "
            "FROM pos_master p LEFT JOIN "
            "  (SELECT pos_id, MAX(visit_date) lv FROM salesapp_visits GROUP BY pos_id) v "
            "  ON v.pos_id = p.pos_id "
            f"WHERE p.active=1 AND {pred}")
        total = len(rows)
        if not total:
            continue
        never = sum(1 for r in rows if r["lv"] is None)
        weeks = [r["wk"] for r in rows if r["wk"] is not None]
        overdue = never + sum(1 for w in weeks if w > gap)
        within = total - overdue
        weeks_sorted = sorted(weeks)
        median = weeks_sorted[len(weeks_sorted) // 2] if weeks_sorted else None
        worst = max(weeks) if weeks else None
        out.append({
            "segment": label, "total": total,
            "within": within, "overdue": overdue, "never": never,
            "overduePct": round(100 * overdue / total),
            "medianWeeks": round(median, 1) if median is not None else None,
            "worstWeeks": round(worst, 1) if worst is not None else None,
            "level": "bad" if overdue / total >= 0.4 else ("warn" if overdue / total >= 0.15 else "ok"),
        })
    return {"gapWeeks": gap, "gapMonths": round(gap / 4.33, 1), "segments": out}


def _weekly_capacity() -> dict:
    """Realistic POS/week the field can serve: technicians who ACTUALLY work the
    field (had a real visit in the last ~60 days) × per-week visits. Counting
    every nominally-active TECHNIK overstates capacity — many carry no visits."""
    active = db.get(
        "SELECT COUNT(DISTINCT s.technician) c FROM salesapp_visits s "
        "JOIN technicians t ON t.name=s.technician "
        "WHERE t.role='TECHNIK' AND t.active=1 AND t.excluded=0 "
        "AND s.visit_date IS NOT NULL AND date(s.visit_date) >= date('now','-60 day')")[0]["c"]
    nominal = db.get("SELECT COUNT(*) c FROM technicians WHERE role='TECHNIK' AND active=1 AND excluded=0")[0]["c"]
    techs = active or nominal
    per = None
    try:
        import settings
        per = settings.get("planner", "visits_per_tech_week")
    except Exception:  # noqa: BLE001
        per = None
    per = float(per) if per else 40.0
    return {"technicians": techs, "nominalTechnicians": nominal,
            "perTechWeek": per, "weekly": int(round(techs * per))}


def coverage_forecast(weeks: int = 5) -> dict:
    """How much of the active network N weeks of capacity can touch (unique POS),
    plus how many weeks a full single pass would take. Honest ceiling — the
    engine's cadence/holdback decide the actual mix; this is the envelope."""
    cap = _weekly_capacity()
    total = db.get("SELECT COUNT(*) c FROM pos_master WHERE active=1")[0]["c"]
    weekly = max(1, cap["weekly"])
    weeks = max(1, int(weeks))
    servable = min(total, weekly * weeks)
    weeks_full = round(total / weekly, 1) if weekly else None
    return {
        "totalPos": total, "weeklyCapacity": weekly,
        "technicians": cap["technicians"], "perTechWeek": cap["perTechWeek"],
        "weeks": weeks, "servable": servable,
        "coveragePct": round(100 * servable / total) if total else 0,
        "weeksForFullNetwork": weeks_full,
    }
