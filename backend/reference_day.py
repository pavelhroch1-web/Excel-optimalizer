"""Planner — the REFERENCE DAY: one learned, technician-agnostic model of a
long-term achievable working day, assembled from the collective ČR history.

    day budget  = learned productive minutes − on-top (config) − reserve
    stop cost   = learned visit duration + learned real transition

Nothing here is per-technician: productive minutes and durations/transitions are
national/role standards (capacity.py / duration.py / transition_model.py), so the
plan reflects what is realistically doable in the country, not how a specific
person drives. On-top is the one BUSINESS input (installs, audits, promo…): it is
configured, not learned, and is subtracted from the budget before planning.

This module only ASSEMBLES existing learned models behind one interface; the
planner and the feasibility layer read the reference day, never the pieces.
"""
from __future__ import annotations

import datetime

import capacity
import db
import duration
import settings
import transition_model

_DEFAULT_RESERVE_MIN = 30.0     # end-of-day / admin buffer, config-overridable


def reserve_minutes() -> float:
    try:
        v = settings.get("planner", "day_reserve_minutes")
        if v not in (None, ""):
            return max(0.0, float(v))
    except (TypeError, ValueError, Exception):  # noqa: BLE001
        pass
    return _DEFAULT_RESERVE_MIN


def _fallback_work_minutes() -> float:
    try:
        v = settings.get("planner", "work_hours_per_day")
        if v not in (None, ""):
            return float(v) * 60.0
    except (TypeError, ValueError, Exception):  # noqa: BLE001
        pass
    return 480.0


_DEFAULT_TARGET_HOURS = 7.0     # target productive hours/day the plan pulls up to


def target_work_minutes() -> float:
    """Target productive minutes/day the reference day aims for — a company goal
    (ambitious standard), not what history currently shows. Config-overridable."""
    try:
        v = settings.get("planner", "target_work_hours")
        if v not in (None, ""):
            return max(0.0, float(v)) * 60.0
    except (TypeError, ValueError, Exception):  # noqa: BLE001
        pass
    return _DEFAULT_TARGET_HOURS * 60.0


def productive_minutes(role: str = "TECHNIK") -> tuple[float, bool]:
    """Ambitious productive minutes for a day: the LEARNED collective standard,
    but never below the company target (default ~7 h). The plan should pull the
    team UP to the target, not freeze today's shorter days. (minutes, learned?)."""
    rec = capacity.recommended(role)
    learned = float(rec["productiveMinutes"]) if (rec.get("found") and rec.get("productiveMinutes")) else _fallback_work_minutes()
    target = target_work_minutes()
    # take the higher of learned vs target — history says what's possible, the
    # target says where we want the team to get to.
    return round(max(learned, target), 1), bool(rec.get("found"))


def budget_minutes(role: str = "TECHNIK") -> float:
    """Gross day budget available for stops+transitions (before per-day on-top):
    learned productive minutes − reserve."""
    prod, _ = productive_minutes(role)
    return round(prod - reserve_minutes(), 1)


def _iso_week(date_str) -> int | None:
    try:
        return datetime.date.fromisoformat(str(date_str)[:10]).isocalendar()[1]
    except (ValueError, TypeError):
        return None


def ontop_by_tech_week(week_from: int | None = None,
                       week_to: int | None = None) -> dict[tuple, float]:
    """On-top task minutes per (technician, iso-week): open tasks on a POS,
    attributed to the POS' technician, bucketed by the task's assigned week. Pure
    business input (config), subtracted from the day budget. Empty when no tasks."""
    if not db.get("SELECT name FROM sqlite_master WHERE type='table' AND name='tasks'"):
        return {}
    rows = db.get(
        "SELECT p.technician tech, t.assigned_date ad, t.deadline dl, t.est_minutes est "
        "FROM tasks t JOIN pos_master p ON p.pos_id = t.pos_id "
        "WHERE t.status IN ('open','planned') AND t.est_minutes IS NOT NULL "
        "AND p.technician IS NOT NULL AND p.technician <> ''")
    out: dict[tuple, float] = {}
    for r in rows:
        wk = _iso_week(r["ad"]) or _iso_week(r["dl"])
        if wk is None:
            continue
        if week_from is not None and wk < week_from:
            continue
        if week_to is not None and wk > week_to:
            continue
        key = (r["tech"], wk)
        out[key] = out.get(key, 0.0) + float(r["est"] or 0.0)
    return out


def calibration(role: str = "TECHNIK") -> dict:
    """The whole reference day in one payload, for the UI / audit — so the
    administrator sees exactly what a planned day is built from and where the
    numbers come from (learned vs config)."""
    prod, learned = productive_minutes(role)
    res = reserve_minutes()
    rec = capacity.recommended(role)
    learned_min = float(rec["productiveMinutes"]) if (rec.get("found") and rec.get("productiveMinutes")) else None
    target_min = target_work_minutes()
    du = duration.overview()
    tr = transition_model.overview()
    tr_ready = any(b.get("n") for b in tr.get("byBand", []))
    nat_dur = (du.get("national") or {}).get("p50")
    return {
        "role": role,
        "budget": {
            "productiveMinutes": round(prod, 1),
            "productiveLearned": learned,
            "learnedMinutes": learned_min,
            "targetMinutes": target_min,
            "targetPulledUp": bool(learned_min is not None and target_min > learned_min),
            "reserveMinutes": res,
            "grossBudgetMinutes": round(prod - res, 1),
            "note": "rozpočet = cíl(vyšší z učeného/cílového) − rezerva − on-top (per den)",
        },
        "stopCost": {
            "durationModelP50": nat_dur,
            "durationLearned": nat_dur is not None,
            "transitionReady": tr_ready,
            "transitionTargetQuantile": tr.get("targetQuantile"),
            "note": "cena zastávky = naučená délka návštěvy + naučený reálný přejezd",
        },
        "learnedFrom": "celá historie ČR (kolektivní standard, ne jednotlivec)",
    }
