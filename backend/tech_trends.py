"""Fast all-technicians time series for the dashboard graphs.

Aggregated straight from salesapp_visits with SQL (no per-day GPS route
reconstruction — that's reserved for the single-technician deep view), so the
whole team's trends render quickly on a laptop. Filterable by period grain,
date range, region, chain/market and campaign.
"""
from __future__ import annotations

import db

_METRICS = ("visits", "activeDays", "onPosHours", "visitsPerDay", "avgVisitMin")


def _period_expr(grain: str) -> str:
    # visit_date is 'YYYY-MM-DD'. Month = YYYY-MM; week = ISO-ish YYYY-Www.
    if grain == "month":
        return "substr(v.visit_date,1,7)"
    if grain == "day":
        return "v.visit_date"
    return "strftime('%Y-W%W', v.visit_date)"


def filter_options() -> dict:
    """Distinct values for the filter controls."""
    regions = [r["region"] for r in db.get(
        "SELECT region, COUNT(*) c FROM salesapp_visits WHERE region IS NOT NULL AND region<>'' "
        "GROUP BY region ORDER BY c DESC")]
    markets = [r["market"] for r in db.get(
        "SELECT market, COUNT(*) c FROM pos_master WHERE market IS NOT NULL AND market<>'' "
        "GROUP BY market ORDER BY c DESC")]
    campaigns = [r["c"] for r in db.get(
        "SELECT DISTINCT los_activity c FROM salesapp_visits WHERE los_activity IS NOT NULL AND los_activity<>'' "
        "ORDER BY los_activity")]
    return {"regions": regions, "markets": markets, "campaigns": campaigns,
            "metrics": list(_METRICS)}


def all_series(grain: str = "week", date_from: str | None = None, date_to: str | None = None,
               region: str | None = None, market: str | None = None,
               campaign: str | None = None, role: str = "TECHNIK") -> dict:
    """Per-technician time series across all technicians, one row per
    (technician, period). Everything filtered in SQL. Returns:
      { periods: [...ordered...], technicians: [{name, region, total, series:{period:metrics}}],
        metrics, filters:{...} }"""
    where = ["v.technician IS NOT NULL", "v.technician<>''"]
    params: list = []
    if role:
        where.append("v.visitor_role=?"); params.append(role)
    if date_from:
        where.append("v.visit_date>=?"); params.append(date_from)
    if date_to:
        where.append("v.visit_date<=?"); params.append(date_to)
    if region:
        where.append("v.region=?"); params.append(region)
    if campaign:
        where.append("v.los_activity=?"); params.append(campaign)
    join = ""
    if market:
        join = "JOIN pos_master p ON p.pos_id=v.pos_id"
        where.append("p.market=?"); params.append(market)

    pexpr = _period_expr(grain)
    rows = db.get(
        f"SELECT v.technician tech, {pexpr} period, "
        f"COUNT(*) visits, COUNT(DISTINCT v.visit_date) days, "
        f"COALESCE(SUM(v.real_duration),0)*60.0 onpos_min "
        f"FROM salesapp_visits v {join} "
        f"WHERE {' AND '.join(where)} "
        f"GROUP BY v.technician, period ORDER BY period", tuple(params))

    # region label per technician (most frequent)
    treg = {r["technician"]: r["region"] for r in db.get(
        "SELECT technician, region, COUNT(*) c FROM salesapp_visits "
        "WHERE technician IS NOT NULL GROUP BY technician ORDER BY c")}

    periods: list = []
    seen_p: set = set()
    techs: dict = {}
    for r in rows:
        p = r["period"]
        if p not in seen_p:
            seen_p.add(p); periods.append(p)
        t = techs.setdefault(r["tech"], {"name": r["tech"], "region": treg.get(r["tech"], ""),
                                         "total": 0, "series": {}})
        visits, days = r["visits"], r["days"] or 0
        onpos_h = round(r["onpos_min"] / 60.0, 1)
        t["total"] += visits
        t["series"][p] = {
            "visits": visits, "activeDays": days,
            "onPosHours": onpos_h,
            "visitsPerDay": round(visits / days, 2) if days else 0,
            "avgVisitMin": round(r["onpos_min"] / visits, 1) if visits else 0,
        }
    periods.sort()
    out_techs = sorted(techs.values(), key=lambda x: -x["total"])
    return {"grain": grain, "from": date_from, "to": date_to,
            "periods": periods, "metrics": list(_METRICS),
            "technicians": out_techs, "filters": filter_options()}
