"""Route Planner = long-term technician visit plan, read model over SQLite.

This module does NOT decide anything - the Planning Engine (config-driven via
db_state, following the agreed business rules) decides which technician visits
which POS, when, and the geographic day grouping. Here we only:
  * persist that decision into draft_plans (queryable, per technician/week/day),
  * expose a per-technician multi-week working view,
  * attach km as SUPPORTIVE info (efficiency), never as an optimisation goal.

Km are informational: with no fixed technician start point (product owner),
we report a nearest-neighbour chain length through a day's POS as an estimate
of intra-day driving - a spread indicator, not a real route.
"""
from __future__ import annotations

import db
from desktop_client.engines.core_logic import distance_km

# MANAGER_PLAN column -> draft_plans column
_MP_TO_DRAFT = {
    "WEEK": "week", "DATE": "plan_date", "DAY": "day", "TECHNICIAN": "technician",
    "POS": "pos_id", "KATEGORIE": "category", "NAZEV_PROVOZOVNY": "name",
    "ULICE": "street", "CISLO": "house_number", "MESTO": "city", "OBLAST": "area",
    "POS_AREA": "pos_area", "PPT": "ppt", "REASON": "reason", "GPS_GROUP": "day_group",
}

_DAY_ORDER = {"MON": 1, "TUE": 2, "WED": 3, "THU": 4, "FRI": 5}


def materialize_draft_plans(state: dict, year: int = 2026) -> int:
    """Replace draft_plans with the current engine MANAGER_PLAN, joining GPS
    from pos_master. Pure persistence of the engine's decision."""
    mp = state.get("MANAGER_PLAN") or []
    if len(mp) < 2:
        return 0
    hidx = {str(h): i for i, h in enumerate(mp[0])}
    conn = db.connect()
    try:
        gps = {str(r["pos_id"]): (r["gps_x"], r["gps_y"])
               for r in conn.execute("SELECT pos_id, gps_x, gps_y FROM pos_master")}
        conn.execute("DELETE FROM draft_plans")
        n = 0
        for row in mp[1:]:
            wk = row[hidx["WEEK"]] if "WEEK" in hidx else None
            if wk in (None, ""):
                continue
            vals = {"year": year}
            for src, dst in _MP_TO_DRAFT.items():
                vals[dst] = row[hidx[src]] if src in hidx else None
            pid = str(vals.get("pos_id"))
            vals["gps_x"], vals["gps_y"] = gps.get(pid, (None, None))
            fields = ", ".join(vals.keys())
            marks = ", ".join("?" for _ in vals)
            conn.execute(f"INSERT INTO draft_plans ({fields}) VALUES ({marks})", tuple(vals.values()))
            n += 1
        conn.commit()
        return n
    finally:
        conn.close()


def _chain_km(points: list[tuple]) -> float:
    """Nearest-neighbour chain length through GPS points (supportive estimate)."""
    pts = [(x, y) for x, y in points if x is not None and y is not None]
    if len(pts) < 2:
        return 0.0
    remaining = pts[1:]
    cur = pts[0]
    total = 0.0
    while remaining:
        nxt = min(remaining, key=lambda p: distance_km(cur[0], cur[1], p[0], p[1]))
        total += distance_km(cur[0], cur[1], nxt[0], nxt[1])
        cur = nxt
        remaining.remove(nxt)
    return round(total, 1)


def planned_technicians() -> list[dict]:
    rows = db.get("SELECT technician, COUNT(*) AS visits, MIN(week) AS wk_from, "
                  "MAX(week) AS wk_to FROM draft_plans WHERE technician IS NOT NULL "
                  "GROUP BY technician ORDER BY technician")
    return [dict(r) for r in rows]


def technician_route(technician: str, week_from: int | None = None,
                     week_to: int | None = None) -> dict:
    """Per-technician plan grouped week -> day, with supportive day km."""
    q = "SELECT week, day, plan_date, pos_id, name, city, category, ppt, reason, " \
        "day_group, gps_x, gps_y FROM draft_plans WHERE technician=?"
    params: list = [technician]
    if week_from is not None:
        q += " AND week >= ?"; params.append(week_from)
    if week_to is not None:
        q += " AND week <= ?"; params.append(week_to)
    rows = [dict(r) for r in db.get(q, tuple(params))]

    weeks: dict = {}
    for r in rows:
        wk = weeks.setdefault(r["week"], {})
        wk.setdefault(r["day"], []).append(r)

    out_weeks = []
    for wk in sorted(weeks):
        days = []
        for day in sorted(weeks[wk], key=lambda d: _DAY_ORDER.get(d, 9)):
            stops = weeks[wk][day]
            day_km = _chain_km([(s["gps_x"], s["gps_y"]) for s in stops])
            days.append({"day": day, "date": stops[0].get("plan_date"),
                         "stops": stops, "count": len(stops), "supportive_km": day_km})
        out_weeks.append({"week": wk, "days": days,
                          "visits": sum(d["count"] for d in days),
                          "supportive_km": round(sum(d["supportive_km"] for d in days), 1)})
    return {"technician": technician,
            "totalVisits": sum(w["visits"] for w in out_weeks),
            "weeks": out_weeks}
