"""Time-feasibility advisory for an already-generated plan.

The Planning Engine sizes a day by a flat VISIT COUNT (TARGET_VISITS_DAY). This
module does NOT change that decision — it reads the plan the engine already
produced (draft_plans) and answers a separate, advisory question the manager
actually cares about: **does the day fit in the working hours?**

It reuses components that already exist in the repo but the planner never wired
together:
  * duration.predict_for()  — learned on-POS visit-duration model (p50/p75 min),
  * travel_model            — straight-line km -> realistic road time (nonlinear
                              speed ramp),
  * core_logic.distance_km / diagnostics._nn_order — the same offline distance
    and nearest-order helpers the day view already uses.

Fully deterministic (same plan -> same numbers), offline (no OSRM / network),
and read-only over draft_plans. It never feeds back into selection, so the
generated plan is byte-for-byte unchanged whether or not this runs.
"""
from __future__ import annotations

import db
import duration
import travel_model
from desktop_client.engines.core_logic import distance_km

_DEFAULT_WORK_HOURS = 8.0        # available field hours/day if nothing configured
_TIGHT_RATIO = 0.85             # >= this share of the day used -> "napjatý"
_OVER_RATIO = 1.0               # > available -> "přeplněný"


def _work_hours_per_day() -> float:
    try:
        import settings
        v = settings.get("planner", "work_hours_per_day")
        if v not in (None, ""):
            return float(v)
    except Exception:  # noqa: BLE001
        pass
    return _DEFAULT_WORK_HOURS


def _nn_order(points: list[tuple[float, float]]) -> list[int]:
    """Nearest-neighbour visit order over (x, y) points — the same heuristic the
    day view uses, inlined here to keep this module's estimate independent of any
    planning decision. Used ONLY to estimate travel time, never to plan."""
    n = len(points)
    if n <= 1:
        return list(range(n))
    remaining = list(range(1, n))
    order = [0]
    while remaining:
        last = points[order[-1]]
        nxt = min(remaining, key=lambda i: distance_km(last[0], last[1], points[i][0], points[i][1]))
        order.append(nxt)
        remaining.remove(nxt)
    return order


def _day_travel_minutes(points: list[tuple[float, float]]) -> float:
    """Estimated road driving minutes for a day's stops. Orders them
    nearest-neighbour (start point unknown, same as the engine's day view),
    turns each leg's straight-line km into realistic road minutes via
    travel_model. Points at (0,0) — no GPS — contribute no leg."""
    pts = [p for p in points if not (p[0] == 0 and p[1] == 0)]
    if len(pts) < 2:
        return 0.0
    order = _nn_order(pts)
    legs = [distance_km(pts[order[i]][0], pts[order[i]][1], pts[order[i + 1]][0], pts[order[i + 1]][1])
            for i in range(len(order) - 1)]
    return travel_model.minutes_for_legs(legs)


def _duration_cache():
    """Batch region-per-technician once; memoise duration lookups by
    (category, market, region) so a whole plan needs only a handful of model
    reads."""
    region_of: dict[str, str | None] = {}
    cache: dict[tuple, float] = {}

    def on_pos_minutes(category, market, technician) -> float:
        if technician not in region_of:
            region_of[technician] = duration._region_of(technician)
        region = region_of[technician]
        key = (category or "", market or "", region or "")
        if key not in cache:
            pred = duration.predict_for(category, market, region, technician)
            # p50 (median) is the honest day-fill figure; p75 would over-fill.
            cache[key] = float(pred.get("p50") or 0.0)
        return cache[key]

    return on_pos_minutes


def feasibility(week_from: int | None = None, week_to: int | None = None) -> dict:
    """Per (technician, week, day) time load of the generated plan vs available
    hours. Advisory only — does not touch the plan."""
    where = ["dp.pos_id IS NOT NULL"]
    params: list = []
    if week_from is not None:
        where.append("dp.week >= ?"); params.append(week_from)
    if week_to is not None:
        where.append("dp.week <= ?"); params.append(week_to)
    rows = db.get(
        "SELECT dp.year yr, dp.week wk, dp.day day, dp.plan_date pdate, dp.technician tech, "
        "dp.pos_id pos, dp.category cat, dp.gps_x gx, dp.gps_y gy, p.market market "
        "FROM draft_plans dp LEFT JOIN pos_master p ON p.pos_id = dp.pos_id "
        f"WHERE {' AND '.join(where)} ORDER BY dp.technician, dp.week, dp.day", tuple(params))

    work_hours = _work_hours_per_day()
    avail_min = work_hours * 60.0
    on_pos_minutes = _duration_cache()

    # bucket rows by (tech, week, day)
    days: dict[tuple, dict] = {}
    for r in rows:
        key = (r["tech"], int(r["wk"]), r["day"])
        d = days.setdefault(key, {"technician": r["tech"], "week": int(r["wk"]),
                                  "day": r["day"], "date": r["pdate"], "visits": 0,
                                  "onPosMin": 0.0, "_pts": []})
        d["visits"] += 1
        d["onPosMin"] += on_pos_minutes(r["cat"], r["market"], r["tech"])
        d["_pts"].append((r["gx"] or 0, r["gy"] or 0))

    day_list = []
    for d in days.values():
        travel = _day_travel_minutes(d.pop("_pts"))
        total = round(d["onPosMin"] + travel, 1)
        ratio = round(total / avail_min, 3) if avail_min else None
        d["onPosMin"] = round(d["onPosMin"], 1)
        d["travelMin"] = round(travel, 1)
        d["totalMin"] = total
        d["availMin"] = round(avail_min, 1)
        d["loadPct"] = round(100 * ratio, 1) if ratio is not None else None
        d["status"] = ("přeplněný" if ratio and ratio > _OVER_RATIO
                       else ("napjatý" if ratio and ratio >= _TIGHT_RATIO else "ok"))
        day_list.append(d)

    # roll up per (technician, week)
    weeks: dict[tuple, dict] = {}
    for d in day_list:
        key = (d["technician"], d["week"])
        w = weeks.setdefault(key, {"technician": d["technician"], "week": d["week"],
                                   "days": 0, "visits": 0, "onPosMin": 0.0,
                                   "travelMin": 0.0, "totalMin": 0.0, "overloadedDays": 0})
        w["days"] += 1
        w["visits"] += d["visits"]
        w["onPosMin"] += d["onPosMin"]
        w["travelMin"] += d["travelMin"]
        w["totalMin"] += d["totalMin"]
        if d["status"] == "přeplněný":
            w["overloadedDays"] += 1
    week_list = []
    for w in weeks.values():
        avail = w["days"] * avail_min
        for k in ("onPosMin", "travelMin", "totalMin"):
            w[k] = round(w[k], 1)
        w["availMin"] = round(avail, 1)
        w["loadPct"] = round(100 * w["totalMin"] / avail, 1) if avail else None
        week_list.append(w)

    dur_ov = duration.overview()
    return {
        "workHoursPerDay": work_hours,
        "durationModelReady": bool(dur_ov.get("national")),
        "days": sorted(day_list, key=lambda x: (x["technician"], x["week"], str(x["day"]))),
        "weeks": sorted(week_list, key=lambda x: (x["technician"], x["week"])),
        "overloadedDays": sum(1 for d in day_list if d["status"] == "přeplněný"),
        "tightDays": sum(1 for d in day_list if d["status"] == "napjatý"),
        "totalDays": len(day_list),
        "note": ("Časová proveditelnost je poradní: engine plánuje na počet návštěv, "
                 "tato vrstva ukazuje reálný čas (doba návštěvy + silniční přejezd) "
                 "vs. dostupné hodiny. Nemění plán."),
    }
