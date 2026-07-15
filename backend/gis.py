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
            active="active", max_points=6000) -> dict:
    start, end, label, *_ = _summary.resolve_period(period, year, month, quarter, date_from, date_to)
    region_map = _summary._tech_region_map()
    names = _summary._people((role or "TECHNIK").upper(), region, active, technician, region_map)
    where, params = _visit_where(start, end, names, region, chain, visit_type)

    # visited POS (aggregated) — powers markers + heatmap
    visited = db.get(
        f"SELECT v.pos_id pos, p.gps_x lat, p.gps_y lon, COALESCE(p.name,v.store_name) nm, "
        f"p.city city, COUNT(*) visits FROM salesapp_visits v JOIN pos_master p ON p.pos_id=v.pos_id "
        f"WHERE {where} GROUP BY v.pos_id ORDER BY visits DESC LIMIT ?", tuple(params + [max_points]))
    visitedPos = [{"pos": str(r["pos"]), "lat": r["lat"], "lon": r["lon"], "name": r["nm"],
                   "city": r["city"], "visits": r["visits"]} for r in visited]
    heat = [[r["lat"], r["lon"], r["visits"]] for r in visited]

    # planned but not visited in the period
    unvisited = _planned_unvisited(start, end, names)

    # region centroids (click -> filter dashboard)
    regions = _region_centroids(where, params)

    # technician centroids (click -> open detail)
    techs = _tech_centroids(where, params)

    # repeated-area-return hotspots (weighted by how often a tech came back)
    returns = _area_return_hotspots(start, end, names)

    # real road route only when a single technician is in focus (else too much)
    routes = []
    if technician:
        routes = _tech_routes(technician, start, end)

    allpts = [(p["lat"], p["lon"]) for p in visitedPos] + [(p["lat"], p["lon"]) for p in unvisited]
    bounds = _bounds(allpts)
    return {"period": {"label": label, "from": start.isoformat(), "to": end.isoformat()},
            "visitedPos": visitedPos, "unvisitedPos": unvisited, "heat": heat,
            "regions": regions, "technicians": techs, "areaReturns": returns,
            "routes": routes, "bounds": bounds,
            "counts": {"visited": len(visitedPos), "unvisited": len(unvisited),
                       "regions": len(regions), "technicians": len(techs)}}


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
         "COALESCE(pp.city,p.city) city, pp.technician tech FROM published_plans pp "
         "JOIN plan_lifecycle pl ON pl.week=pp.week AND pl.snapshot_id=pp.snapshot_id AND pl.status='Published' "
         "LEFT JOIN pos_master p ON p.pos_id=pp.pos_id "
         "WHERE p.gps_x IS NOT NULL AND NOT EXISTS (SELECT 1 FROM salesapp_visits v "
         "  WHERE v.pos_id=pp.pos_id AND v.visit_date>=? AND v.visit_date<=?) ")
    params = [start.isoformat(), end.isoformat()]
    if names:
        q += "AND pp.technician IN (%s) " % ",".join("?" * len(names)); params += names
    q += "GROUP BY pp.pos_id LIMIT ?"; params.append(limit)
    return [{"pos": str(r["pos"]), "lat": r["lat"], "lon": r["lon"], "name": r["nm"],
             "city": r["city"], "technician": r["tech"]} for r in db.get(q, tuple(params))]


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


def _tech_routes(name, start, end, max_days=45):
    """Real road routes for a technician over the period, one per worked day."""
    import route_actual
    data = route_actual.technician_route(name, start.isoformat(), end.isoformat())
    out = []
    for d in sorted(data.get("days", []), key=lambda x: x["date"], reverse=True)[:max_days]:
        pts = [(s["lat"], s["lon"]) for s in d.get("stops", [])
               if s.get("kind", "pos") == "pos" and s.get("lat") is not None]
        if len(pts) < 2:
            continue
        rr = osrm.road_route(pts)
        out.append({"date": d["date"], "geometry": rr["geometry"], "km": rr["km"],
                    "source": rr["source"], "stops": len(pts)})
    return out


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
    return base


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
        "SELECT pos_id pos, name nm, city, gps_x lat, gps_y lon, market FROM pos_master "
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
                        "lat": r["lat"], "lon": r["lon"], "market": r["market"],
                        "distM": round(d * 1000)})
    out.sort(key=lambda x: x["distM"])
    return out[:limit]
