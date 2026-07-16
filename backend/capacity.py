"""Planner — recommended daily productive capacity (learned company standard).

The planner must NOT copy current behaviour (one technician works 4 h, another
20 h). Daily capacity is a long-term, company-wide STANDARD: from the aggregated
history of the whole company we compute productive minutes per worked day, drop
the extremes, and take ~p60/p70 as the recommended target. That way the planner
pushes everyone the same direction instead of normalising weak performance.

"Productive minutes" of a day = time on real POS + modelled driving between
them (idle/absence excluded). Computed per role (TECHNIK vs OZ), never per
individual. Deterministic; recomputable cache in `capacity_standard`, refreshed
after every SalesApp import.
"""
from __future__ import annotations

import datetime

import db
import travel_model
from desktop_client.engines.core_logic import distance_km

_CLIP_LO, _CLIP_HI = 1.0, 120.0     # per-visit on-POS minutes (drop pings/artefacts)
_DAY_MIN = 30.0                     # a "worked day" must have at least this much
_TRIM_LO, _TRIM_HI = 0.10, 0.95     # drop the bottom 10% (short/absence) and top 5%


def _dt(s):
    try:
        return datetime.datetime.fromisoformat(str(s))
    except (ValueError, TypeError):
        return None


def _pct(sorted_vals, q):
    if not sorted_vals:
        return None
    return round(sorted_vals[min(int(q * len(sorted_vals)), len(sorted_vals) - 1)], 1)


def _day_productive(rows):
    """rows for one (tech,day), each (started, finished, dur, lat, lon).
    Returns (productive_min, pos_count)."""
    stops = sorted(rows, key=lambda r: _dt(r[0]) or datetime.datetime.max)
    onpos = 0.0
    legs = []
    prev = None
    for st, fin, dur, lat, lon in stops:
        a, b = _dt(st), _dt(fin)
        m = (b - a).total_seconds() / 60.0 if a and b else (float(dur) * 60 if dur not in (None, "") else None)
        if m is not None and _CLIP_LO <= m <= _CLIP_HI:
            onpos += m
        if prev and None not in (prev[0], prev[1], lat, lon):
            legs.append(distance_km(prev[0], prev[1], lat, lon))
        if lat is not None and lon is not None:
            prev = (lat, lon)
    drive = travel_model.minutes_for_legs(legs)
    return onpos + drive, len(stops)


def rebuild() -> dict:
    """Recompute the capacity standard per role and store it."""
    rows = db.get(
        "SELECT v.technician tech, COALESCE(t.role, v.visitor_role) role, date(v.visit_date) d, "
        "v.started_at st, v.finished_at fin, v.real_duration dur, p.gps_x lat, p.gps_y lon "
        "FROM salesapp_visits v JOIN pos_master p ON p.pos_id=v.pos_id "
        "LEFT JOIN technicians t ON t.name=v.technician "
        "WHERE v.started_at IS NOT NULL AND v.visit_date IS NOT NULL")
    from collections import defaultdict
    byday = defaultdict(list)
    role_of = {}
    for r in rows:
        key = (r["tech"], r["d"])
        byday[key].append((r["st"], r["fin"], r["dur"], r["lat"], r["lon"]))
        role_of[key] = (r["role"] or "TECHNIK").upper()

    prod_by_role = defaultdict(list)
    pos_by_role = defaultdict(list)
    for key, day_rows in byday.items():
        prod, pos = _day_productive(day_rows)
        if prod < _DAY_MIN:
            continue                 # absence / near-empty day, excluded
        role = role_of[key] if role_of[key] in ("TECHNIK", "OZ") else "TECHNIK"
        prod_by_role[role].append(prod)
        pos_by_role[role].append(pos)

    db.run("DELETE FROM capacity_standard")
    out = {}
    for role in ("TECHNIK", "OZ"):
        vals = sorted(prod_by_role.get(role, []))
        if len(vals) < 20:
            continue
        lo, hi = int(_TRIM_LO * len(vals)), int(_TRIM_HI * len(vals))
        trimmed = vals[lo:hi] or vals
        posv = sorted(pos_by_role.get(role, []))
        posv_t = posv[int(_TRIM_LO * len(posv)):int(_TRIM_HI * len(posv))] or posv
        rec = {"productive_p50": _pct(trimmed, 0.50), "productive_p60": _pct(trimmed, 0.60),
               "productive_p70": _pct(trimmed, 0.70), "pos_per_day": _pct(posv_t, 0.60),
               "days": len(vals)}
        db.run("INSERT INTO capacity_standard(role, productive_p50, productive_p60, productive_p70, "
               "pos_per_day, days) VALUES(?,?,?,?,?,?)",
               (role, rec["productive_p50"], rec["productive_p60"], rec["productive_p70"],
                rec["pos_per_day"], rec["days"]))
        out[role] = rec
    return {"rebuilt": bool(out), "byRole": out, "trim": [_TRIM_LO, _TRIM_HI]}


def _target_percentile() -> str:
    try:
        import settings
        v = settings.get("planner", "capacityPercentile")
        if v in ("p50", "p60", "p70"):
            return v
    except Exception:  # noqa: BLE001
        pass
    return "p60"


def recommended(role: str = "TECHNIK") -> dict:
    """Recommended productive-minute capacity for a role (learned standard).
    Optional config `planner.capacityPercentile` picks p50/p60/p70; a config
    override on the standard work day can act as a guardrail (not yet wired)."""
    r = db.get("SELECT * FROM capacity_standard WHERE role=?", (role.upper(),))
    if not r:
        return {"role": role, "found": False}
    row = r[0]
    pct = _target_percentile()
    val = {"p50": row["productive_p50"], "p60": row["productive_p60"], "p70": row["productive_p70"]}[pct]
    return {"role": role, "found": True, "targetPercentile": pct,
            "productiveMinutes": val, "productiveHours": round(val / 60.0, 1),
            "posPerDay": row["pos_per_day"], "days": row["days"],
            "p50": row["productive_p50"], "p60": row["productive_p60"], "p70": row["productive_p70"]}


def overview() -> dict:
    rows = db.get("SELECT * FROM capacity_standard")
    return {"targetPercentile": _target_percentile(),
            "roles": {r["role"]: {"p50": r["productive_p50"], "p60": r["productive_p60"],
                                  "p70": r["productive_p70"], "posPerDay": r["pos_per_day"],
                                  "days": r["days"]} for r in rows}}
