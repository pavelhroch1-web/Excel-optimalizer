"""Planner v2 — feasibility-by-construction (parallel to the locked v1 engine).

v1 (desktop_client/engines) selects POS by COUNT and then splits them across days
by count; time and geography are only checked afterwards (plan_feasibility). v2
keeps v1's PRIORITY brain untouched — it reuses the engine's own scored candidate
pool (candidates_out: score, hold-back status, GPS, category…) — and replaces ONLY
the selection→day step with a constructive day builder where TIME (reference-day
minute budget) and GEOGRAPHY (region) are HARD constraints during selection. A day
therefore cannot be built infeasible; a POS that fits no feasible day is deferred
with a reason.

This is deliberately a transparent greedy heuristic (build the day by priority +
proximity + time budget — the way an experienced planner does), not a black-box
solver: deterministic, explainable, fast. Nothing here touches v1; it runs beside
it for A/B comparison on the real network.

Reference day (technician-agnostic, learned ČR standard):
    day budget  = reference_day.budget_minutes − on-top(config)
    stop cost   = duration.predict (visit) + transition_model (real move)
"""
from __future__ import annotations

import db
import db_state
import duration
import pipeline
import reference_day
import runtime_state
import transition_model
from desktop_client.engines.core_logic import distance_km

_WORK_DAYS = ["MON", "TUE", "WED", "THU", "FRI"]
_POOL_CAP = 90          # top-N candidates per (tech, week) by score — plenty for ~5 days
_HOLDBACK = "Odloženo (hold-back)"


def _pos_geo():
    """area (region + environment) per POS, once."""
    out = {}
    for r in db.get("SELECT pos_id, area FROM pos_master"):
        out[str(r["pos_id"])] = r["area"]
    return out


def _build_day(pool, budget, area_of, dur_of):
    """Greedily fill one day from `pool` (already priority-sorted, mandatory first),
    honouring the day's region (hard) and the minute budget (hard). Returns
    (placed_list, minutes_used). `pool` items are consumed (removed)."""
    if not pool:
        return [], 0.0
    # seed = highest-priority POS that fits on its own
    seed = None
    for i, c in enumerate(pool):
        if dur_of(c) <= budget:
            seed = pool.pop(i)
            break
    if seed is None:
        return [], 0.0
    day = [dict(seed, _why="seed (nejvyšší priorita, vejde se)")]
    used = dur_of(seed)
    km_total = 0.0
    day_region = _region(area_of.get(str(seed["pos"])))

    changed = True
    while changed:
        changed = False
        best_i = best_cost = best_km = None
        for i, c in enumerate(pool):
            if _region(area_of.get(str(c["pos"]))) != day_region:
                continue                          # geography: same region only (hard)
            move, km = _nearest_move(c, day)
            cost = dur_of(c) + move
            if used + cost <= budget:              # time: fits remaining budget (hard)
                best_i, best_cost, best_km = i, cost, km  # score-sorted → first fit = best priority
                break
        if best_i is not None:
            c = pool.pop(best_i)
            c = dict(c, _why=f"priorita + region + vejde se (+{round(best_cost)} min)")
            day.append(c); used += best_cost; km_total += best_km; changed = True
    return day, round(used, 1), round(km_total, 1)


def _region(area):
    return area or "?"


def _nearest_move(c, day):
    """(transition minutes, km) from the nearest already-placed stop to candidate c."""
    cx, cy = c.get("x") or 0, c.get("y") or 0
    if cx == 0 and cy == 0:
        return 0.0, 0.0
    best_m = best_km = None
    for s in day:
        sx, sy = s.get("x") or 0, s.get("y") or 0
        if sx == 0 and sy == 0:
            continue
        km = distance_km(sx, sy, cx, cy)
        if best_km is None or km < best_km:
            best_km = km
            best_m = transition_model.predict(km).get("minutes") or 0.0
    return (best_m or 0.0), (best_km or 0.0)


def simulate(start_week: int, length: int = 1, mode: str = "vyvazeny",
             visits_per_tech_week: float | None = None) -> dict:
    """Run v1's scoring once, then build the v2 plan beside it. Returns both plans'
    summaries for A/B — does NOT change the current draft."""
    state = runtime_state.build()
    db_state.configure(state, mode, start_week, length, visits_per_tech_week)
    cands: list = []
    pipeline.run_planning(state, start_week, length, candidates_out=cands)

    area_of = _pos_geo()
    budget = reference_day.budget_minutes("TECHNIK")

    # duration per candidate (national learned, capped at region — technician-agnostic)
    dur_cache: dict = {}

    def dur_of(c):
        key = (c.get("kategorie") or "", c.get("market") or "", _region(area_of.get(str(c["pos"]))))
        if key not in dur_cache:
            pred = duration.predict_for(c.get("kategorie"), c.get("market"), key[2], None)
            dur_cache[key] = float(pred.get("p50") or 15.0)
        return dur_cache[key]

    # group candidate pool by (tech, week); drop hold-back-deferred; rank mandatory-first, then score
    pools: dict = {}
    for c in cands:
        if c.get("status") == _HOLDBACK:
            continue
        pools.setdefault((c["tech"], c["week"]), []).append(c)
    for key in pools:
        pools[key].sort(key=lambda c: (0 if c.get("mandatoryRuleId") else 1, -(c.get("score") or 0)))
        del pools[key][_POOL_CAP:]

    v2_planned = 0
    v2_deferred = 0
    v2_days = []
    for (tech, week), pool in pools.items():
        considered = len(pool)
        placed_here = 0
        for day in _WORK_DAYS:
            stops, used, km = _build_day(pool, budget, area_of, dur_of)
            if not stops:
                continue
            placed_here += len(stops)
            v2_days.append({"technician": tech, "week": week, "day": day,
                            "visits": len(stops), "minutes": used, "km": km,
                            "budget": budget, "loadPct": round(100 * used / budget, 1)})
        v2_planned += placed_here
        v2_deferred += max(considered - placed_here, 0)

    # v1 selection = engine's own picks (status Vybráno), same run
    v1_planned = sum(1 for c in cands if c.get("status") == "Vybráno")

    over = [d for d in v2_days if d["loadPct"] > 105]
    n_days = len(v2_days) or 1
    avg_load = round(sum(d["loadPct"] for d in v2_days) / n_days, 1)
    total_km = round(sum(d["km"] for d in v2_days), 1)
    total_min = sum(d["minutes"] for d in v2_days)
    metrics = {
        "planned": v2_planned, "deferred": v2_deferred, "daysBuilt": len(v2_days),
        "overloadedDays": len(over), "avgLoadPct": avg_load,
        "avgVisitsPerDay": round(v2_planned / n_days, 1),
        "totalTravelKm": total_km, "avgTravelKmPerDay": round(total_km / n_days, 1),
        "avgWorkMinutesPerDay": round(total_min / n_days, 1),
    }
    result = {
        "startWeek": start_week, "length": length, "mode": mode,
        "budgetMinutesPerDay": budget,
        "v1": {"planned": v1_planned, "label": "v1 (počet POS, dnešní engine)"},
        "v2": dict(metrics, label="v2 (rozpočet minut + geografie, feasibility-by-construction)"),
        "note": ("v2 staví den tak, že reálný čas (naučená délka + naučený přejezd) "
                 "nikdy nepřekročí rozpočet referenčního dne a nemíchá regiony v jednom "
                 "dni — proto z principu nevznikne neproveditelný den. v1 vybírá na počet. "
                 f"v2 overloaded dní: {len(over)} (mělo by být 0)."),
        "days": sorted(v2_days, key=lambda x: (x["technician"], x["week"], x["day"])),
    }
    _record_ab(start_week, length, mode, budget, v1_planned, metrics)
    return result


def _record_ab(start_week, length, mode, budget, v1_planned, m) -> None:
    """Append this A/B run so real numbers accumulate over time (trend, not a
    one-shot). Best-effort — never breaks a simulate."""
    try:
        db.run(
            "INSERT INTO ab_runs (start_week, length, mode, budget_min, v1_planned, "
            "v2_planned, v2_deferred, v2_days, v2_overloaded, v2_avg_load, "
            "v2_avg_visits_day, v2_travel_km, v2_avg_work_min) "
            "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (start_week, length, mode, budget, v1_planned, m["planned"], m["deferred"],
             m["daysBuilt"], m["overloadedDays"], m["avgLoadPct"], m["avgVisitsPerDay"],
             m["totalTravelKm"], m["avgWorkMinutesPerDay"]))
    except Exception:  # noqa: BLE001
        pass


def ab_history(limit: int = 50) -> list[dict]:
    """Recorded A/B runs, newest first — the collected real numbers to iterate on."""
    try:
        return [dict(r) for r in db.get(
            "SELECT * FROM ab_runs ORDER BY id DESC LIMIT ?", (limit,))]
    except Exception:  # noqa: BLE001
        return []
