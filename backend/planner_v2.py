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
    day_area = area_of.get(str(seed["pos"]))
    day_region = _region(day_area)

    changed = True
    while changed:
        changed = False
        best_i = best_cost = None
        for i, c in enumerate(pool):
            if _region(area_of.get(str(c["pos"]))) != day_region:
                continue                          # geography: same region only (hard)
            move = _nearest_move(c, day, area_of)
            cost = dur_of(c) + move
            if used + cost <= budget:              # time: fits remaining budget (hard)
                best_i, best_cost = i, cost        # pool is score-sorted → first fit = best priority
                break
        if best_i is not None:
            c = pool.pop(best_i)
            c = dict(c, _why=f"priorita + region + vejde se (+{round(best_cost)} min)")
            day.append(c); used += best_cost; changed = True
    return day, round(used, 1)


def _region(area):
    return area or "?"


def _nearest_move(c, day, area_of):
    """Transition minutes from the nearest already-placed stop to candidate c."""
    cx, cy = c.get("x") or 0, c.get("y") or 0
    if cx == 0 and cy == 0:
        return 0.0
    best = None
    for s in day:
        sx, sy = s.get("x") or 0, s.get("y") or 0
        if sx == 0 and sy == 0:
            continue
        km = distance_km(sx, sy, cx, cy)
        m = transition_model.predict(km).get("minutes") or 0.0
        best = m if best is None else min(best, m)
    return best or 0.0


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
            stops, used = _build_day(pool, budget, area_of, dur_of)
            if not stops:
                continue
            placed_here += len(stops)
            v2_days.append({"technician": tech, "week": week, "day": day,
                            "visits": len(stops), "minutes": used,
                            "budget": budget, "loadPct": round(100 * used / budget, 1)})
        v2_planned += placed_here
        v2_deferred += max(considered - placed_here, 0)

    # v1 selection = engine's own picks (status Vybráno), same run
    v1_planned = sum(1 for c in cands if c.get("status") == "Vybráno")

    over = [d for d in v2_days if d["loadPct"] > 105]
    return {
        "startWeek": start_week, "length": length, "mode": mode,
        "budgetMinutesPerDay": budget,
        "v1": {"planned": v1_planned, "label": "v1 (počet POS, dnešní engine)"},
        "v2": {"planned": v2_planned, "deferred": v2_deferred,
               "daysBuilt": len(v2_days), "overloadedDays": len(over),
               "label": "v2 (rozpočet minut + geografie, feasibility-by-construction)"},
        "note": ("v2 staví den tak, že reálný čas (naučená délka + naučený přejezd) "
                 "nikdy nepřekročí rozpočet referenčního dne a nemíchá regiony v jednom "
                 "dni — proto z principu nevznikne neproveditelný den. v1 vybírá na počet. "
                 f"v2 overloaded dní: {len(over)} (mělo by být 0)."),
        "days": sorted(v2_days, key=lambda x: (x["technician"], x["week"], x["day"])),
    }
