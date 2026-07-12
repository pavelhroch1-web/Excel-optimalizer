"""Time series for the technician and the region (středisko), with flexible
time filtering (week or month grain, any date range).

Built on the same honest per-day reconstruction the day view uses
(route_actual): lunch / office / prospect entries are not counted as store
visits, distance is real road km, driving time is modelled. Aggregating those
days into weeks or months gives trends a manager can filter and compare over
any period, for one technician or a whole region.

Read-only over SalesApp. No engine change.
"""
from __future__ import annotations

import datetime
from collections import defaultdict

import db
import route_actual
import travel_model


def _period_key(date_iso: str, grain: str) -> str:
    d = datetime.date.fromisoformat(date_iso[:10])
    if grain == "month":
        return f"{d.year}-{d.month:02d}"
    iso = d.isocalendar()
    return f"{iso[0]}-W{iso[1]:02d}"


def _blank():
    return {"visits": 0, "activeDays": 0, "workHours": 0.0, "onPosHours": 0.0,
            "drivingHours": 0.0, "roadKm": 0.0}


def _fold_day(bucket: dict, d: dict):
    """Add one reconstructed technician-day into a period bucket."""
    pos = d.get("stopCount") or 0
    if not pos:
        return
    bucket["visits"] += pos
    bucket["activeDays"] += 1
    bucket["workHours"] += d.get("workHours") or 0
    bucket["onPosHours"] += (d.get("onPosMin") or 0) / 60.0
    straight_legs = [lg["km"] for lg in d.get("legs", []) if lg.get("km") is not None]
    bucket["drivingHours"] += travel_model.minutes_for_legs(straight_legs) / 60.0
    bucket["roadKm"] += travel_model.road_km(d.get("totalKm"))


def _finalize(periods: dict) -> list:
    out = []
    for key in sorted(periods):
        b = periods[key]
        wh = b["workHours"] or 0
        ad = b["activeDays"] or 0
        out.append({
            "period": key,
            "visits": b["visits"],
            "activeDays": ad,
            "visitsPerDay": round(b["visits"] / ad, 2) if ad else 0,
            "workHours": round(wh, 1),
            "avgWorkHours": round(wh / ad, 1) if ad else 0,
            "onPosHours": round(b["onPosHours"], 1),
            "drivingHours": round(b["drivingHours"], 1),
            "roadKm": round(b["roadKm"], 1),
            "visitsPerWorkHour": round(b["visits"] / wh, 2) if wh else 0,
            "onPosRatioPct": round(100 * b["onPosHours"] / (b["onPosHours"] + b["drivingHours"]), 1)
                             if (b["onPosHours"] + b["drivingHours"]) > 0 else None,
        })
    return out


def _range(days_back, date_from, date_to):
    end = datetime.date.fromisoformat(date_to) if date_to else datetime.date.today()
    start = datetime.date.fromisoformat(date_from) if date_from else end - datetime.timedelta(days=days_back)
    return start.isoformat(), end.isoformat()


def technician_series(name: str, grain: str = "week", days_back: int = 180,
                      date_from: str | None = None, date_to: str | None = None) -> dict:
    start, end = _range(days_back, date_from, date_to)
    route = route_actual.technician_route(name, start, end)
    periods: dict = defaultdict(_blank)
    for d in route.get("days", []):
        if not d.get("stopCount"):
            continue
        _fold_day(periods[_period_key(d["date"], grain)], d)
    return {"entity": name, "entityType": "technician", "grain": grain,
            "from": start, "to": end, "series": _finalize(periods)}


def region_series(region: str, grain: str = "week", days_back: int = 180,
                  date_from: str | None = None, date_to: str | None = None) -> dict:
    start, end = _range(days_back, date_from, date_to)
    techs = [r["technician"] for r in db.get(
        "SELECT DISTINCT technician FROM salesapp_visits WHERE region=? AND technician IS NOT NULL", (region,))]
    periods: dict = defaultdict(_blank)
    for t in techs:
        route = route_actual.technician_route(t, start, end)
        for d in route.get("days", []):
            if not d.get("stopCount"):
                continue
            _fold_day(periods[_period_key(d["date"], grain)], d)
    return {"entity": region, "entityType": "region", "grain": grain,
            "from": start, "to": end, "technicians": len(techs), "series": _finalize(periods)}


def regions() -> list:
    return [r["region"] for r in db.get(
        "SELECT region, COUNT(*) c FROM salesapp_visits "
        "WHERE region IS NOT NULL AND region<>'' GROUP BY region ORDER BY c DESC")]


def series(entity_type: str, entity: str, grain: str = "week", days_back: int = 180,
           date_from: str | None = None, date_to: str | None = None) -> dict:
    if entity_type == "region":
        return region_series(entity, grain, days_back, date_from, date_to)
    return technician_series(entity, grain, days_back, date_from, date_to)
