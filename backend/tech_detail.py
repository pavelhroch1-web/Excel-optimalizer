"""Deep technician profile — everything about one person's work in one place.

Composes the existing analytics (Health Score, cause diagnosis, TourPlan
fulfilment, real driven days, trends) into a single manager-facing profile, plus
two things that need the plan + the route together:

  * missed planned POS the technician DROVE PAST (planned that week, not visited,
    yet the actual route came within a few km) - a concrete "neodvedená práce"
    signal, quantified;
  * a per-day view: real route vs. an optimal ordering, a timeline of the day
    (on-POS / travel / idle), and the estimated time/km that better ordering
    would save.

Read-only over SalesApp + the uploaded plan. No engine change.
"""
from __future__ import annotations

import datetime

import db
import route_actual
import travel_model
from desktop_client.engines.core_logic import GeoPoint, compute_optimal_route_km, distance_km

_DROVE_PAST_KM = 3.0     # planned-but-skipped POS this close to the real route


def _role(name: str) -> str:
    r = db.get("SELECT role FROM technicians WHERE name=?", (name,))
    return (r[0]["role"] if r else "TECHNIK") or "TECHNIK"


def _published_week_range():
    r = db.get("SELECT MIN(week) a, MAX(week) b FROM published_plans")
    return (r[0]["a"], r[0]["b"]) if r and r[0]["a"] is not None else (None, None)


def missed_past_pos(name: str) -> dict:
    """Planned POS the technician did NOT visit in the planned week, yet drove
    within a few km of during that week - avoidable misses, with examples."""
    wa, wb = _published_week_range()
    if wa is None:
        return {"count": 0, "examples": [], "hasPlan": False}
    planned = db.get(
        "SELECT pp.week wk, pp.pos_id pos, COALESCE(pp.name, p.name) nm, "
        "COALESCE(pp.city, p.city) city, p.gps_x gx, p.gps_y gy "
        "FROM published_plans pp "
        "JOIN plan_lifecycle pl ON pl.week=pp.week AND pl.snapshot_id=pp.snapshot_id AND pl.status='Published' "
        "LEFT JOIN pos_master p ON p.pos_id=pp.pos_id "
        "WHERE pp.technician=? AND p.gps_x IS NOT NULL", (name,))
    if not planned:
        return {"count": 0, "examples": [], "hasPlan": True}
    # actual visits by this technician, grouped by ISO week, with GPS
    acts = db.get(
        "SELECT v.pos_id pos, v.visit_date d, p.gps_x gx, p.gps_y gy "
        "FROM salesapp_visits v LEFT JOIN pos_master p ON p.pos_id=v.pos_id "
        "WHERE v.technician=? AND v.visit_date IS NOT NULL AND p.gps_x IS NOT NULL", (name,))
    from collections import defaultdict
    visited_by_week = defaultdict(set)
    route_by_week = defaultdict(list)
    for a in acts:
        wk = _iso_week(a["d"])
        if wk is None:
            continue
        visited_by_week[wk].add(str(a["pos"]))
        route_by_week[wk].append((a["gx"], a["gy"]))
    misses = []
    for pl in planned:
        wk = pl["wk"]
        if str(pl["pos"]) in visited_by_week.get(wk, set()):
            continue  # was visited
        route = route_by_week.get(wk, [])
        if not route:
            continue
        nearest = min(distance_km(pl["gx"], pl["gy"], rx, ry) for rx, ry in route)
        if nearest <= _DROVE_PAST_KM:
            misses.append({"week": wk, "pos": str(pl["pos"]), "name": pl["nm"],
                           "city": pl["city"], "nearestKm": round(nearest, 1)})
    misses.sort(key=lambda m: m["nearestKm"])
    return {"count": len(misses), "examples": misses[:8], "hasPlan": True}


def _iso_week(date_str):
    try:
        return datetime.date.fromisoformat(str(date_str)[:10]).isocalendar()[1]
    except (ValueError, TypeError):
        return None


def profile(name: str, days_back: int = 120) -> dict:
    """One-call composite profile for the technician detail screen."""
    import diagnostics
    import team_analytics
    role = _role(name)

    # KPIs from the team sweep (role-correct)
    ov = team_analytics.overview(days_back=days_back, role=role)
    kpi = next((t for t in ov.get("technicians", []) if t["technician"] == name), None)

    # Health Score + why (find this person in the role's ranking)
    hs = diagnostics.health_scores(days_back, role)
    health = next((t for t in hs.get("technicians", []) if t["technician"] == name), None)

    # Cause diagnosis (route profile, causes, opportunity, lost hours, narrative)
    diag = diagnostics.diagnose(name, days_back) if role == "TECHNIK" else None

    # TourPlan fulfilment for this technician
    fulfil = None
    wa, wb = _published_week_range()
    if wa is not None:
        import plan_reality
        f = plan_reality.fulfillment(int(wa), int(wb))
        fulfil = next((t for t in f.get("perTechnician", []) if t["technician"] == name), None)

    # Real driven days (summaries for the "Dny" tab)
    end = datetime.date.today()
    start = end - datetime.timedelta(days=days_back)
    route = route_actual.technician_route(name, start.isoformat(), end.isoformat())
    days = [{"date": d["date"], "stops": len(d.get("stops", [])),
             "km": d.get("totalKm"), "travelMin": d.get("travelMin"),
             "onPosMin": d.get("onPosMin"), "workHours": d.get("workHours"),
             "workStart": d.get("workStart"), "workEnd": d.get("workEnd")}
            for d in route.get("days", []) if d.get("stops")]
    days.sort(key=lambda d: d["date"], reverse=True)

    return {
        "technician": name, "role": role, "daysBack": days_back,
        "kpi": kpi, "health": health, "diagnosis": diag,
        "fulfilment": fulfil, "missedPast": missed_past_pos(name),
        "days": days, "daysWorked": len(days),
    }


def day(name: str, date: str) -> dict:
    """One technician-day: the real route, an optimal ordering to compare, a
    timeline (on-POS / travel / idle), and the time/km better ordering saves."""
    route = route_actual.technician_route(name, date, date)
    d = next((x for x in route.get("days", []) if x["date"] == date and x.get("stops")), None)
    if not d:
        return {"technician": name, "date": date, "found": False}
    stops = d["stops"]
    pts = [GeoPoint(s["lat"], s["lon"]) for s in stops if s.get("lat") is not None and s.get("lon") is not None]

    optimal = None
    if len(pts) >= 2:
        actual_legs = [distance_km(pts[i].x, pts[i].y, pts[i + 1].x, pts[i + 1].y) for i in range(len(pts) - 1)]
        import diagnostics
        order = diagnostics._nn_order(pts)
        opt_legs = [distance_km(pts[order[i]].x, pts[order[i]].y, pts[order[i + 1]].x, pts[order[i + 1]].y)
                    for i in range(len(order) - 1)]
        actual_km = round(sum(actual_legs), 1)
        opt_km = round(compute_optimal_route_km(pts), 1)
        optimal = {
            "order": order, "optimalKm": opt_km, "actualKm": actual_km,
            "savedKm": round(actual_km - opt_km, 1),
            "actualTravelMin": travel_model.minutes_for_legs(actual_legs),
            "optimalTravelMin": travel_model.minutes_for_legs(opt_legs),
            "savedMin": round(travel_model.minutes_for_legs(actual_legs) - travel_model.minutes_for_legs(opt_legs)),
        }

    # timeline: for each stop, on-POS minutes; between stops, travel (measured)
    timeline = []
    for i, s in enumerate(stops):
        timeline.append({"seq": s["seq"], "pos": s["pos"], "name": s.get("name"),
                         "started": s.get("started"), "finished": s.get("finished"),
                         "onPosMin": s.get("onPosMin"),
                         "lat": s.get("lat"), "lon": s.get("lon")})
    return {
        "technician": name, "date": date, "found": True,
        "stops": timeline, "legs": d.get("legs", []),
        "totalKm": d.get("totalKm"), "travelMin": d.get("travelMin"),
        "onPosMin": d.get("onPosMin"), "workHours": d.get("workHours"),
        "workStart": d.get("workStart"), "workEnd": d.get("workEnd"),
        "optimal": optimal,
    }
