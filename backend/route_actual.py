"""Actual driven route from SalesApp - order, km, travel time, on-POS time.

We know the real visit order (started_at / finished_at), so we reconstruct how
a technician actually drove that day: the ordered stops, the leg distance
between consecutive POS (engine distance_km over GPS), the travel time (gap
between one visit finishing and the next starting), and the on-POS time. Feeds
the map and the km/time metrics. Read-only over SQLite. No planning logic.
"""
from __future__ import annotations

import datetime

import db
from desktop_client.engines.core_logic import distance_km


def _dt(s):
    if not s:
        return None
    try:
        return datetime.datetime.fromisoformat(str(s))
    except ValueError:
        for fmt in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%d %H:%M", "%d.%m.%Y %H:%M:%S"):
            try:
                return datetime.datetime.strptime(str(s), fmt)
            except ValueError:
                continue
    return None


def _minutes(a, b):
    if a and b:
        return round((b - a).total_seconds() / 60.0, 1)
    return None


def _classify(store_name, has_gps):
    """What kind of entry is this SalesApp record? Only real stores ("pos")
    count as productive on-POS visits; lunches / offices / prospects are their
    own activities and must not be counted as store time or driving distance.

    Returns (kind, label)."""
    s = (store_name or "").strip()
    low = s.lower()
    if low.startswith("oběd") or low.startswith("obed"):
        return "break", "Oběd (pauza)"
    if "středisko" in low or "stredisko" in low:
        return "office", "Regionální středisko"
    if low.startswith("zájemce") or low.startswith("zajemce"):
        return "prospect", "Zájemce (akvizice)"
    if has_gps:
        return "pos", s or "POS"
    return "other", s or "—"


def technician_route(technician: str, date_from: str | None = None,
                     date_to: str | None = None) -> dict:
    """Actual route per day for a technician, in [date_from, date_to] (YYYY-MM-DD)."""
    gps = {str(r["pos_id"]): (r["gps_x"], r["gps_y"], r["name"], r["city"])
           for r in db.get("SELECT pos_id, gps_x, gps_y, name, city FROM pos_master")}
    q = ("SELECT pos_id, store_name, visit_date, started_at, finished_at, real_duration "
         "FROM salesapp_visits WHERE technician=? AND started_at IS NOT NULL")
    params: list = [technician]
    if date_from:
        q += " AND date(visit_date) >= date(?)"; params.append(date_from)
    if date_to:
        q += " AND date(visit_date) <= date(?)"; params.append(date_to)
    rows = [dict(r) for r in db.get(q, tuple(params))]

    days: dict = {}
    for r in rows:
        day = str(r["visit_date"])[:10]
        days.setdefault(day, []).append(r)

    out_days = []
    for day in sorted(days):
        stops_raw = sorted(days[day], key=lambda r: _dt(r["started_at"]) or datetime.datetime.max)
        stops, legs = [], []
        total_km = travel_min = onpos_min = break_min = admin_min = 0.0
        prev_gps = None           # last stop that had GPS (route anchor)
        interstitial = 0.0        # break/admin minutes since the last GPS stop
        for i, r in enumerate(stops_raw):
            pid = str(r["pos_id"]) if r["pos_id"] else None
            gx, gy, nm, city = gps.get(pid, (None, None, r.get("store_name"), None))
            kind, label = _classify(r.get("store_name"), gx is not None)
            st, fin = _dt(r["started_at"]), _dt(r["finished_at"])
            on = _minutes(st, fin)
            if on is None and r.get("real_duration") not in (None, ""):
                try:
                    on = round(float(r["real_duration"]) * 60, 1)
                except (ValueError, TypeError):
                    on = None
            # only real stores count as productive on-POS time
            if on:
                if kind == "pos":
                    onpos_min += on
                elif kind == "break":
                    break_min += on
                else:
                    admin_min += on
            stop = {"seq": i + 1, "pos": pid, "name": nm if kind == "pos" else label,
                    "city": city, "kind": kind,
                    "lat": gx, "lon": gy, "started": r["started_at"],
                    "finished": r["finished_at"], "onPosMin": on}
            if kind == "pos" and gx is not None:
                if prev_gps is not None:
                    leg_km = round(distance_km(prev_gps["lat"], prev_gps["lon"], gx, gy), 1)
                    total_km += leg_km
                    # travel = wall-clock gap minus any break/admin sitting between
                    gap = _minutes(_dt(prev_gps["finished"]), st)
                    tmin = None
                    if gap is not None:
                        tmin = round(max(gap - interstitial, 0.0), 1)
                        travel_min += tmin
                    legs.append({"fromSeq": prev_gps["seq"], "toSeq": i + 1,
                                 "km": leg_km, "travelMin": tmin})
                prev_gps = stop
                interstitial = 0.0
            elif on:
                interstitial += on
            stops.append(stop)
        starts = [_dt(s["started"]) for s in stops if _dt(s["started"])]
        ends = [_dt(s["finished"]) for s in stops if _dt(s["finished"])]
        work_start = min(starts) if starts else None
        work_end = max(ends) if ends else None
        work_min = _minutes(work_start, work_end)
        pos_count = sum(1 for s in stops if s["kind"] == "pos")
        out_days.append({
            "date": day, "stops": stops, "legs": legs,
            "stopCount": pos_count,
            "totalKm": round(total_km, 1),
            "travelMin": round(travel_min, 1),
            "onPosMin": round(onpos_min, 1),
            "breakMin": round(break_min, 1),
            "adminMin": round(admin_min, 1),
            "workStart": work_start.strftime("%H:%M") if work_start else None,
            "workEnd": work_end.strftime("%H:%M") if work_end else None,
            "workHours": round(work_min / 60.0, 1) if work_min else None,
        })
    return {"technician": technician, "days": out_days,
            "totalKm": round(sum(d["totalKm"] for d in out_days), 1),
            "dayCount": len(out_days)}


def technician_days(technician: str) -> list[str]:
    return [str(r["d"]) for r in db.get(
        "SELECT DISTINCT date(visit_date) AS d FROM salesapp_visits "
        "WHERE technician=? AND started_at IS NOT NULL ORDER BY d DESC", (technician,))]
