"""Planner — learned TRANSITION model (collective, nationwide).

The real cost of moving from one stop to the next: drive + parking + walking +
normal overhead between two consecutive visits. Learned from SalesApp history
(gap between one visit's finished_at and the next visit's started_at, with the
two POS' GPS from pos_master), so the planner sizes the day on what a move really
costs — not on a constant ×1.35 crow-flight guess that underestimates reality
1.4–6× (measured on 13.8k real legs).

Design principles (agreed with product owner):
  * OBJECTIVE predictors only — distance band × environment (city/rural) × region.
    Deliberately NOT daypart and NOT the individual technician: those encode
    fatigue / admin drift / individual (in)efficiency, which we do not want to
    bake in as the "correct" plan target.
  * Ambitious-but-achievable target — we store the median (p50, reference) but
    PLAN on an ambitious quantile (_TARGET_Q) so the reference day pulls
    technicians up over time instead of copying today's behaviour. Same spirit
    as capacity.py ("normal-to-slightly-above-average").
  * Extreme-trimming + shrinkage toward the parent level, like duration.py.
  * Pluggable — one stable interface (`predict`) for the planner. Adding a
    predictor (traffic density, road type, …) is a new LEVEL here; the planner
    never changes. That is the whole point of the separate layer.

Deterministic and transparent; no ML library. Recomputable cache in
`transition_model`, rebuilt from history.
"""
from __future__ import annotations

import math
import statistics
from collections import defaultdict

import db
import travel_model

# Distance bands (km). Fixed, monotone; the primary axis of every level.
_BANDS = [(0, 1), (1, 3), (3, 7), (7, 15), (15, 30), (30, 60), (60, 200)]
_GAP_LO, _GAP_HI = 0.5, 180.0   # minutes — drop same-second pings, lunches, overnights
_KM_HI = 200.0                  # drop cross-country artefacts
_SHRINK_K = 30.0                # pseudo-count pulling a thin cell toward its parent
_MIN_N = 8                      # a cell needs at least this many legs to be trusted
# Ambitious-but-achievable planning quantile. p45 = a good realistic move that
# ~45% of real (objective-conditioned) legs already achieve → pulls up without
# becoming fantasy. Tunable; the median is stored alongside for transparency.
_TARGET_Q = 0.45


def band_index(km: float) -> int:
    for i, (lo, hi) in enumerate(_BANDS):
        if lo <= km < hi:
            return i
    return len(_BANDS) - 1 if km >= _BANDS[-1][1] else 0


def environment(area: str | None) -> str:
    """City vs rural from the Czech okres name (objective, not behavioural).
    'Brno-venkov' -> venkov, 'Brno-město' / 'Praha' -> mesto, else unknown."""
    a = (area or "").strip().lower()
    if not a:
        return "?"
    if "venkov" in a:
        return "venkov"
    if "město" in a or "mesto" in a or a.startswith("praha"):
        return "mesto"
    return "?"


def _haversine_km(x1, y1, x2, y2) -> float:
    R = 6371.0
    p1, p2 = math.radians(x1), math.radians(x2)
    dp, dl = math.radians(x2 - x1), math.radians(y2 - y1)
    h = math.sin(dp / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dl / 2) ** 2
    return 2 * R * math.asin(math.sqrt(h))


def _quantile(sorted_vals, q):
    if not sorted_vals:
        return None
    i = min(int(q * len(sorted_vals)), len(sorted_vals) - 1)
    return round(sorted_vals[i], 1)


def _load_legs():
    """Consecutive same-technician same-day legs with real gap minutes + the
    destination POS' distance/environment/region. GPS comes from pos_master (the
    SalesApp export carries times but no coordinates)."""
    rows = db.get(
        "SELECT v.technician tech, date(v.visit_date) d, v.started_at s, v.finished_at f, "
        "p.gps_x x, p.gps_y y, p.area area "
        "FROM salesapp_visits v JOIN pos_master p ON p.pos_id=v.pos_id "
        "WHERE v.started_at IS NOT NULL AND v.finished_at IS NOT NULL "
        "AND p.gps_x IS NOT NULL AND p.gps_y IS NOT NULL AND p.gps_x<>0 AND p.gps_y<>0 "
        "ORDER BY v.technician, d, v.started_at")
    days = defaultdict(list)
    for r in rows:
        days[(r["tech"], r["d"])].append(r)

    legs = []  # (km, gap_min, env, region)
    for seq in days.values():
        for i in range(len(seq) - 1):
            a, b = seq[i], seq[i + 1]
            fin, st = _parse(a["f"]), _parse(b["s"])
            if fin is None or st is None:
                continue
            gap = (st - fin).total_seconds() / 60.0
            km = _haversine_km(a["x"], a["y"], b["x"], b["y"])
            if _GAP_LO <= gap <= _GAP_HI and 0 <= km <= _KM_HI:
                legs.append((km, gap, environment(b["area"]), (b["area"] or "?")))
    return legs


def _parse(s):
    import datetime
    try:
        return datetime.datetime.strptime(str(s)[:19].replace("T", " "), "%Y-%m-%d %H:%M:%S")
    except (ValueError, TypeError):
        return None


def _key(dims) -> str:
    return "|".join(str(x) for x in dims)


def rebuild() -> dict:
    """Recompute the whole model from history and store it. Returns a summary."""
    legs = _load_legs()
    if not legs:
        return {"rebuilt": False, "reason": "no usable legs"}

    # gather gap-minutes per cell at each level, keyed with the band first
    lvl0 = defaultdict(list)   # band
    lvl1 = defaultdict(list)   # band | env
    lvl2 = defaultdict(list)   # band | env | region
    for km, gap, env, region in legs:
        bi = band_index(km)
        lvl0[(bi,)].append(gap)
        lvl1[(bi, env)].append(gap)
        lvl2[(bi, env, region)].append(gap)

    rows = []
    shrunk = {}  # (level, cell) -> ambitious minutes (shrinkage anchor)

    # L0: per band, national — enough data, no parent (anchor of the hierarchy)
    for cell, vals in lvl0.items():
        vals = sorted(vals)
        amb, med = _quantile(vals, _TARGET_Q), _quantile(vals, 0.5)
        shrunk[(0, cell)] = amb
        rows.append((0, _key(cell), len(vals), amb, med))

    # L1/L2: shrink toward the parent cell (drop the last dim)
    for lvl, cells in ((1, lvl1), (2, lvl2)):
        for cell, vals in cells.items():
            vals = sorted(vals)
            n = len(vals)
            amb, med = _quantile(vals, _TARGET_Q), _quantile(vals, 0.5)
            parent = shrunk.get((lvl - 1, cell[:-1]))
            if parent is None:
                parent = shrunk.get((0, (cell[0],)), amb)
            s_amb = round((n * amb + _SHRINK_K * parent) / (n + _SHRINK_K), 1)
            shrunk[(lvl, cell)] = s_amb
            rows.append((lvl, _key(cell), n, s_amb, med))

    db.run("DELETE FROM transition_model")
    for r in rows:
        db.run("INSERT INTO transition_model(level, ckey, n, minutes, p50) VALUES(?,?,?,?,?)", r)
    return {"rebuilt": True, "cells": len(rows), "legs": len(legs),
            "targetQuantile": _TARGET_Q, "bands": len(_BANDS)}


def _lookup(level, ckey):
    r = db.get("SELECT n, minutes, p50 FROM transition_model WHERE level=? AND ckey=?", (level, ckey))
    return r[0] if r else None


# ---- the ONE interface the planner uses -----------------------------------

def predict(km: float, environment_kind: str | None = None,
            region: str | None = None) -> dict:
    """Ambitious-but-achievable transition minutes for a move of `km`, optionally
    refined by environment (city/rural) and region. Falls back up the hierarchy
    when a specific cell is thin, and finally to the constant travel model.

    The planner calls only this. Adding a predictor changes the model internals
    (a new level in rebuild), never this signature."""
    if km is None or km < 0:
        return {"minutes": None, "basis": "none", "n": 0}
    bi = band_index(km)
    env = environment_kind or "?"
    reg = region or "?"
    for lvl, cell, name in ((2, (bi, env, reg), "band+env+region"),
                            (1, (bi, env), "band+env"),
                            (0, (bi,), "band")):
        row = _lookup(lvl, _key(cell))
        if row and (lvl == 0 or (row["n"] or 0) >= _MIN_N):
            return {"minutes": row["minutes"], "p50": row["p50"], "n": row["n"],
                    "level": lvl, "basis": name, "kmBand": _BANDS[bi]}
    # last resort: the physical constant model (never learned)
    return {"minutes": travel_model.estimate_minutes(km), "p50": None, "n": 0,
            "level": None, "basis": "constant-fallback", "kmBand": _BANDS[bi]}


def predict_between(x1, y1, x2, y2, region: str | None = None,
                    area_to: str | None = None) -> dict:
    """Convenience for the feasibility / day-builder: minutes to move between two
    GPS points, deriving distance + environment. `area_to` is the destination
    okres (for the city/rural signal); `region` refines the cell."""
    if None in (x1, y1, x2, y2) or (x1 == 0 and y1 == 0) or (x2 == 0 and y2 == 0):
        return {"minutes": None, "basis": "no-gps", "n": 0}
    km = _haversine_km(x1, y1, x2, y2)
    out = predict(km, environment(area_to) if area_to else None, region or area_to)
    out["km"] = round(km, 2)
    return out


def overview() -> dict:
    """Per-band national transition minutes (ambitious vs median) next to the old
    constant model — for the UI / audit, so the improvement gap is visible."""
    out = []
    for i, (lo, hi) in enumerate(_BANDS):
        row = _lookup(0, _key((i,)))
        mid = (lo + hi) / 2 if hi < 200 else 80
        out.append({
            "band": f"{lo}–{hi} km", "n": row["n"] if row else 0,
            "ambitious": row["minutes"] if row else None,
            "median": row["p50"] if row else None,
            "constantModel": travel_model.estimate_minutes(mid),
        })
    return {"targetQuantile": _TARGET_Q, "minN": _MIN_N, "shrinkK": _SHRINK_K,
            "byBand": out}
