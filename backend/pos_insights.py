"""Read-only POS insight queries over SQLite - the informational layer.

Serves POS Explorer and the technician planner. OZ are NOT planned; they are
purely informational here, so the planner can see that an OZ already covered a
POS (when, what, how many) and a technician need not re-drive it without
business value. Pure queries over existing tables - additive module, no schema
change, no engine change.
"""
from __future__ import annotations

import db


def _norm(s) -> str:
    return str(s if s is not None else "").strip().upper()


def _last_visit(pos_id: str, role: str) -> dict | None:
    r = db.get(
        "SELECT visit_date, technician, purpose FROM salesapp_visits "
        "WHERE pos_id = ? AND visitor_role = ? ORDER BY visit_date DESC LIMIT 1",
        (pos_id, role))
    return dict(r[0]) if r else None


def list_filters() -> dict:
    """Distinct values for the POS table filter controls."""
    areas = [r["pos_area"] for r in db.get(
        "SELECT pos_area, COUNT(*) c FROM pos_master WHERE pos_area IS NOT NULL AND pos_area<>'' "
        "GROUP BY pos_area ORDER BY c DESC")]
    markets = [r["market"] for r in db.get(
        "SELECT market, COUNT(*) c FROM pos_master WHERE market IS NOT NULL AND market<>'' "
        "GROUP BY market ORDER BY c DESC")]
    techs = [r["technician"] for r in db.get(
        "SELECT technician, COUNT(*) c FROM pos_master WHERE technician IS NOT NULL AND technician<>'' "
        "GROUP BY technician ORDER BY technician")]
    return {"areas": areas, "markets": markets, "technicians": techs}


def _risk(weeks) -> str:
    if weeks is None:
        return "never"
    if weeks >= 8:
        return "overdue"
    if weeks >= 5:
        return "soon"
    return "ok"


def pos_list(q: str | None = None, area: str | None = None, market: str | None = None,
             technician: str | None = None, status: str = "all",
             limit: int = 200, offset: int = 0) -> dict:
    """All POS with last visit + weeks-since + a simple cadence risk, filtered
    and paginated in SQL — fast over the whole network. `status`: all | overdue
    | never. Any TECHNIK or OZ visit counts as coverage (MAX visit_date)."""
    where = ["p.active=1"]
    params: list = []
    if q:
        where.append("(p.pos_id LIKE ? OR p.name LIKE ? OR p.city LIKE ?)")
        like = f"%{q}%"; params += [like, like, like]
    if area:
        where.append("p.pos_area=?"); params.append(area)
    if market:
        where.append("p.market=?"); params.append(market)
    if technician:
        where.append("p.technician=?"); params.append(technician)
    base = ("FROM pos_master p LEFT JOIN "
            "(SELECT pos_id, MAX(visit_date) lv FROM salesapp_visits GROUP BY pos_id) v "
            "ON v.pos_id=p.pos_id WHERE " + " AND ".join(where))
    wk = "((julianday('now') - julianday(v.lv)) / 7.0)"
    having = ""
    if status == "never":
        having = " AND v.lv IS NULL"
    elif status == "overdue":
        having = f" AND (v.lv IS NULL OR {wk} >= 8)"
    total = db.get(f"SELECT COUNT(*) c {base}{having}", tuple(params))[0]["c"]
    rows = db.get(
        f"SELECT p.pos_id, p.name, p.city, p.pos_area, p.market, p.category, p.technician, "
        f"v.lv last_visit, CASE WHEN v.lv IS NULL THEN NULL ELSE round({wk},1) END weeks_since "
        f"{base}{having} "
        f"ORDER BY (v.lv IS NULL) DESC, v.lv ASC LIMIT ? OFFSET ?",
        tuple(params) + (limit, offset))
    out = []
    for r in rows:
        d = dict(r)
        d["risk"] = _risk(d.get("weeks_since"))
        out.append(d)
    return {"total": total, "count": len(out), "offset": offset, "limit": limit, "pos": out}


def search(q: str, limit: int = 40) -> dict:
    """Full-text-ish POS search by number / name / city, with last visit.
    Powers the command-bar search on the main screen."""
    q = (q or "").strip()
    if not q:
        return {"query": q, "results": [], "count": 0}
    like = f"%{q}%"
    rows = db.get(
        "SELECT pos_id, name, city, technician, category, market, classification "
        "FROM pos_master WHERE active=1 AND "
        "(pos_id LIKE ? OR name LIKE ? OR city LIKE ?) "
        "ORDER BY (pos_id = ?) DESC, (pos_id LIKE ?) DESC, name LIMIT ?",
        (like, like, like, q, q + "%", limit))
    # last visit (any role) per matched POS in one pass
    ids = [str(r["pos_id"]) for r in rows]
    last: dict = {}
    if ids:
        marks = ",".join("?" for _ in ids)
        for r in db.get(
            f"SELECT pos_id, MAX(visit_date) AS lv FROM salesapp_visits "
            f"WHERE pos_id IN ({marks}) GROUP BY pos_id", tuple(ids)):
            last[str(r["pos_id"])] = r["lv"]
    results = []
    for r in rows:
        d = dict(r)
        d["lastVisit"] = last.get(str(r["pos_id"]))
        results.append(d)
    return {"query": q, "results": results, "count": len(results)}


def _to_date(s):
    import datetime
    try:
        y, m, d = (int(x) for x in str(s)[:10].split("-"))
        return datetime.date(y, m, d)
    except (ValueError, TypeError):
        return None


def pos_card(pos_id: str) -> dict:
    """Everything the TourPlan controller needs about a POS in one view, from
    existing data (no engine run): master attributes, technician vs OZ frequency,
    recommended GECO/CORN cadence vs the actual visit interval, deviation, a
    12-month trend, next-due, and a short system recommendation."""
    import datetime
    import live_plan
    rows = db.get(
        "SELECT pos_id, terminal_id, name, street, house_number, city, area, pos_area, "
        "category, market, classification, terminal_type, ppt, gps_x, gps_y, technician, "
        "manager_override_type, active FROM pos_master WHERE pos_id = ?", (pos_id,))
    master = dict(rows[0]) if rows else {"pos_id": pos_id}

    visits = [dict(r) for r in db.get(
        "SELECT visit_date, visitor_role, technician, purpose, started_at, finished_at "
        "FROM salesapp_visits WHERE pos_id = ? AND visit_date IS NOT NULL ORDER BY visit_date", (pos_id,))]
    tech = [v for v in visits if _norm(v["visitor_role"]) == "TECHNIK"]
    oz = [v for v in visits if _norm(v["visitor_role"]) == "OZ"]

    def last(vs):
        return vs[-1] if vs else None

    def avg_interval_weeks(vs):
        ds = [d for d in (_to_date(v["visit_date"]) for v in vs) if d]
        ds = sorted(set(ds))
        if len(ds) < 2:
            return None
        gaps = [(ds[i] - ds[i - 1]).days for i in range(1, len(ds))]
        return round(sum(gaps) / len(gaps) / 7.0, 1)

    actual_tech_weeks = avg_interval_weeks(tech)

    # recommended cadence from the matched GECO/CORN rule (the only hard cadence)
    rules, neglected = live_plan._cadence_rules()
    rule = live_plan._match_rule(rules, master.get("category"), master.get("market"))
    recommended_weeks = rule.maxIntervalWeeks if rule else None
    cadence_id = rule.ruleId if rule else None

    # next due (any-role last visit counts as coverage)
    last_any = _to_date(visits[-1]["visit_date"]) if visits else None
    today = datetime.date.today()
    next_due = days_remaining = None
    due_status = "none"
    if recommended_weeks is not None:
        if last_any:
            nd = last_any + datetime.timedelta(days=int(recommended_weeks * 7))
            next_due = nd.isoformat()
            days_remaining = (nd - today).days
            due_status = "overdue" if days_remaining < 0 else ("dueSoon" if days_remaining <= 14 else "ok")
        else:
            due_status = "overdue"     # never visited but has a cadence

    # deviation actual vs recommended (weeks); negative = visited more often than needed
    deviation = None
    if actual_tech_weeks is not None and recommended_weeks:
        deviation = round(actual_tech_weeks - recommended_weeks, 1)

    # 12-month trend: visits per month (tech)
    trend = {}
    for v in tech:
        mkey = str(v["visit_date"])[:7]
        trend[mkey] = trend.get(mkey, 0) + 1
    months = sorted(trend.keys())[-12:]
    trend_series = [{"month": m, "visits": trend[m]} for m in months]

    # short system recommendation (controller-facing, from the numbers above)
    rec = _pos_recommendation(recommended_weeks, actual_tech_weeks, deviation,
                              days_remaining, due_status, len(tech), len(oz),
                              master.get("manager_override_type"))

    return {
        "posId": pos_id,
        "name": master.get("name"), "city": master.get("city"),
        "address": ", ".join(x for x in [master.get("street"), master.get("house_number"), master.get("city")] if x),
        "area": master.get("area"), "posArea": master.get("pos_area"),
        "category": master.get("category"), "segment": master.get("classification"),
        "market": master.get("market"), "terminalType": master.get("terminal_type"),
        "ppt": master.get("ppt"), "technician": master.get("technician"),
        "overrideType": master.get("manager_override_type"),
        "gps": {"x": master.get("gps_x"), "y": master.get("gps_y")},
        "active": master.get("active"),
        "technicianVisits": len(tech), "ozVisits": len(oz), "totalVisits": len(visits),
        "lastTechnicianVisit": (last(tech) or {}).get("visit_date"),
        "lastOzVisit": (last(oz) or {}).get("visit_date"),
        "recommendedCadenceWeeks": recommended_weeks, "cadenceRule": cadence_id,
        "actualCadenceWeeks": actual_tech_weeks, "cadenceDeviationWeeks": deviation,
        "nextDue": next_due, "daysRemaining": days_remaining, "dueStatus": due_status,
        "trend": trend_series,
        "recommendation": rec,
        "recentVisits": list(reversed(visits))[:20],
    }


def _pos_recommendation(rec_w, act_w, dev, days_rem, due_status, n_tech, n_oz, override):
    if override and str(override).upper() == "FORCE_EXCLUDE":
        return {"tone": "info", "text": "Ručně vyřazeno z plánování (FORCE_EXCLUDE)."}
    if override and str(override).upper() == "FORCE_INCLUDE":
        return {"tone": "warn", "text": "Ručně vynuceno do plánu (FORCE_INCLUDE) – garantovaná priorita."}
    if rec_w is None:
        return {"tone": "info", "text": "POS nemá tvrdou cadenci (GECO/CORN); řídí se zanedbaností a skóre."}
    if due_status == "overdue":
        if days_rem is None:
            return {"tone": "bad", "text": f"Nikdy nenavštíveno technikem, přitom má cadenci {rec_w:g} t. – naplánuj co nejdřív."}
        return {"tone": "bad", "text": f"Po termínu cadence o {abs(days_rem)} dní – zařaď do nejbližšího plánu."}
    if dev is not None and dev < -1.5:
        extra = f" ({n_oz}× i OZ)" if n_oz else ""
        return {"tone": "warn", "text": f"Navštěvováno častěji, než PPT vyžaduje (skutečně á {act_w:g} t. vs doporučeno {rec_w:g} t.){extra} – možná rezerva."}
    if due_status == "dueSoon":
        return {"tone": "warn", "text": f"Splatné do {days_rem} dní – zvaž zařazení."}
    return {"tone": "good", "text": f"V cadenci (skutečně á {act_w if act_w is not None else '?'} t. vs doporučeno {rec_w:g} t.)."}


def pos_visit_summary(pos_id: str) -> dict:
    """Everything the planner/POS Explorer needs about who has been at a POS."""
    counts = {row["visitor_role"] or "UNKNOWN": row["c"] for row in db.get(
        "SELECT visitor_role, COUNT(*) AS c FROM salesapp_visits "
        "WHERE pos_id = ? GROUP BY visitor_role", (pos_id,))}
    recent = [dict(r) for r in db.get(
        "SELECT visit_date, technician, visitor_role, purpose, started_at, finished_at "
        "FROM salesapp_visits WHERE pos_id = ? ORDER BY visit_date DESC LIMIT 20", (pos_id,))]
    return {
        "posId": pos_id,
        "lastTechnicianVisit": _last_visit(pos_id, "TECHNIK"),
        "lastOzVisit": _last_visit(pos_id, "OZ"),
        "technicianVisitCount": counts.get("TECHNIK", 0),
        "ozVisitCount": counts.get("OZ", 0),
        "totalVisitCount": sum(counts.values()),
        "recentVisits": recent,
    }
