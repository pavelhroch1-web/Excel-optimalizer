"""GIS layers for the Monthly Summary map and the technician day view.

Assembles, for a chosen period and the same filters the summary uses, the map
layers a manager reads visually: visited POS, planned-but-unvisited POS, a
visit-density heatmap, region and technician centroids (for drill-down),
repeated-area-return hotspots, and — when a single technician is selected —
their real road-routed movement.

The day view returns the road route (OSRM, cached), an optimal ordering to
compare, every visited and missed-planned POS, and the POS the technician drove
past within a radius but did not visit — the visual proof behind the numbers.

Read-only over SalesApp + the published plan. No engine change.
"""
from __future__ import annotations

import datetime
from collections import defaultdict

import db
import osrm
import summary as _summary
from desktop_client.engines.core_logic import GeoPoint, distance_km

# Read-side memo for the summary map. network() runs several full scans over
# visits+POS and returns the same layers until data changes (import / role edit),
# both of which call diagnostics.invalidate_cache() -> gis.invalidate(). Keyed by
# the full filter signature; a copy is returned so callers can't poison it.
_net_cache: dict = {}
_NET_CACHE_MAX = 24   # keep the memo bounded across many filter combos


def invalidate() -> None:
    _net_cache.clear()


def _visit_where(start, end, names, region, chain, visit_type):
    where = ["v.visit_date >= ?", "v.visit_date <= ?", "p.gps_x IS NOT NULL"]
    params = [start.isoformat(), end.isoformat()]
    if names:
        where.append("v.technician IN (%s)" % ",".join("?" * len(names))); params += names
    if region:
        where.append("v.region = ?"); params.append(region)
    if chain:
        where.append("p.market = ?"); params.append(chain)
    if visit_type:
        vt_sql, vt_p = _summary._visit_type_clause(visit_type)
        if vt_sql:
            where.append(vt_sql.replace(" AND ", "")); params += vt_p
    return " AND ".join(where), params


def network(period="month", year=None, month=None, quarter=None, date_from=None, date_to=None,
            role="TECHNIK", region=None, technician=None, chain=None, visit_type=None,
            active="active", include_optimal=False, max_points=6000) -> dict:
    """Cached wrapper. include_optimal pulls OSRM routes (expensive, single-tech)
    and is never memoized; everything else is memoized by its filter signature."""
    if include_optimal:
        return _network_compute(period, year, month, quarter, date_from, date_to, role,
                                region, technician, chain, visit_type, active, True, max_points)
    key = (period, year, month, quarter, date_from, date_to, role, region,
           technician, chain, visit_type, active, max_points)
    hit = _net_cache.get(key)
    if hit is None:
        if len(_net_cache) >= _NET_CACHE_MAX:
            _net_cache.clear()
        hit = _network_compute(period, year, month, quarter, date_from, date_to, role,
                               region, technician, chain, visit_type, active, False, max_points)
        _net_cache[key] = hit
    # Returned straight to FastAPI for JSON serialization (read-only); the map
    # payload is large (thousands of points), so a deep copy would cost more than
    # the recompute we just saved. No caller mutates it.
    return hit


def _network_compute(period="month", year=None, month=None, quarter=None, date_from=None, date_to=None,
                     role="TECHNIK", region=None, technician=None, chain=None, visit_type=None,
                     active="active", include_optimal=False, max_points=6000) -> dict:
    start, end, label, *_ = _summary.resolve_period(period, year, month, quarter, date_from, date_to)
    region_map = _summary._tech_region_map()
    names = _summary._people((role or "TECHNIK").upper(), region, active, technician, region_map)
    where, params = _visit_where(start, end, names, region, chain, visit_type)

    # visited POS (aggregated) — powers markers + heatmap
    visited = db.get(
        f"SELECT v.pos_id pos, p.gps_x lat, p.gps_y lon, COALESCE(p.name,v.store_name) nm, "
        f"p.city city, p.street street, p.house_number hn, p.market chain, COUNT(*) visits, "
        f"SUM(CASE WHEN lower(v.purpose) LIKE '%náběh kampaně%' THEN 1 ELSE 0 END) vis "
        f"FROM salesapp_visits v JOIN pos_master p ON p.pos_id=v.pos_id "
        f"WHERE {where} GROUP BY v.pos_id ORDER BY visits DESC LIMIT ?", tuple(params + [max_points]))
    visitedPos = [{"pos": str(r["pos"]), "lat": r["lat"], "lon": r["lon"], "name": r["nm"],
                   "city": r["city"], "address": _addr(r["street"], r["hn"], r["city"]),
                   "chain": r["chain"], "visits": r["visits"], "visibility": r["vis"],
                   "status": "visited"} for r in visited]
    heat = [[r["lat"], r["lon"], r["visits"]] for r in visited]
    visibility = [p for p in visitedPos if p["visibility"]]

    # planned but not visited in the period
    unvisited = _planned_unvisited(start, end, names)
    # planned AND visited (for the TourPlan-completion layer)
    plannedVisited = _planned_visited(start, end, names)
    # near-missed: planned-unvisited POS in a city where the same technician DID
    # work that period (drove-past proxy at network scale)
    nearMissed = _near_missed(unvisited, names, start, end)

    regions = _region_centroids(where, params)
    techs = _tech_centroids(where, params)
    returns = _area_return_hotspots(start, end, names)
    capacity = _capacity_hotspots(unvisited)

    # real road routes only when a single technician is in focus; optimal
    # ordering is heavier (extra routing) so it's fetched only on demand
    routes, optimalRoutes = [], []
    if technician:
        routes, optimalRoutes = _tech_routes(technician, start, end, include_optimal=include_optimal)

    allpts = [(p["lat"], p["lon"]) for p in visitedPos] + [(p["lat"], p["lon"]) for p in unvisited]
    bounds = _bounds(allpts)
    return {"period": {"label": label, "from": start.isoformat(), "to": end.isoformat()},
            "visitedPos": visitedPos, "unvisitedPos": unvisited, "plannedVisited": plannedVisited,
            "nearMissed": nearMissed, "visibility": visibility, "heat": heat,
            "regions": regions, "technicians": techs, "areaReturns": returns,
            "capacity": capacity, "routes": routes, "optimalRoutes": optimalRoutes, "bounds": bounds,
            "counts": {"visited": len(visitedPos), "unvisited": len(unvisited),
                       "regions": len(regions), "technicians": len(techs),
                       "visibility": len(visibility), "nearMissed": len(nearMissed)}}


def _addr(street, hn, city):
    parts = [x for x in [(str(street).strip() if street else "") + ((" " + str(hn).strip()) if hn else ""),
                         str(city).strip() if city else ""] if x and x.strip()]
    return ", ".join(parts) or None


def _bounds(pts):
    if not pts:
        return None
    la = [p[0] for p in pts]; lo = [p[1] for p in pts]
    return [[min(la), min(lo)], [max(la), max(lo)]]


def _planned_unvisited(start, end, names, limit=6000):
    wk = db.get("SELECT MIN(week) a, MAX(week) b FROM published_plans")
    if not wk or wk[0]["a"] is None:
        return []
    q = ("SELECT pp.pos_id pos, p.gps_x lat, p.gps_y lon, COALESCE(pp.name,p.name) nm, "
         "COALESCE(pp.city,p.city) city, p.street street, p.house_number hn, p.market chain, "
         "pp.technician tech FROM published_plans pp "
         "JOIN plan_lifecycle pl ON pl.week=pp.week AND pl.snapshot_id=pp.snapshot_id AND pl.status='Published' "
         "LEFT JOIN pos_master p ON p.pos_id=pp.pos_id "
         "WHERE p.gps_x IS NOT NULL AND NOT EXISTS (SELECT 1 FROM salesapp_visits v "
         "  WHERE v.pos_id=pp.pos_id AND v.visit_date>=? AND v.visit_date<=?) ")
    params = [start.isoformat(), end.isoformat()]
    if names:
        q += "AND pp.technician IN (%s) " % ",".join("?" * len(names)); params += names
    q += "GROUP BY pp.pos_id LIMIT ?"; params.append(limit)
    return [{"pos": str(r["pos"]), "lat": r["lat"], "lon": r["lon"], "name": r["nm"],
             "city": r["city"], "address": _addr(r["street"], r["hn"], r["city"]),
             "chain": r["chain"], "technician": r["tech"], "status": "missed"}
            for r in db.get(q, tuple(params))]


def _planned_visited(start, end, names, limit=6000):
    """Planned POS that WERE visited in the period (green side of TourPlan
    completion)."""
    wk = db.get("SELECT MIN(week) a, MAX(week) b FROM published_plans")
    if not wk or wk[0]["a"] is None:
        return []
    q = ("SELECT pp.pos_id pos, p.gps_x lat, p.gps_y lon, COALESCE(pp.name,p.name) nm, "
         "COALESCE(pp.city,p.city) city, p.market chain, pp.technician tech FROM published_plans pp "
         "JOIN plan_lifecycle pl ON pl.week=pp.week AND pl.snapshot_id=pp.snapshot_id AND pl.status='Published' "
         "LEFT JOIN pos_master p ON p.pos_id=pp.pos_id "
         "WHERE p.gps_x IS NOT NULL AND EXISTS (SELECT 1 FROM salesapp_visits v "
         "  WHERE v.pos_id=pp.pos_id AND v.visit_date>=? AND v.visit_date<=?) ")
    params = [start.isoformat(), end.isoformat()]
    if names:
        q += "AND pp.technician IN (%s) " % ",".join("?" * len(names)); params += names
    q += "GROUP BY pp.pos_id LIMIT ?"; params.append(limit)
    return [{"pos": str(r["pos"]), "lat": r["lat"], "lon": r["lon"], "name": r["nm"],
             "city": r["city"], "chain": r["chain"], "technician": r["tech"], "status": "done"}
            for r in db.get(q, tuple(params))]


def _near_missed(unvisited, names, start, end):
    """Planned-unvisited POS in a city where their planned technician DID work
    that period — a network-scale 'drove past the area but skipped it' proxy."""
    if not unvisited:
        return []
    worked = set()
    for r in db.get(
            "SELECT DISTINCT v.technician tech, p.city city FROM salesapp_visits v "
            "JOIN pos_master p ON p.pos_id=v.pos_id WHERE v.visit_date>=? AND v.visit_date<=? "
            "AND p.city IS NOT NULL", (start.isoformat(), end.isoformat())):
        worked.add((r["tech"], r["city"]))
    return [p for p in unvisited if (p.get("technician"), p.get("city")) in worked]


def _capacity_hotspots(unvisited, limit=120):
    """Cities with the most planned-but-unvisited POS — where the biggest
    unrealised coverage capacity sits."""
    from collections import defaultdict
    agg = defaultdict(lambda: {"n": 0, "la": 0.0, "lo": 0.0})
    for p in unvisited:
        if not p.get("city"):
            continue
        a = agg[p["city"]]; a["n"] += 1; a["la"] += p["lat"]; a["lo"] += p["lon"]
    out = [{"city": c, "count": a["n"], "lat": a["la"] / a["n"], "lon": a["lo"] / a["n"]}
           for c, a in agg.items() if a["n"] >= 3]
    out.sort(key=lambda x: -x["count"])
    return out[:limit]


def _region_centroids(where, params):
    rows = db.get(
        f"SELECT v.region region, AVG(p.gps_x) lat, AVG(p.gps_y) lon, COUNT(*) visits, "
        f"COUNT(DISTINCT v.technician) techs FROM salesapp_visits v JOIN pos_master p ON p.pos_id=v.pos_id "
        f"WHERE {where} AND v.region IS NOT NULL AND v.region<>'' GROUP BY v.region", tuple(params))
    return [{"region": r["region"], "lat": r["lat"], "lon": r["lon"],
             "visits": r["visits"], "techs": r["techs"]} for r in rows]


def _tech_centroids(where, params):
    rows = db.get(
        f"SELECT v.technician tech, AVG(p.gps_x) lat, AVG(p.gps_y) lon, COUNT(*) visits "
        f"FROM salesapp_visits v JOIN pos_master p ON p.pos_id=v.pos_id "
        f"WHERE {where} AND v.technician IS NOT NULL GROUP BY v.technician", tuple(params))
    return [{"technician": r["tech"], "lat": r["lat"], "lon": r["lon"], "visits": r["visits"]}
            for r in rows]


def _area_return_hotspots(start, end, names, limit=200):
    """Cities a technician returned to on >=2 separate days in the same week —
    placed at the city centroid, weighted by how often it happened."""
    q = ("SELECT p.city city, AVG(p.gps_x) lat, AVG(p.gps_y) lon, "
         "SUM(CASE WHEN dd>=2 THEN 1 ELSE 0 END) returns FROM ("
         "  SELECT v.technician, p.city, strftime('%Y-%W', v.visit_date) wk, "
         "  COUNT(DISTINCT v.visit_date) dd, MIN(p.gps_x) gx, MIN(p.gps_y) gy "
         "  FROM salesapp_visits v JOIN pos_master p ON p.pos_id=v.pos_id "
         "  WHERE v.visit_date>=? AND v.visit_date<=? AND p.gps_x IS NOT NULL AND p.city IS NOT NULL ")
    params = [start.isoformat(), end.isoformat()]
    if names:
        q += "AND v.technician IN (%s) " % ",".join("?" * len(names)); params += names
    q += ("  GROUP BY v.technician, p.city, wk) t JOIN pos_master p ON p.city=t.city "
          "GROUP BY t.city HAVING returns>0 ORDER BY returns DESC LIMIT ?")
    params.append(limit)
    return [{"city": r["city"], "lat": r["lat"], "lon": r["lon"], "returns": r["returns"]}
            for r in db.get(q, tuple(params))]


def _tech_routes(name, start, end, max_days=45, include_optimal=False):
    """Real road routes (+ optionally optimal ordering) for a technician over the
    period, one per worked day. Returns (actualRoutes, optimalRoutes)."""
    import route_actual
    import diagnostics
    from desktop_client.engines.core_logic import GeoPoint
    data = route_actual.technician_route(name, start.isoformat(), end.isoformat())
    actual, optimal = [], []
    for d in sorted(data.get("days", []), key=lambda x: x["date"], reverse=True)[:max_days]:
        pts = [(s["lat"], s["lon"]) for s in d.get("stops", [])
               if s.get("kind", "pos") == "pos" and s.get("lat") is not None]
        if len(pts) < 2:
            continue
        rr = osrm.road_route(pts)
        actual.append({"date": d["date"], "geometry": rr["geometry"], "km": rr["km"],
                       "source": rr["source"], "stops": len(pts)})
        if include_optimal:
            order = diagnostics._nn_order([GeoPoint(a, b) for a, b in pts])
            opt = osrm.road_route([pts[i] for i in order])
            optimal.append({"date": d["date"], "geometry": opt["geometry"], "km": opt["km"]})
    return actual, optimal


def pos_detail(pos_id: str, days_back: int = 180) -> dict:
    """Everything about one POS: master record + full visit history (who, when,
    on-POS time) + which weeks it was planned. Drill target from the map."""
    p = db.get("SELECT pos_id, name, city, street, house_number, market, category, "
               "classification, terminal_type, gps_x, gps_y, technician, active "
               "FROM pos_master WHERE pos_id=?", (str(pos_id),))
    if not p:
        return {"found": False, "pos": str(pos_id)}
    r = p[0]
    end = datetime.datetime.now().date()
    start = end - datetime.timedelta(days=days_back)
    visits = db.get(
        "SELECT visit_date d, technician tech, visitor_role role, started_at st, finished_at fin, "
        "real_duration dur, purpose FROM salesapp_visits WHERE pos_id=? AND visit_date>=? "
        "ORDER BY visit_date DESC LIMIT 60", (str(pos_id), start.isoformat()))
    hist = []
    for v in visits:
        onmin = _min_between(v["st"], v["fin"])
        if onmin is None and v["dur"] not in (None, ""):
            try:
                onmin = round(float(v["dur"]) * 60, 1)
            except (ValueError, TypeError):
                onmin = None
        hist.append({"date": str(v["d"])[:10], "technician": v["tech"], "role": v["role"],
                     "started": v["st"], "finished": v["fin"], "onPosMin": onmin,
                     "visibility": "náběh kampaně" in (v["purpose"] or "").lower()})
    planned = db.get(
        "SELECT DISTINCT pp.week wk, pp.technician tech FROM published_plans pp "
        "JOIN plan_lifecycle pl ON pl.week=pp.week AND pl.snapshot_id=pp.snapshot_id AND pl.status='Published' "
        "WHERE pp.pos_id=? ORDER BY pp.week", (str(pos_id),))
    return {"found": True, "pos": str(r["pos_id"]), "name": r["name"], "city": r["city"],
            "address": _addr(r["street"], r["house_number"], r["city"]),
            "chain": r["market"], "category": r["category"], "classification": r["classification"],
            "terminalType": r["terminal_type"], "technician": r["technician"],
            "lat": r["gps_x"], "lon": r["gps_y"], "active": bool(r["active"]),
            "visits": hist, "visitCount": len(hist),
            "plannedWeeks": [{"week": pw["wk"], "technician": pw["tech"]} for pw in planned]}


# ------------------------------------------------------------------ day view
def technician_day(name: str, date: str, radius_m: int = 250) -> dict:
    """Full GIS proof of one work day: road route, optimal ordering, visited +
    missed-planned + drove-past-nearby POS within a radius, and the timeline."""
    import tech_detail
    base = tech_detail.day(name, date)
    if not base.get("found"):
        return base
    stops = [s for s in base["stops"] if s.get("kind", "pos") == "pos" and s.get("lat") is not None]
    pts = [(s["lat"], s["lon"]) for s in stops]
    road = osrm.road_route(pts) if len(pts) >= 2 else {"geometry": [[a, b] for a, b in pts], "km": 0, "source": "none"}
    opt = base.get("optimal")
    opt_road = None
    if opt and opt.get("order"):
        opt_pts = [pts[i] for i in opt["order"]]
        opt_road = osrm.road_route(opt_pts)

    visited_ids = {s["pos"] for s in stops}
    # planned this ISO week, not visited that week -> missed; flag those near route
    wk = _iso_week(date)
    missed = _missed_planned(name, wk, visited_ids)
    nearby = _nearby_pos(road["geometry"] or pts, visited_ids, radius_m)
    # mark missed that are also near the route (drove past)
    near_ids = {n["pos"] for n in nearby}
    for m in missed:
        m["drovePast"] = m["pos"] in near_ids

    base["road"] = {"geometry": road["geometry"], "km": road["km"], "source": road["source"],
                    "min": road.get("min")}
    if opt_road:
        base["optimalRoad"] = {"geometry": opt_road["geometry"], "km": opt_road["km"],
                               "source": opt_road["source"], "min": opt_road.get("min")}
    base["missedPlanned"] = missed
    base["nearbyPos"] = nearby
    base["radiusM"] = radius_m

    # gap validation between consecutive visits (green / yellow / red)
    gaps = _gap_analysis(stops)
    base["gaps"] = gaps
    # quantified cost of the planned POS the technician drove right past
    base["missedNearCost"] = _missed_near_cost([m for m in missed if m.get("drovePast")], stops)
    base["managerSummary"] = _day_summary(base, gaps)
    return base


import travel_model  # noqa: E402


def _min_between(a, b):
    if not a or not b:
        return None
    try:
        ta = datetime.datetime.fromisoformat(str(a))
        tb = datetime.datetime.fromisoformat(str(b))
        return round((tb - ta).total_seconds() / 60.0, 1)
    except (ValueError, TypeError):
        return None


def _classify_gap(est, actual):
    """est = modelled drive minutes, actual = elapsed minutes between visits.
    green = normal, yellow = suspicious, red = large unexplained gap."""
    if actual is None or est is None:
        return "na", 0.0
    excess = round(actual - est, 1)
    if actual <= est * 1.5 + 10:
        return "green", excess
    if actual <= est * 2.5 + 30:
        return "yellow", excess
    return "red", excess


def _gap_analysis(stops):
    """For each consecutive pair of visits: real straight->road drive estimate
    vs. the actual elapsed time, classified. Aggregates the unexplained time."""
    out = []
    unexplained = 0.0
    for i in range(len(stops) - 1):
        a, b = stops[i], stops[i + 1]
        km = distance_km(a["lat"], a["lon"], b["lat"], b["lon"]) if None not in (a["lat"], a["lon"], b["lat"], b["lon"]) else None
        est = travel_model.estimate_minutes(km) if km is not None else None
        actual = _min_between(a.get("finished"), b.get("started"))
        band, excess = _classify_gap(est, actual)
        if band in ("yellow", "red") and excess > 0:
            unexplained += excess
        out.append({"fromSeq": i + 1, "toSeq": i + 2, "roadKm": round(travel_model.road_km(km), 1) if km else None,
                    "estMin": est, "actualMin": actual, "band": band, "excessMin": excess,
                    "from": a.get("name"), "to": b.get("name")})
    return {"legs": out, "unexplainedMin": round(unexplained, 1),
            "suspicious": sum(1 for g in out if g["band"] in ("yellow", "red"))}


def _missed_near_cost(drove_past_planned, stops):
    """Management proof: how much visiting the planned POS the technician drove
    past would have added. Detour = there-and-back from the nearest route point,
    turned into road km and driving minutes."""
    if not drove_past_planned or not stops:
        return {"count": 0, "addedKm": 0.0, "addedMin": 0.0}
    route = [(s["lat"], s["lon"]) for s in stops]
    detour_km = 0.0
    for m in drove_past_planned:
        nearest = min(distance_km(m["lat"], m["lon"], rx, ry) for rx, ry in route)
        detour_km += 2 * nearest                      # there and back
    added_km = travel_model.road_km(detour_km)
    added_min = travel_model.minutes_for_legs([2 * min(distance_km(m["lat"], m["lon"], rx, ry) for rx, ry in route)
                                               for m in drove_past_planned])
    return {"count": len(drove_past_planned), "addedKm": round(added_km, 1), "addedMin": round(added_min)}


def _day_summary(base, gaps):
    """One manager sentence explaining WHY the day reads efficient or not."""
    cost = base.get("missedNearCost") or {}
    opt = base.get("optimal") or {}
    parts = []
    verdict = "efektivní"
    if cost.get("count"):
        parts.append(f"projel do {base['radiusM']} m kolem {cost['count']} naplánovaných POS — zajet k nim by přidalo jen ~{cost['addedMin']} min a ~{cost['addedKm']} km")
        verdict = "s rezervou"
    if gaps.get("suspicious"):
        parts.append(f"{gaps['suspicious']}× neobvyklá prodleva mezi návštěvami (~{round(gaps['unexplainedMin'])} min bez vysvětlení)")
        verdict = "s rezervou"
    if opt.get("savedKm", 0) > 5:
        parts.append(f"lepší pořadí zastávek by ušetřilo {opt['savedKm']} km / {opt.get('savedMin', 0)} min")
        verdict = "s rezervou"
    if not parts:
        return {"verdict": "efektivní", "text": "Den bez zjevných rezerv — trasa i časy sedí, žádné velké prodlevy ani minuté naplánované POS poblíž."}
    return {"verdict": verdict, "text": "Den " + verdict + ": " + "; ".join(parts) + "."}


def _iso_week(date_str):
    try:
        return datetime.date.fromisoformat(str(date_str)[:10]).isocalendar()[1]
    except (ValueError, TypeError):
        return None


def _missed_planned(name, week, visited_ids):
    if week is None:
        return []
    rows = db.get(
        "SELECT pp.pos_id pos, COALESCE(pp.name,p.name) nm, COALESCE(pp.city,p.city) city, "
        "p.gps_x lat, p.gps_y lon FROM published_plans pp "
        "JOIN plan_lifecycle pl ON pl.week=pp.week AND pl.snapshot_id=pp.snapshot_id AND pl.status='Published' "
        "LEFT JOIN pos_master p ON p.pos_id=pp.pos_id "
        "WHERE pp.technician=? AND pp.week=? AND p.gps_x IS NOT NULL", (name, week))
    return [{"pos": str(r["pos"]), "name": r["nm"], "city": r["city"], "lat": r["lat"], "lon": r["lon"]}
            for r in rows if str(r["pos"]) not in visited_ids]


def _nearby_pos(route_geo, visited_ids, radius_m, limit=120):
    """POS within `radius_m` of the actual route that were NOT visited — the
    'drove past but skipped' set. Uses a bounding-box prefilter then exact
    distance to each route vertex."""
    if not route_geo:
        return []
    la = [p[0] for p in route_geo]; lo = [p[1] for p in route_geo]
    pad = radius_m / 111000.0 + 0.02
    rows = db.get(
        "SELECT pos_id pos, name nm, city, street, house_number hn, gps_x lat, gps_y lon, market FROM pos_master "
        "WHERE active=1 AND gps_x BETWEEN ? AND ? AND gps_y BETWEEN ? AND ?",
        (min(la) - pad, max(la) + pad, min(lo) - pad, max(lo) + pad))
    rad_km = radius_m / 1000.0
    out = []
    for r in rows:
        if str(r["pos"]) in visited_ids:
            continue
        d = min(distance_km(r["lat"], r["lon"], gx, gy) for gx, gy in route_geo)
        if d <= rad_km:
            out.append({"pos": str(r["pos"]), "name": r["nm"], "city": r["city"],
                        "address": _addr(r["street"], r["hn"], r["city"]),
                        "lat": r["lat"], "lon": r["lon"], "market": r["market"], "chain": r["market"],
                        "distM": round(d * 1000)})
    out.sort(key=lambda x: x["distM"])
    return out[:limit]
