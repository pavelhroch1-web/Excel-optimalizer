"""Technician hotspots — the plain-language "WHERE" behind a low score.

A manager who sees "low productivity / inefficient routes" needs the next click
to answer *where exactly*:

  • longStops   — the specific POS where the technician spends far more time than
                  the COLLECTIVE norm for that partner/store type. The norm is
                  learned across everyone (e.g. "at GECO everyone stays ~2 min"),
                  deliberately EXCLUDING the technician's own history, so his own
                  inflation cannot hide inside his own average.
  • slowTravel  — approaches to a POS where the real driving time is far over the
                  learned transition norm for that distance (e.g. "should be
                  ~20 min, he drove 2 h"). Recurring ones are flagged loud.
  • detourDays  — the specific work days whose real driving is much longer than
                  an efficient ordering of the same stops (actual km vs optimal).

All norms are the collective learned standard (duration model at partner level,
transition model per km-band) — the same references the TourPlan plans against.
Deterministic, offline (straight-line geometry, no OSRM). Read-only over SQLite.
"""
from __future__ import annotations

import datetime

import db
import diagnostics
import duration
import route_actual
import transition_model
from desktop_client.engines.core_logic import GeoPoint, distance_km

_MIN_OVER_MIN = 3.0      # ignore POS within 3 min of the norm (noise)
_MIN_EXTRA_KM = 5.0      # ignore days within 5 km of optimal (noise)
_SLOW_MIN_EXTRA = 20.0   # a leg is "slow" only if >=20 min over the norm …
_SLOW_RATIO = 1.8        # … and at least 1.8× the norm
_SCREAM_COUNT = 3        # recurring (>= this many times) -> loud alert
_TOP = 15


def _pos_attr() -> dict:
    """pos_id -> (category, market/partner) for the collective norm lookup."""
    return {str(r["pos_id"]): (r["category"], r["market"])
            for r in db.get("SELECT pos_id, category, market FROM pos_master")}


def _optimal_km(pts) -> float:
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
    attr = _pos_attr()

    # Collective (partner-level) duration norm — learned across EVERYONE, not this
    # technician, so his own long stops can't define his own "normal".
    norm_cache: dict = {}

    def collective_norm(pos_id):
        cat, market = attr.get(str(pos_id), (None, None))
        key = (cat, market)
        if key not in norm_cache:
            pred = duration.predict_for(cat, market, None, None)  # partner level, no technician
            norm_cache[key] = (pred.get("p50"), pred.get("levelName"))
        return norm_cache[key]

    # ---- WHERE too long on POS: real on-POS time vs the collective partner norm --
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
            if a["lastDate"] is None or d["date"] > a["lastDate"]:
                a["lastDate"] = d["date"]

    long_stops = []
    total_lost_min = 0.0
    for a in agg.values():
        exp, level = collective_norm(a["pos"])
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
            "normLevel": level, "overMinPerVisit": round(over, 1),
            "totalOverMin": round(total_over), "lastDate": a["lastDate"],
        })
    long_stops.sort(key=lambda x: -x["totalOverMin"])

    # ---- WHERE the drive itself is absurd: real leg time vs the transition norm --
    # Aggregate by destination POS so a recurring slow approach screams.
    slow: dict = {}
    for d in days:
        seq_stop = {s["seq"]: s for s in d.get("stops", [])}
        for leg in d.get("legs", []):
            actual = leg.get("travelMin")
            km = leg.get("km")
            if actual is None or km is None:
                continue
            norm = (transition_model.predict(km) or {}).get("minutes")
            if not norm:
                continue
            extra = actual - norm
            if extra < _SLOW_MIN_EXTRA or actual < norm * _SLOW_RATIO:
                continue
            dest = seq_stop.get(leg.get("toSeq"))
            if not dest or dest.get("kind") != "pos":
                continue
            pid = dest["pos"]
            a = slow.setdefault(pid, {"pos": pid, "name": dest.get("name"), "city": dest.get("city"),
                                      "lat": dest.get("lat"), "lon": dest.get("lon"),
                                      "count": 0, "sumExtra": 0.0, "worstActual": 0.0,
                                      "worstNorm": 0.0, "worstKm": 0.0, "worstDate": None})
            a["count"] += 1
            a["sumExtra"] += extra
            if actual > a["worstActual"]:
                a["worstActual"] = actual; a["worstNorm"] = norm
                a["worstKm"] = km; a["worstDate"] = d["date"]
    slow_travel = []
    total_slow_min = 0.0
    for a in slow.values():
        total_slow_min += a["sumExtra"]
        slow_travel.append({
            "pos": a["pos"], "name": a["name"], "city": a["city"],
            "lat": a["lat"], "lon": a["lon"], "count": a["count"],
            "totalExtraMin": round(a["sumExtra"]), "worstActualMin": round(a["worstActual"]),
            "normMin": round(a["worstNorm"]), "km": round(a["worstKm"], 1),
            "worstDate": a["worstDate"], "recurring": a["count"] >= _SCREAM_COUNT,
        })
    slow_travel.sort(key=lambda x: (-int(x["recurring"]), -x["totalExtraMin"]))

    # ---- WHERE inefficient overall: actual driving vs an efficient ordering ------
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
        "from": start.isoformat(), "to": end.isoformat(), "daysWorked": len(days),
        "longStops": long_stops[:_TOP], "longStopsCount": len(long_stops),
        "totalLostMinutes": round(total_lost_min),
        "slowTravel": slow_travel[:_TOP], "slowTravelCount": len(slow_travel),
        "slowTravelRecurring": sum(1 for x in slow_travel if x["recurring"]),
        "totalSlowMinutes": round(total_slow_min),
        "detourDays": detour_days[:_TOP], "detourDaysCount": len(detour_days),
        "totalExtraKm": round(total_extra_km, 1),
    }
