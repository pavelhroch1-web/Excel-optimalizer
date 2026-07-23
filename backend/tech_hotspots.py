"""Technician hotspots — the plain-language "WHERE" behind a low score.

A manager who sees "low productivity / inefficient routes" needs the next click
to answer *where exactly*:

  • longStops   — the specific POS where the technician spends far more time than
                  the learned norm for that kind of store (actual vs expected
                  minutes, how many visits, total minutes lost). Each carries GPS
                  so the UI can pin it on the map.
  • detourDays  — the specific work days whose real driving is much longer than
                  an efficient ordering of the same stops (actual km vs optimal,
                  extra km / %). Each opens that day's route on the map.

Deterministic, offline (straight-line geometry + the learned duration model —
no OSRM, so it is instant inside the portable .exe). Read-only over SQLite.
"""
from __future__ import annotations

import datetime

import db  # noqa: F401  (kept for parity / future queries)
import diagnostics
import duration
import route_actual
from desktop_client.engines.core_logic import GeoPoint, distance_km

_MIN_OVER_MIN = 3.0      # ignore POS within 3 min of the norm (noise)
_MIN_EXTRA_KM = 5.0      # ignore days within 5 km of optimal (noise)
_TOP = 15


def _optimal_km(pts) -> float:
    """Straight-line length of a nearest-neighbour ordering of the day's stops —
    the same 'optimum' estimate the day view already shows, computed offline."""
    if len(pts) < 2:
        return 0.0
    gp = [GeoPoint(a, b) for a, b in pts]
    order = diagnostics._nn_order(gp)
    total = 0.0
    for i in range(len(order) - 1):
        a, b = gp[order[i]], gp[order[i + 1]]
        total += distance_km(a.x, a.y, b.x, b.y)
    return round(total, 1)


def hotspots(name: str, days_back: int = 90) -> dict:
    end = datetime.date.today()
    start = end - datetime.timedelta(days=days_back)
    data = route_actual.technician_route(name, start.isoformat(), end.isoformat())
    days = data.get("days", [])

    exp_cache: dict = {}

    def expected(pos_id):
        if pos_id not in exp_cache:
            exp_cache[pos_id] = (duration.predict(pos_id) or {}).get("p50")
        return exp_cache[pos_id]

    # ---- WHERE too long: aggregate real on-POS time per POS vs the norm ----
    agg: dict = {}
    for d in days:
        for s in d.get("stops", []):
            if s.get("kind") != "pos" or s.get("onPosMin") is None:
                continue
            pid = s["pos"]
            a = agg.setdefault(pid, {"pos": pid, "name": s.get("name"), "city": s.get("city"),
                                     "lat": s.get("lat"), "lon": s.get("lon"),
                                     "visits": 0, "sumActual": 0.0, "lastDate": None})
            a["visits"] += 1
            a["sumActual"] += s["onPosMin"]
            day = d["date"]
            if a["lastDate"] is None or day > a["lastDate"]:
                a["lastDate"] = day

    long_stops = []
    total_lost_min = 0.0
    for a in agg.values():
        exp = expected(a["pos"])
        if not exp or a["visits"] == 0:
            continue
        avg = a["sumActual"] / a["visits"]
        over = avg - exp
        if over <= _MIN_OVER_MIN:
            continue
        total_over = over * a["visits"]
        total_lost_min += total_over
        long_stops.append({
            "pos": a["pos"], "name": a["name"], "city": a["city"],
            "lat": a["lat"], "lon": a["lon"], "visits": a["visits"],
            "avgActualMin": round(avg, 1), "expectedMin": round(exp, 1),
            "overMinPerVisit": round(over, 1), "totalOverMin": round(total_over),
            "lastDate": a["lastDate"],
        })
    long_stops.sort(key=lambda x: -x["totalOverMin"])

    # ---- WHERE inefficient: actual driving vs an efficient ordering, per day ----
    detour_days = []
    total_extra_km = 0.0
    for d in days:
        pts = [(s["lat"], s["lon"]) for s in d.get("stops", [])
               if s.get("kind") == "pos" and s.get("lat") is not None]
        if len(pts) < 3:
            continue
        actual_km = d.get("totalKm") or 0.0
        opt_km = _optimal_km(pts)
        extra = round(actual_km - opt_km, 1)
        if extra <= _MIN_EXTRA_KM:
            continue
        total_extra_km += extra
        detour_days.append({
            "date": d["date"], "stops": len(pts),
            "actualKm": round(actual_km, 1), "optimalKm": opt_km, "extraKm": extra,
            "extraPct": round(100 * extra / opt_km) if opt_km else None,
            "workHours": d.get("workHours"),
        })
    detour_days.sort(key=lambda x: -x["extraKm"])

    return {
        "technician": name, "daysBack": days_back,
        "from": start.isoformat(), "to": end.isoformat(),
        "daysWorked": len(days),
        "longStops": long_stops[:_TOP],
        "longStopsCount": len(long_stops),
        "totalLostMinutes": round(total_lost_min),
        "detourDays": detour_days[:_TOP],
        "detourDaysCount": len(detour_days),
        "totalExtraKm": round(total_extra_km, 1),
    }
