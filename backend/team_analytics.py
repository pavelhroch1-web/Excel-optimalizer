"""Team dashboard over SalesApp - who needs attention, where time/km leaks.

A manager cockpit for the whole field team, computed efficiently in one pass:
per active technician the real workload (visits, days, km, travel vs on-POS
time, worked hours, productivity), the plan load (planned stops vs weekly
capacity -> overloaded / slack), and overdue work. Plus team rollups and the
biggest leaks (travel time, long transfers). Read-only over SQLite; the driven
route km/time is reconstructed from visit times + POS GPS (same maths as
route_actual) but in a single sweep so it scales to the whole team.

OZ stay informational (only role=TECHNIK & active=1 count).
"""
from __future__ import annotations

import datetime

import db
from desktop_client.engines.core_logic import distance_km

DEFAULT_CAPACITY = 40          # weekly POS capacity fallback if not configured
LONG_LEG_KM = 30.0


def _dt(s):
    if not s:
        return None
    for fmt in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%d %H:%M"):
        try:
            return datetime.datetime.strptime(str(s)[:19], fmt)
        except ValueError:
            continue
    try:
        return datetime.datetime.fromisoformat(str(s))
    except ValueError:
        return None


def _iso_week(date_str):
    try:
        y, m, d = (int(x) for x in str(date_str)[:10].split("-"))
        return datetime.date(y, m, d).isocalendar()[1]
    except (ValueError, TypeError):
        return None


def overview(days_back: int = 21, role: str = "TECHNIK") -> dict:
    """Per-person workload + plan load + overdue, team rollups and leaks, for one
    role (TECHNIK by default; OZ shown only when explicitly requested)."""
    techs = {r["name"]: dict(r) for r in db.get(
        "SELECT name, capacity_per_week, region FROM technicians "
        "WHERE role=? AND active=1", (role.upper(),))}
    if not techs:
        return {"technicians": [], "team": {}, "note": f"Nejsou žádní aktivní lidé (role {role})."}

    gps = {str(r["pos_id"]): (r["gps_x"], r["gps_y"])
           for r in db.get("SELECT pos_id, gps_x, gps_y FROM pos_master")}
    cutoff = (datetime.date.today() - datetime.timedelta(days=days_back)).isoformat()

    # one sweep of recent visits, grouped by (technician, day)
    rows = db.get(
        "SELECT technician, pos_id, visit_date, started_at, finished_at, real_duration "
        "FROM salesapp_visits WHERE technician IS NOT NULL AND visit_date IS NOT NULL "
        "AND date(visit_date) >= date(?) ORDER BY technician, visit_date, started_at", (cutoff,))
    per_day: dict = {}
    for r in rows:
        t = r["technician"]
        if t not in techs:
            continue
        key = (t, str(r["visit_date"])[:10])
        per_day.setdefault(key, []).append(r)

    agg: dict = {t: {"visits": 0, "posSet": set(), "days": 0, "km": 0.0,
                     "travelMin": 0.0, "onPosMin": 0.0, "workMin": 0.0,
                     "longTransfers": 0} for t in techs}
    for (t, _day), visits in per_day.items():
        a = agg[t]
        a["days"] += 1
        stops = sorted(visits, key=lambda r: _dt(r["started_at"]) or datetime.datetime.max)
        prev = None
        starts, ends = [], []
        for r in stops:
            a["visits"] += 1
            if r["pos_id"]:
                a["posSet"].add(str(r["pos_id"]))
            st, fin = _dt(r["started_at"]), _dt(r["finished_at"])
            if st:
                starts.append(st)
            if fin:
                ends.append(fin)
            on = None
            if st and fin:
                on = (fin - st).total_seconds() / 60.0
            elif r["real_duration"] not in (None, ""):
                try:
                    on = float(r["real_duration"]) * 60
                except (ValueError, TypeError):
                    on = None
            if on:
                a["onPosMin"] += on
            g = gps.get(str(r["pos_id"])) if r["pos_id"] else None
            if prev and prev[0] and g and None not in (prev[0][0], prev[0][1], g[0], g[1]):
                leg = distance_km(prev[0][0], prev[0][1], g[0], g[1])
                a["km"] += leg
                if leg > LONG_LEG_KM:
                    a["longTransfers"] += 1
            if prev and prev[1] and st:
                tmin = (st - prev[1]).total_seconds() / 60.0
                if tmin > 0:
                    a["travelMin"] += tmin
            prev = (g, fin)
        if starts and ends:
            a["workMin"] += (max(ends) - min(starts)).total_seconds() / 60.0

    # plan load (current + next published week) + overdue, from published_plans
    today = datetime.date.today()
    cur_week = today.isocalendar()[1]
    plan_rows = db.get(
        "SELECT pp.technician AS t, pp.week AS wk, pp.pos_id AS pos FROM published_plans pp "
        "JOIN plan_lifecycle pl ON pl.week=pp.week AND pl.snapshot_id=pp.snapshot_id "
        "AND pl.status='Published'")
    visited_week = {}   # (pos, week) visited in reality (any role)
    for r in db.get("SELECT pos_id, visit_date FROM salesapp_visits "
                    "WHERE pos_id IS NOT NULL AND visit_date IS NOT NULL"):
        wk = _iso_week(r["visit_date"])
        if wk is not None:
            visited_week.setdefault((str(r["pos_id"]), wk), True)
    plan_load = {t: 0 for t in techs}
    overdue = {t: 0 for t in techs}
    for r in plan_rows:
        t = r["t"]
        if t not in techs:
            continue
        wk = r["wk"]
        if wk in (cur_week, cur_week + 1):
            plan_load[t] += 1
        if wk < cur_week and (str(r["pos"]), wk) not in visited_week:
            overdue[t] += 1

    out = []
    for t, a in agg.items():
        cap = techs[t].get("capacity_per_week") or DEFAULT_CAPACITY
        days = a["days"]
        total_active = a["onPosMin"] + a["travelMin"]
        on_ratio = round(100 * a["onPosMin"] / total_active, 1) if total_active else None
        work_h = round(a["workMin"] / 60.0, 1) if a["workMin"] else None
        per_h = round(a["visits"] / (a["workMin"] / 60.0), 2) if a["workMin"] else None
        # plan load per-week vs capacity (plan_load spans 2 weeks -> /2)
        load_pct = round(100 * (plan_load[t] / 2) / cap, 0) if cap else None
        status = "ok"
        if load_pct is not None:
            status = "over" if load_pct > 115 else ("slack" if load_pct < 55 else "ok")
        attention = (overdue[t] * 3) + (a["longTransfers"] * 2) + (0 if on_ratio is None else max(0, 50 - on_ratio) / 10)
        out.append({
            "technician": t, "region": techs[t].get("region"),
            "visits": a["visits"], "uniquePos": len(a["posSet"]), "daysWorked": days,
            "totalKm": round(a["km"], 1), "kmPerDay": round(a["km"] / days, 1) if days else None,
            "travelMin": round(a["travelMin"]), "onPosMin": round(a["onPosMin"]),
            "avgOnPosMin": round(a["onPosMin"] / a["visits"], 1) if a["visits"] else None,
            "onPosRatioPct": on_ratio, "avgWorkHours": work_h,
            "visitsPerWorkHour": per_h, "longTransfers": a["longTransfers"],
            "planLoad": plan_load[t], "capacityPerWeek": cap, "loadPct": load_pct,
            "loadStatus": status, "overdue": overdue[t],
            "attention": round(attention, 1),
        })
    out.sort(key=lambda x: -x["attention"])

    active = [x for x in out if x["visits"]]
    team = {
        "technicianCount": len(out), "windowDays": days_back,
        "totalVisits": sum(x["visits"] for x in out),
        "totalKm": round(sum(x["totalKm"] for x in out), 1),
        "totalTravelHours": round(sum(x["travelMin"] for x in out) / 60.0, 1),
        "totalOnPosHours": round(sum(x["onPosMin"] for x in out) / 60.0, 1),
        "avgOnPosRatioPct": round(sum(x["onPosRatioPct"] for x in active if x["onPosRatioPct"] is not None)
                                  / max(len([x for x in active if x["onPosRatioPct"] is not None]), 1), 1) if active else None,
        "overloaded": sum(1 for x in out if x["loadStatus"] == "over"),
        "slack": sum(1 for x in out if x["loadStatus"] == "slack"),
        "totalOverdue": sum(x["overdue"] for x in out),
        "totalLongTransfers": sum(x["longTransfers"] for x in out),
    }
    # leaks: worst travel-time and long-transfer offenders
    leaks = {
        "byTravel": sorted([x for x in active if x["travelMin"]],
                           key=lambda x: -x["travelMin"])[:5],
        "byLongTransfers": sorted([x for x in active if x["longTransfers"]],
                                  key=lambda x: -x["longTransfers"])[:5],
        "byLowOnPos": sorted([x for x in active if x["onPosRatioPct"] is not None],
                             key=lambda x: x["onPosRatioPct"])[:5],
    }
    return {"technicians": out, "team": team, "leaks": leaks}
