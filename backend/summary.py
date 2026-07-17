"""Monthly Summary — a management overview of the whole network for a chosen
period, with filtering and drill-down.

One screen the leadership can read in minutes: how the company did over a month
/ quarter / year / custom range, where the biggest losses are, where
performance is improving, and which technicians / regions / POS need attention.

Design:
  * a single honest pass reconstructs every filtered technician's real days
    (route_actual: lunches/offices excluded, real road km, modelled driving),
    accumulating BOTH whole-period totals (for KPIs, Health Score, TOP lists)
    and per-period buckets (for the development charts) in one go;
  * the Health Score reuses the exact component weights from diagnostics, so
    "weakest technician" here means the same thing as everywhere else;
  * TourPlan fulfilment reuses plan_reality; the previous period is computed the
    same way so every KPI carries a real vs-last-period delta.

Read-only over SalesApp + the published plan. No engine change. Everything is
built so a number can be traced to a technician, a day, or a POS.
"""
from __future__ import annotations

import calendar
import datetime
import statistics
from collections import defaultdict

import db
import route_actual
import travel_model
from desktop_client.engines.core_logic import GeoPoint, distance_km

_VISIBILITY_TOKEN = "náběh kampaně"


# ---------------------------------------------------------------- period math
def _month_range(year, month):
    last = calendar.monthrange(year, month)[1]
    return datetime.date(year, month, 1), datetime.date(year, month, last)


def resolve_period(period: str, year: int | None, month: int | None,
                   quarter: int | None, date_from: str | None, date_to: str | None):
    """Return (start, end, label, prev_start, prev_end) for the chosen period."""
    today = datetime.date.today()
    year = year or today.year
    if period == "custom" and date_from and date_to:
        s = datetime.date.fromisoformat(date_from); e = datetime.date.fromisoformat(date_to)
        span = (e - s).days + 1
        return s, e, f"{s.isoformat()} – {e.isoformat()}", s - datetime.timedelta(days=span), s - datetime.timedelta(days=1)
    if period == "year":
        s, e = datetime.date(year, 1, 1), datetime.date(year, 12, 31)
        return s, e, str(year), datetime.date(year - 1, 1, 1), datetime.date(year - 1, 12, 31)
    if period == "quarter":
        q = quarter or ((today.month - 1) // 3 + 1)
        m0 = (q - 1) * 3 + 1
        s, _ = _month_range(year, m0); _, e = _month_range(year, m0 + 2)
        pq, py = (q - 1, year) if q > 1 else (4, year - 1)
        pm0 = (pq - 1) * 3 + 1
        ps, _ = _month_range(py, pm0); _, pe = _month_range(py, pm0 + 2)
        return s, e, f"Q{q} {year}", ps, pe
    # default: month
    m = month or today.month
    s, e = _month_range(year, m)
    pm, py = (m - 1, year) if m > 1 else (12, year - 1)
    ps, pe = _month_range(py, pm)
    return s, e, f"{m:02d}/{year}", ps, pe


def _period_key(date_iso: str, grain: str) -> str:
    d = datetime.date.fromisoformat(date_iso[:10])
    if grain == "month":
        return f"{d.year}-{d.month:02d}"
    iso = d.isocalendar()
    return f"{iso[0]}-W{iso[1]:02d}"


# ---------------------------------------------------------------- dimensions
def _tech_region_map() -> dict:
    """Each technician's most-common SalesApp region (the technicians table
    isn't reliably filled)."""
    m: dict = {}
    for r in db.get(
            "SELECT technician, region, COUNT(*) n FROM salesapp_visits "
            "WHERE technician IS NOT NULL AND region IS NOT NULL AND region<>'' "
            "GROUP BY technician, region ORDER BY technician, n DESC"):
        m.setdefault(r["technician"], r["region"])
    return m


def dimensions() -> dict:
    """Everything the filter bar needs, straight from the data."""
    regions = [r["region"] for r in db.get(
        "SELECT region, COUNT(*) c FROM salesapp_visits "
        "WHERE region IS NOT NULL AND region<>'' GROUP BY region ORDER BY c DESC")]
    chains = [r["market"] for r in db.get(
        "SELECT market, COUNT(*) c FROM pos_master WHERE market IS NOT NULL AND market<>'' "
        "GROUP BY market ORDER BY c DESC")]
    techs = [{"name": r["name"], "role": r["role"], "active": bool(r["active"])}
             for r in db.get("SELECT name, role, active FROM technicians WHERE excluded=0 ORDER BY name")]
    campaigns = [r["name"] for r in db.get(
        "SELECT DISTINCT name FROM campaigns WHERE name IS NOT NULL AND name<>'' ORDER BY name")] \
        if db.get("SELECT name FROM sqlite_master WHERE type='table' AND name='campaigns'") else []
    visit_types = ["Náběh kampaně", "Zásobování / obálky", "MCHD ostatní",
                   "OZ - Aktivita 1", "OZ - Ostatní", "Kontrola / osvědčení"]
    rng = db.get("SELECT MIN(date(visit_date)) a, MAX(date(visit_date)) b FROM salesapp_visits")
    return {"regions": regions, "chains": chains, "technicians": techs,
            "campaigns": campaigns, "visitTypes": visit_types,
            "roles": ["TECHNIK", "OZ"],
            "dataFrom": rng[0]["a"], "dataTo": rng[0]["b"]}


# ---------------------------------------------------------------- aggregation
def _people(role: str, region: str | None, active: str | None,
            technician: str | None, region_map: dict) -> list:
    """The technicians in scope after who-filters (role / region / active /
    single technician)."""
    # excluded=0 is unconditional: blacklisted test accounts never appear in any
    # analytics, regardless of the active/inactive/all filter above.
    q = "SELECT name, role, active FROM technicians WHERE excluded=0"
    params: list = []
    if role in ("TECHNIK", "OZ"):
        q += " AND role=?"; params.append(role)
    if active == "active":
        q += " AND active=1"
    elif active == "inactive":
        q += " AND active=0"
    rows = db.get(q, tuple(params))
    names = [r["name"] for r in rows]
    if technician:
        names = [n for n in names if n == technician]
    if region:
        names = [n for n in names if region_map.get(n) == region]
    return names


def _nn_savings(pts: list) -> tuple:
    """Nearest-neighbour optimal ordering vs. the real order — cheap estimate of
    km and minutes better ordering would save on a day."""
    n = len(pts)
    if n < 2:
        return 0.0, 0.0
    actual_legs = [distance_km(pts[i].x, pts[i].y, pts[i + 1].x, pts[i + 1].y) for i in range(n - 1)]
    unvisited = set(range(n)); order = [0]; unvisited.discard(0)
    while unvisited:
        last = order[-1]
        nxt = min(unvisited, key=lambda j: distance_km(pts[last].x, pts[last].y, pts[j].x, pts[j].y))
        order.append(nxt); unvisited.discard(nxt)
    opt_legs = [distance_km(pts[order[i]].x, pts[order[i]].y, pts[order[i + 1]].x, pts[order[i + 1]].y)
                for i in range(n - 1)]
    saved_km = travel_model.road_km(sum(actual_legs)) - travel_model.road_km(sum(opt_legs))
    saved_min = travel_model.minutes_for_legs(actual_legs) - travel_model.minutes_for_legs(opt_legs)
    return max(saved_km, 0.0), max(saved_min, 0.0)


def _blank_tot():
    return {"visits": 0, "daysWorked": 0, "workHours": 0.0, "onPosHours": 0.0,
            "drivingHours": 0.0, "roadKm": 0.0, "savableKm": 0.0, "savableMin": 0.0,
            "onPosMinSum": 0.0, "gapExcessMin": 0.0}


def _day_gap_excess(d) -> float:
    """Sum of unexplained (yellow/red) minutes between consecutive visits on a
    day: measured travel time vs. the modelled drive estimate for each leg."""
    tot = 0.0
    for lg in d.get("legs", []):
        km = lg.get("km")
        actual = lg.get("travelMin")
        if km is None or actual is None:
            continue
        est = travel_model.estimate_minutes(km)
        # yellow/red bands (mirrors gis._classify_gap)
        if actual > est * 1.5 + 10:
            tot += max(actual - est, 0.0)
    return tot


def _aggregate(names: list, start, end, grain: str):
    """One honest pass over every technician-day in the range.

    Returns (per_tech_totals, per_period_network, per_period_by_tech)."""
    per_tech: dict = {n: _blank_tot() for n in names}
    per_period: dict = defaultdict(_blank_tot)                 # network, for charts
    per_period_tech: dict = defaultdict(lambda: defaultdict(_blank_tot))  # [period][tech]
    s_iso, e_iso = start.isoformat(), end.isoformat()
    for name in names:
        route = route_actual.technician_route(name, s_iso, e_iso)
        for d in route.get("days", []):
            pos = d.get("stopCount") or 0
            if not pos:
                continue
            pk = _period_key(d["date"], grain)
            legs = [lg["km"] for lg in d.get("legs", []) if lg.get("km") is not None]
            drive_h = travel_model.minutes_for_legs(legs) / 60.0
            road = travel_model.road_km(d.get("totalKm"))
            onpos_min = d.get("onPosMin") or 0
            # optimal-ordering savings (cheap nn)
            pts = [GeoPoint(x["lat"], x["lon"]) for x in d.get("stops", [])
                   if x.get("kind", "pos") == "pos" and x.get("lat") is not None]
            sav_km, sav_min = _nn_savings(pts)
            gap_excess = _day_gap_excess(d)
            for bucket in (per_tech[name], per_period[pk], per_period_tech[pk][name]):
                bucket["visits"] += pos
                bucket["daysWorked"] += 1
                bucket["workHours"] += d.get("workHours") or 0
                bucket["onPosHours"] += onpos_min / 60.0
                bucket["onPosMinSum"] += onpos_min
                bucket["drivingHours"] += drive_h
                bucket["roadKm"] += road
                bucket["savableKm"] += sav_km
                bucket["savableMin"] += sav_min
                bucket["gapExcessMin"] += gap_excess
    return per_tech, per_period, per_period_tech


def _derive(tot: dict) -> dict:
    """Turn raw sums into the per-technician KPI fields (incl. Health inputs)."""
    dw, wh, v = tot["daysWorked"], tot["workHours"], tot["visits"]
    onpos, drive = tot["onPosHours"], tot["drivingHours"]
    return {
        "visits": v, "daysWorked": dw,
        "visitsPerDay": round(v / dw, 2) if dw else None,
        "workHours": round(wh, 1),
        "workHoursPerDay": round(wh / dw, 2) if dw else None,
        "onPosHours": round(onpos, 1),
        "drivingHours": round(drive, 1),
        "avgOnPosMin": round(tot["onPosMinSum"] / v, 1) if v else None,
        "onPosRatioPct": round(100 * onpos / (onpos + drive), 1) if (onpos + drive) > 0 else None,
        "visitsPerWorkHour": round(v / wh, 2) if wh else None,
        "roadKm": round(tot["roadKm"], 1),
        "savableKm": round(tot["savableKm"], 1),
        "savableHours": round(tot["savableMin"] / 60.0, 1),
        "suspiciousGapPerDay": round(tot["gapExcessMin"] / dw, 1) if dw else None,
        "unexplainedGapMin": round(tot["gapExcessMin"], 1),
    }


# ---------------------------------------------------------------- health
def _score_population(people_stats: list, role: str, min_visits: int = 30,
                      min_days: int = 10) -> dict:
    """Health Score over a population, using diagnostics' component weights so
    it means the same as the cockpit's 'Kritické případy'. people_stats items
    must carry the referenced fields. Returns {name: {healthScore, why}}.
    Thresholds are relaxed for short (per-week) buckets so the trend line can
    still be drawn."""
    import diagnostics
    comps = diagnostics._HEALTH_PROFILES.get(role, diagnostics._HEALTH_COMPS_TECHNIK)
    pop = [t for t in people_stats if (t.get("visits") or 0) >= min_visits and (t.get("daysWorked") or 0) >= min_days]
    if len(pop) < 5:
        return {}
    stats = {}
    for field, *_ in comps:
        vals = [t[field] for t in pop if t.get(field) is not None]
        if vals:
            stats[field] = (statistics.median(vals), statistics.pstdev(vals) if len(vals) > 1 else 0)
    total_w = sum(w for field, _, w, _ in comps if field in stats) or 1
    out = {}
    for t in pop:
        badness, why = 0.0, []
        for field, bad_dir, w, label in comps:
            if field not in stats or t.get(field) is None:
                continue
            med, sd = stats[field]
            if not sd:
                continue
            z = (t[field] - med) / sd
            z_bad = z if bad_dir == "high" else -z
            badness += w * max(0.0, min(z_bad, 3.0)) / 3.0
            if z_bad >= 1.0:
                why.append({"label": label, "value": t[field], "peerMedian": round(med, 1)})
        out[t["technician"]] = {"healthScore": max(0, min(100, round(100 * (1 - badness / total_w)))),
                                "why": why[:3]}
    return out


# ---------------------------------------------------------------- fulfilment
def _fulfilment_map(start, end) -> dict:
    """TourPlan fulfilment % per technician over the published weeks that fall in
    the range."""
    try:
        import plan_reality
        wk = db.get("SELECT MIN(week) a, MAX(week) b FROM published_plans")
        if not wk or wk[0]["a"] is None:
            return {}, None
        wa, wb = int(wk[0]["a"]), int(wk[0]["b"])
        # narrow to weeks intersecting the range
        rs, re = start.isocalendar()[1], end.isocalendar()[1]
        lo, hi = max(wa, min(rs, re)), min(wb, max(rs, re))
        if lo > hi:
            lo, hi = wa, wb
        f = plan_reality.fulfillment(lo, hi)
        return {t["technician"]: t.get("fulfilmentPct") for t in f.get("perTechnician", [])}, f
    except Exception:  # noqa: BLE001
        return {}, None


# ---------------------------------------------------------------- coverage
def _visit_type_clause(visit_type: str | None):
    """Map a friendly visit-type to a purpose LIKE clause."""
    if not visit_type:
        return "", []
    m = {
        "Náběh kampaně": "%Náběh kampaně%", "Zásobování / obálky": "%Zásobování%",
        "MCHD ostatní": "%MCHD - Ostatní%", "OZ - Aktivita 1": "%Aktivita 1%",
        "OZ - Ostatní": "%OZ - Ostatní%", "Kontrola / osvědčení": "%osvědčení%",
    }
    pat = m.get(visit_type)
    return (" AND v.purpose LIKE ?", [pat]) if pat else ("", [])


def _coverage(start, end, names: list, region, chain, visit_type) -> dict:
    """Visit-level breakdowns for the range: visibility share, most-often
    unserved POS, chain split. Scoped to the same people/region, optionally a
    chain and visit-type."""
    s_iso, e_iso = start.isoformat(), end.isoformat()
    where = ["v.visit_date >= ? ", "v.visit_date <= ?"]
    params: list = [s_iso, e_iso]
    if names:
        where.append("v.technician IN (%s)" % ",".join("?" * len(names))); params += names
    if region:
        where.append("v.region=?"); params.append(region)
    vt_sql, vt_p = _visit_type_clause(visit_type)
    base = " AND ".join(where) + vt_sql
    params_base = params + vt_p
    chain_join = "LEFT JOIN pos_master p ON p.pos_id=v.pos_id"
    chain_sql = ""
    if chain:
        chain_sql = " AND p.market=?"; params_base = params_base + [chain]
    total = db.get(f"SELECT COUNT(*) c FROM salesapp_visits v {chain_join} WHERE {base}{chain_sql}", tuple(params_base))[0]["c"]
    vis = db.get(f"SELECT COUNT(*) c FROM salesapp_visits v {chain_join} WHERE {base}{chain_sql} AND lower(v.purpose) LIKE ?",
                 tuple(params_base + [f"%{_VISIBILITY_TOKEN}%"]))[0]["c"]
    # chain split
    chains = [{"chain": r["market"] or "—", "visits": r["c"]} for r in db.get(
        f"SELECT p.market market, COUNT(*) c FROM salesapp_visits v {chain_join} WHERE {base}{chain_sql} "
        f"GROUP BY p.market ORDER BY c DESC LIMIT 8", tuple(params_base))]
    return {"visitsTotal": total, "visibilityVisits": vis,
            "visibilitySharePct": round(100 * vis / total, 1) if total else None,
            "chains": chains}


def _unserved_pos(start, end, names, region, limit=8) -> list:
    """POS planned in published weeks intersecting the range but not visited —
    the most-often-skipped, with how many planned weeks were missed."""
    wk = db.get("SELECT MIN(week) a, MAX(week) b FROM published_plans")
    if not wk or wk[0]["a"] is None:
        return []
    q = ("SELECT pp.pos_id pos, COALESCE(pp.name,p.name) nm, COALESCE(pp.city,p.city) city, "
         "pp.technician tech, COUNT(*) planned FROM published_plans pp "
         "JOIN plan_lifecycle pl ON pl.week=pp.week AND pl.snapshot_id=pp.snapshot_id AND pl.status='Published' "
         "LEFT JOIN pos_master p ON p.pos_id=pp.pos_id "
         "WHERE NOT EXISTS (SELECT 1 FROM salesapp_visits v WHERE v.pos_id=pp.pos_id "
         "  AND v.visit_date>=? AND v.visit_date<=?) ")
    params: list = [start.isoformat(), end.isoformat()]
    if names:
        q += "AND pp.technician IN (%s) " % ",".join("?" * len(names)); params += names
    q += "GROUP BY pp.pos_id ORDER BY planned DESC, nm LIMIT ?"; params.append(limit)
    return [{"pos": str(r["pos"]), "name": r["nm"] or f"POS {r['pos']}", "city": r["city"],
             "technician": r["tech"], "plannedWeeks": r["planned"]} for r in db.get(q, tuple(params))]


# ---------------------------------------------------------------- the summary
def summary(period: str = "month", year: int | None = None, month: int | None = None,
            quarter: int | None = None, date_from: str | None = None, date_to: str | None = None,
            role: str = "TECHNIK", region: str | None = None, technician: str | None = None,
            chain: str | None = None, visit_type: str | None = None,
            active: str | None = "active", grain: str = "week") -> dict:
    start, end, label, pstart, pend = resolve_period(period, year, month, quarter, date_from, date_to)
    region_map = _tech_region_map()
    role_u = (role or "TECHNIK").upper()
    names = _people(role_u, region, active, technician, region_map)

    per_tech, per_period, per_period_tech = _aggregate(names, start, end, grain)
    fulfil, _f = _fulfilment_map(start, end) if role_u == "TECHNIK" else ({}, None)
    import diagnostics
    area_returns = diagnostics._area_returns_per_week() if role_u == "TECHNIK" else {}

    # per-technician derived + health inputs
    people = []
    for name in names:
        d = _derive(per_tech[name])
        d["technician"] = name
        d["region"] = region_map.get(name)
        d["planFulfilmentPct"] = fulfil.get(name)
        d["areaReturnsPerWeek"] = round(area_returns.get(name, 0), 2)
        d["loadPct"] = None  # capacity rarely set; excluded from health when None
        people.append(d)
    health = _score_population(people, role_u)
    for p in people:
        h = health.get(p["technician"])
        p["healthScore"] = h["healthScore"] if h else None
        p["why"] = h["why"] if h else []
    active_people = [p for p in people if p["daysWorked"] > 0]

    # ---- previous period (same filters) for deltas & movers
    pt_prev, _, _ = _aggregate(names, pstart, pend, grain)
    prev_people = []
    pfulfil = (_fulfilment_map(pstart, pend)[0] if role_u == "TECHNIK" else {})
    for name in names:
        if pt_prev[name]["daysWorked"] == 0:
            continue
        d = _derive(pt_prev[name]); d["technician"] = name
        d["planFulfilmentPct"] = pfulfil.get(name)
        d["areaReturnsPerWeek"] = round(area_returns.get(name, 0), 2)
        d["loadPct"] = None
        prev_people.append(d)
    prev_health = _score_population(prev_people, role_u)
    prev_by = {p["technician"]: p for p in prev_people}

    def net(ppl, field):
        vals = [p[field] for p in ppl if p.get(field) is not None]
        return vals

    def _kpi(field, agg="sum"):
        cur = net(active_people, field)
        prev = net(prev_people, field)
        f = (lambda xs: round(sum(xs), 1)) if agg == "sum" else (lambda xs: round(statistics.mean(xs), 2) if xs else None)
        c, p = (f(cur) if cur else (0 if agg == "sum" else None)), (f(prev) if prev else None)
        delta = round(c - p, 2) if (c is not None and p is not None) else None
        return {"value": c, "prev": p, "delta": delta}

    def _avg_health(hmap):
        vs = [v["healthScore"] for v in hmap.values()]
        return round(statistics.mean(vs), 1) if vs else None

    total_visits = sum(p["visits"] for p in active_people)
    prev_visits = sum(p["visits"] for p in prev_people)
    planned_visits = _planned_visits(names, start, end)
    _gap_cur = round(sum(p.get("unexplainedGapMin") or 0 for p in active_people) / 60.0, 1)
    _gap_prev = round(sum(p.get("unexplainedGapMin") or 0 for p in prev_people) / 60.0, 1) if prev_people else None

    kpis = {
        "visits": {"value": total_visits, "prev": prev_visits,
                   "delta": total_visits - prev_visits},
        "plannedVisits": {"value": planned_visits},
        "planFulfilmentPct": _kpi("planFulfilmentPct", "avg"),
        "productivity": _kpi("visitsPerWorkHour", "avg"),
        "visitsPerDay": _kpi("visitsPerDay", "avg"),
        "workHours": _kpi("workHours", "sum"),
        "onPosHours": _kpi("onPosHours", "sum"),
        "travelHours": _kpi("drivingHours", "sum"),
        "roadKm": _kpi("roadKm", "sum"),
        "savableHours": _kpi("savableHours", "sum"),
        "savableKm": _kpi("savableKm", "sum"),
        "unexplainedGapHours": {"value": _gap_cur, "prev": _gap_prev,
                                "delta": round(_gap_cur - _gap_prev, 1) if _gap_prev is not None else None},
        "onPosRatioPct": _kpi("onPosRatioPct", "avg"),
        "avgHealthScore": {"value": _avg_health(health), "prev": _avg_health(prev_health),
                           "delta": (round(_avg_health(health) - _avg_health(prev_health), 1)
                                     if _avg_health(health) is not None and _avg_health(prev_health) is not None else None)},
        "activePeople": len(active_people),
    }

    # ---- TOP lists
    scored = [p for p in active_people if p.get("healthScore") is not None]
    best = sorted(scored, key=lambda p: -p["healthScore"])[:6]
    weakest = sorted(scored, key=lambda p: p["healthScore"])[:6]
    movers = []
    for p in active_people:
        pv = prev_by.get(p["technician"])
        if not pv or p.get("visitsPerWorkHour") is None or pv.get("visitsPerWorkHour") is None:
            continue
        movers.append({"technician": p["technician"], "region": p["region"],
                       "now": p["visitsPerWorkHour"], "was": pv["visitsPerWorkHour"],
                       "delta": round(p["visitsPerWorkHour"] - pv["visitsPerWorkHour"], 2)})
    improved = sorted([m for m in movers if m["delta"] > 0], key=lambda m: -m["delta"])[:6]
    dropped = sorted([m for m in movers if m["delta"] < 0], key=lambda m: m["delta"])[:6]
    area_top = sorted([p for p in active_people if p["areaReturnsPerWeek"]],
                      key=lambda p: -p["areaReturnsPerWeek"])[:6]

    # ---- problem regions (lost hours per tech = savable)
    reg = defaultdict(lambda: {"savableHours": 0.0, "savableKm": 0.0, "techs": 0,
                               "onPos": 0.0, "drive": 0.0, "visits": 0})
    for p in active_people:
        r = reg[p["region"] or "—"]
        r["savableHours"] += p["savableHours"]; r["savableKm"] += p["savableKm"]
        r["techs"] += 1; r["onPos"] += p["onPosHours"]; r["drive"] += p["drivingHours"]
        r["visits"] += p["visits"]
    regions = [{"region": k, "savableHoursPerTech": round(v["savableHours"] / v["techs"], 1) if v["techs"] else 0,
                "savableHours": round(v["savableHours"], 1), "savableKm": round(v["savableKm"], 1),
                "techs": v["techs"], "visits": v["visits"],
                "onPosRatioPct": round(100 * v["onPos"] / (v["onPos"] + v["drive"]), 1) if (v["onPos"] + v["drive"]) else None}
               for k, v in reg.items()]
    regions.sort(key=lambda r: -r["savableHoursPerTech"])

    # ---- development charts (per period, network of the filtered scope)
    trend = _period_trends(per_period, per_period_tech, role_u, fulfil, area_returns, grain)

    trend["planFulfilment"] = _plan_fulfilment_series([t["period"] for t in trend["productivity"]], grain)
    coverage = _coverage(start, end, names, region, chain, visit_type)
    unserved = _unserved_pos(start, end, names, region)
    campaigns = _campaigns(start, end, names)

    return {
        "period": {"key": period, "label": label, "from": start.isoformat(), "to": end.isoformat(),
                   "prevFrom": pstart.isoformat(), "prevTo": pend.isoformat()},
        "filters": {"role": role_u, "region": region, "technician": technician,
                    "chain": chain, "visitType": visit_type, "active": active, "grain": grain},
        "kpis": kpis,
        "top": {"best": best, "weakest": weakest, "improved": improved, "dropped": dropped,
                "areaReturns": area_top, "problemRegions": regions[:6]},
        "regions": regions,
        "coverage": coverage,
        "campaigns": campaigns,
        "unservedPos": unserved,
        "trend": trend,
        "peopleCount": len(active_people),
    }


def _planned_visits(names, start, end) -> int | None:
    """How many POS-weeks were planned in the published weeks intersecting the
    range (planned visits), for the people in scope."""
    wk = db.get("SELECT MIN(week) a, MAX(week) b FROM published_plans")
    if not wk or wk[0]["a"] is None:
        return None
    q = ("SELECT COUNT(*) c FROM published_plans pp "
         "JOIN plan_lifecycle pl ON pl.week=pp.week AND pl.snapshot_id=pp.snapshot_id AND pl.status='Published'")
    params: list = []
    if names:
        q += " WHERE pp.technician IN (%s)" % ",".join("?" * len(names)); params += names
    return db.get(q, tuple(params))[0]["c"]


def _weeks_for_period(key: str, grain: str) -> list:
    """ISO week numbers covered by a period key ('YYYY-Www' or 'YYYY-MM')."""
    if grain == "month":
        y, m = (int(x) for x in key.split("-"))
        last = calendar.monthrange(y, m)[1]
        weeks = sorted({datetime.date(y, m, d).isocalendar()[1] for d in range(1, last + 1)})
        return weeks
    return [int(key.split("W")[1])]


def _plan_fulfilment_series(period_keys: list, grain: str) -> list:
    """TourPlan fulfilment % per period (planned POS visited vs planned),
    reusing plan_reality over the weeks each period covers."""
    import plan_reality
    have = db.get("SELECT MIN(week) a, MAX(week) b FROM published_plans")
    if not have or have[0]["a"] is None:
        return [{"period": k, "value": None} for k in period_keys]
    wa, wb = int(have[0]["a"]), int(have[0]["b"])
    out = []
    for k in period_keys:
        weeks = [w for w in _weeks_for_period(k, grain) if wa <= w <= wb]
        if not weeks:
            out.append({"period": k, "value": None}); continue
        f = plan_reality.fulfillment(min(weeks), max(weeks))
        out.append({"period": k, "value": f.get("fulfilmentPct")})
    return out


def _campaigns(start, end, names) -> list:
    """Campaigns active in the period, with TourPlan fulfilment over their weeks
    and how many visibility (Náběh kampaně) visits landed in that window."""
    if not db.get("SELECT name FROM sqlite_master WHERE type='table' AND name='campaigns'"):
        return []
    import plan_reality
    rs, re = start.isocalendar()[1], end.isocalendar()[1]
    lo, hi = min(rs, re), max(rs, re)
    rows = db.get("SELECT name, kind, start_week, end_week FROM campaigns "
                  "WHERE active=1 AND start_week<=? AND end_week>=? ORDER BY start_week", (hi, lo))
    out = []
    for c in rows:
        wf, wt = max(int(c["start_week"]), lo), min(int(c["end_week"]), hi)
        if wf > wt:
            continue
        f = plan_reality.fulfillment(wf, wt)
        # visibility visits in this window, scoped to the people in play
        q = ("SELECT COUNT(*) c FROM salesapp_visits v WHERE v.visit_date IS NOT NULL "
             "AND lower(v.purpose) LIKE ? "
             "AND CAST(strftime('%W', v.visit_date) AS INT) BETWEEN ? AND ?")
        params: list = [f"%{_VISIBILITY_TOKEN}%", wf, wt]
        if names:
            q += " AND v.technician IN (%s)" % ",".join("?" * len(names)); params += names
        vis = db.get(q, tuple(params))[0]["c"]
        out.append({"name": c["name"], "kind": c["kind"], "weekFrom": wf, "weekTo": wt,
                    "planned": f.get("planned"), "done": (f.get("done") or 0) + (f.get("doneShifted") or 0),
                    "fulfilmentPct": f.get("fulfilmentPct"), "visibilityVisits": vis})
    return out


def _period_trends(per_period, per_period_tech, role, fulfil, area_returns, grain) -> dict:
    """Build the development series over the periods present in the range."""
    keys = sorted(per_period)
    prod, workh, visits, onpos, travel, health_ser, gap_ser = [], [], [], [], [], [], []
    for k in keys:
        b = per_period[k]
        wh, v = b["workHours"], b["visits"]
        prod.append({"period": k, "value": round(v / wh, 2) if wh else None})
        workh.append({"period": k, "value": round(wh, 1)})
        visits.append({"period": k, "value": v})
        onpos.append({"period": k, "value": round(b["onPosHours"], 1)})
        travel.append({"period": k, "value": round(b["drivingHours"], 1)})
        gap_ser.append({"period": k, "value": round(b["gapExcessMin"] / 60.0, 1)})
        # health per period (score that period's population with the same weights)
        pplist = []
        for name, tot in per_period_tech[k].items():
            d = _derive(tot); d["technician"] = name
            d["planFulfilmentPct"] = fulfil.get(name)
            d["areaReturnsPerWeek"] = round(area_returns.get(name, 0), 2)
            d["loadPct"] = None
            pplist.append(d)
        # per-week buckets are small; relax the min data bar so a network-average
        # Health line can still be drawn (monthly buckets easily clear the bar).
        thr = (30, 10) if grain == "month" else (8, 3)
        hs = _score_population(pplist, role, thr[0], thr[1])
        vals = [x["healthScore"] for x in hs.values()]
        health_ser.append({"period": k, "value": round(statistics.mean(vals), 1) if vals else None})
    return {"productivity": prod, "workHours": workh, "visits": visits,
            "onPosHours": onpos, "travelHours": travel, "health": health_ser,
            "unexplainedGap": gap_ser}
