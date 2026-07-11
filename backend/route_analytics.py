"""Technician route analytics over SalesApp - the analysis layer.

Turns one SalesApp sync into a full picture of a technician's day: metrics (km,
travel time, on-POS time, worked hours, averages, productivity), map layers
(planned / visited / driven-past-not-visited / nearby opportunities along the
route) and route-efficiency findings (long transfers, likely backtracking,
missed due POS on the way). Plus long-term per-day trends.

Read-only over SQLite + the actual driven route (route_actual). No planning,
no engine change. OZ stay informational (a nearby POS an OZ already covered is
flagged, so it is not proposed as a missed opportunity).
"""
from __future__ import annotations

import datetime

import db
import route_actual
import live_plan
from desktop_client.engines.core_logic import distance_km

LONG_LEG_KM = 30.0          # a single transfer longer than this is flagged
DEFAULT_RADIUS_KM = 2.0     # "along the route" proximity for nearby POS


def _iso_week(date_str):
    return live_plan._iso_week(date_str)


def _bbox(points, pad_km):
    lats = [p[0] for p in points]; lons = [p[1] for p in points]
    dlat = pad_km / 111.0
    # crude lon scaling by mid-latitude
    import math
    midlat = sum(lats) / len(lats)
    dlon = pad_km / (111.0 * max(math.cos(math.radians(midlat)), 0.2))
    return min(lats) - dlat, max(lats) + dlat, min(lons) - dlon, max(lons) + dlon


def _productivity(visits, onpos_min, travel_min, work_min):
    total = (onpos_min or 0) + (travel_min or 0)
    on_ratio = round(100 * (onpos_min or 0) / total, 1) if total else None
    per_hour = round(visits / (work_min / 60.0), 2) if work_min else None
    return {"onPosRatioPct": on_ratio, "visitsPerWorkHour": per_hour}


def day(technician: str, date: str, radius_km: float = DEFAULT_RADIUS_KM) -> dict:
    """Full analysis of one technician-day: metrics + map layers + findings."""
    base = route_actual.technician_route(technician, date, date)
    d = base["days"][0] if base["days"] else None
    if not d or not d["stops"]:
        return {"technician": technician, "date": date, "hasData": False,
                "message": "Pro tento den nejsou data o návštěvách."}

    stops = d["stops"]
    stop_pts = [(s["lat"], s["lon"]) for s in stops if s["lat"] is not None and s["lon"] is not None]
    visited_ids = {str(s["pos"]) for s in stops if s["pos"]}

    # metrics
    visits = d["stopCount"]
    onpos, travel, km = d["onPosMin"], d["travelMin"], d["totalKm"]
    work_min = None
    if d.get("workHours") is not None:
        work_min = d["workHours"] * 60.0
    avg_onpos = round(onpos / visits, 1) if visits else None
    legs = d["legs"]
    leg_times = [l["travelMin"] for l in legs if l.get("travelMin")]
    avg_travel = round(sum(leg_times) / len(leg_times), 1) if leg_times else None
    metrics = {
        "visits": visits, "uniquePos": len(visited_ids),
        "totalKm": km, "travelMin": travel, "onPosMin": onpos,
        "workHours": d.get("workHours"), "workStart": d.get("workStart"), "workEnd": d.get("workEnd"),
        "avgOnPosMin": avg_onpos, "avgTravelMin": avg_travel,
        **_productivity(visits, onpos, travel, work_min),
    }

    # ---- map layers ----
    wk = _iso_week(date)
    planned_layer = []
    for r in db.get(
        "SELECT pp.pos_id, pp.name, pp.city, pp.gps_x, pp.gps_y FROM published_plans pp "
        "JOIN plan_lifecycle pl ON pl.week=pp.week AND pl.snapshot_id=pp.snapshot_id "
        "AND pl.status='Published' WHERE pp.technician=? AND pp.week=?", (technician, wk)):
        planned_layer.append({"pos": str(r["pos_id"]), "name": r["name"], "city": r["city"],
                              "lat": r["gps_x"], "lon": r["gps_y"],
                              "visited": str(r["pos_id"]) in visited_ids})

    # candidate POS near the route (not visited) — bbox prefilter then exact dist
    passed_by, opportunities = [], []
    if stop_pts:
        rules, _neg = live_plan._cadence_rules()
        last_any, _last_tech = live_plan._last_visits()
        today = datetime.date.today()
        min_lat, max_lat, min_lon, max_lon = _bbox(stop_pts, radius_km)
        cand = db.get(
            "SELECT pos_id, name, city, category, market, technician, gps_x, gps_y "
            "FROM pos_master WHERE active=1 AND gps_x BETWEEN ? AND ? AND gps_y BETWEEN ? AND ?",
            (min_lat, max_lat, min_lon, max_lon))
        for r in cand:
            pid = str(r["pos_id"])
            if pid in visited_ids or r["gps_x"] is None or r["gps_y"] is None:
                continue
            dmin = min(distance_km(r["gps_x"], r["gps_y"], sp[0], sp[1]) for sp in stop_pts)
            if dmin > radius_km:
                continue
            item = {"pos": pid, "name": r["name"], "city": r["city"],
                    "lat": r["gps_x"], "lon": r["gps_y"], "distKm": round(dmin, 2),
                    "technician": r["technician"]}
            passed_by.append(item)
            # opportunity = near the route AND due/overdue by GECO/CORN cadence
            rule = live_plan._match_rule(rules, r["category"], r["market"])
            if rule and rule.maxIntervalWeeks is not None:
                lv = live_plan._date(last_any.get(pid))
                overdue = True
                if lv:
                    overdue = (today - lv).days >= rule.maxIntervalWeeks * 7
                if overdue:
                    opportunities.append({**item, "cadence": rule.ruleId,
                                          "lastVisit": last_any.get(pid)})
        passed_by.sort(key=lambda x: x["distKm"])
        opportunities.sort(key=lambda x: x["distKm"])

    # ---- efficiency findings ----
    findings = []
    long_legs = [l for l in legs if l.get("km") and l["km"] > LONG_LEG_KM]
    for l in sorted(long_legs, key=lambda x: -x["km"])[:5]:
        a = stops[l["fromSeq"] - 1]; b = stops[l["toSeq"] - 1]
        findings.append({
            "type": "long_transfer", "severity": "warn",
            "km": l["km"], "travelMin": l.get("travelMin"),
            "message": f"Dlouhý přesun {l['km']} km ({esc_name(a)} → {esc_name(b)})"
                       f"{' · ' + str(round(l['travelMin'])) + ' min' if l.get('travelMin') else ''}.",
        })
    # likely backtracking: a stop closer to an earlier stop than to its predecessor
    for i in range(2, len(stops)):
        p0, p1, p2 = stops[i - 2], stops[i - 1], stops[i]
        if None in (p0["lat"], p1["lat"], p2["lat"]):
            continue
        d_prev = distance_km(p1["lat"], p1["lon"], p2["lat"], p2["lon"])
        d_back = distance_km(p0["lat"], p0["lon"], p2["lat"], p2["lon"])
        if d_prev > 8 and d_back + 3 < d_prev:
            findings.append({
                "type": "backtrack", "severity": "info",
                "message": f"Možný zbytečný přejezd u zastávky #{i + 1} "
                           f"({esc_name(p2)}) – blíž k dřívější zastávce než k předchozí.",
            })
            break
    if opportunities:
        top = opportunities[:3]
        names = ", ".join(f"{o['name'] or o['pos']} ({o['distKm']} km)" for o in top)
        findings.append({
            "type": "missed_opportunity", "severity": "warn",
            "count": len(opportunities),
            "message": f"{len(opportunities)} POS po termínu cadence do {radius_km:g} km od trasy "
                       f"(např. {names}) – šlo je obsloužit cestou.",
        })

    return {
        "technician": technician, "date": date, "hasData": True,
        "radiusKm": radius_km,
        "metrics": metrics,
        "stops": stops, "legs": legs,
        "layers": {
            "planned": planned_layer,
            "visited": [{"seq": s["seq"], "pos": s["pos"], "name": s["name"], "city": s["city"],
                         "lat": s["lat"], "lon": s["lon"], "onPosMin": s["onPosMin"],
                         "started": s["started"], "finished": s["finished"]} for s in stops],
            "passedBy": passed_by,
            "opportunities": opportunities,
        },
        "findings": findings,
    }


def esc_name(stop):
    return stop.get("name") or stop.get("pos") or "?"


def trends(technician: str, days_back: int = 90) -> dict:
    """Per-day series for long-term trends (km, visits, on-POS, travel, productivity)."""
    day_list = route_actual.technician_days(technician)
    cutoff = (datetime.date.today() - datetime.timedelta(days=days_back)).isoformat()
    day_list = [d for d in day_list if d >= cutoff][:60]
    series = []
    for dstr in sorted(day_list):
        base = route_actual.technician_route(technician, dstr, dstr)
        dd = base["days"][0] if base["days"] else None
        if not dd:
            continue
        wm = (dd.get("workHours") or 0) * 60.0
        series.append({
            "date": dstr, "visits": dd["stopCount"], "km": dd["totalKm"],
            "onPosMin": dd["onPosMin"], "travelMin": dd["travelMin"],
            "workHours": dd.get("workHours"),
            "avgOnPosMin": round(dd["onPosMin"] / dd["stopCount"], 1) if dd["stopCount"] else None,
            **_productivity(dd["stopCount"], dd["onPosMin"], dd["travelMin"], wm),
        })
    # rollups
    def avg(key):
        vals = [s[key] for s in series if s.get(key) is not None]
        return round(sum(vals) / len(vals), 1) if vals else None
    summary = {
        "days": len(series),
        "totalVisits": sum(s["visits"] for s in series),
        "totalKm": round(sum(s["km"] for s in series), 1),
        "avgVisitsPerDay": avg("visits"), "avgKmPerDay": avg("km"),
        "avgOnPosMin": avg("avgOnPosMin"), "avgVisitsPerWorkHour": avg("visitsPerWorkHour"),
    }
    return {"technician": technician, "series": series, "summary": summary}
