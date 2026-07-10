"""Planner simulation / decision-support - run the engine under a scenario and
MEASURE the outcome. No planning logic here: the engine (config-driven via
db_state) decides; this reads its output and computes workload/region/coverage
so the manager can see what a config or capacity change would do.

Everything is scenario = config: mode + capacity + (future) rule/weight
overrides feed db_state; the algorithm never changes.
"""
from __future__ import annotations

import copy

import brain
import db
import db_state
import pipeline
import settings
import store
import state_xlsx

from desktop_client.engines import planning_engine
from desktop_client.engines.mock_workbook import MockWorkbook

_DAY = {"MON": 1, "TUE": 2, "WED": 3, "THU": 4, "FRI": 5}


def _base_state():
    """Snapshot state prepared for a HYPOTHETICAL replan: keep POS_MASTER /
    config / last-visit reality, but clear any prior plan + week locks so the
    scenario plans the whole horizon fresh (a simulation, never persisted)."""
    path = store.snapshot_temp()
    try:
        state = state_xlsx.load_state(path)
    finally:
        import os
        os.remove(path)
    for sheet in ("MANAGER_PLAN", "MANAGER_PLAN_PUBLISHED", "PLAN_LIFECYCLE"):
        if state.get(sheet):
            state[sheet] = [state[sheet][0]]   # header only
    return state


def _per_week_capacity(visits_per_tech_week):
    if visits_per_tech_week:
        return int(visits_per_tech_week)
    per_day = settings.get("planner", "max_visits_per_day") or 8
    return int(per_day) * 5


def simulate(mode: str, start_week: int, length: int,
             visits_per_tech_week: float | None = None,
             tech_count: int | None = None) -> dict:
    """Run the engine over the horizon under DB config + this scenario, and
    return workload (per technician), region load, and headline coverage."""
    state = _base_state()
    meta = db_state.configure(state, mode, start_week, length, visits_per_tech_week)
    wb = MockWorkbook(state)
    planning_engine.run(wb)
    state.update(wb.dump())

    mp = state.get("MANAGER_PLAN") or []
    if len(mp) < 2:
        return {"scenario": meta, "plannedTotal": 0, "perTechnician": [], "perRegion": []}
    h = {str(n): i for i, n in enumerate(mp[0])}
    rows = [r for r in mp[1:] if r and r[h["WEEK"]] not in (None, "")]

    cap = _per_week_capacity(visits_per_tech_week)
    weeks = sorted({int(r[h["WEEK"]]) for r in rows})

    # per-technician workload
    tech = {}
    for r in rows:
        t = str(r[h["TECHNICIAN"]])
        d = tech.setdefault(t, {"technician": t, "visits": 0, "byWeek": {}})
        d["visits"] += 1
        wk = int(r[h["WEEK"]])
        d["byWeek"][wk] = d["byWeek"].get(wk, 0) + 1
    per_tech = []
    for d in tech.values():
        avg = round(d["visits"] / max(len(d["byWeek"]), 1), 1)
        util = round(100 * avg / cap, 0) if cap else None
        status = "ok"
        if util is not None:
            status = "over" if util > 110 else ("under" if util < 60 else "ok")
        per_tech.append({**d, "avgPerWeek": avg, "capacityPerWeek": cap,
                         "utilizationPct": util, "status": status})
    per_tech.sort(key=lambda x: -x["visits"])

    # region load (OBLAST column)
    reg = {}
    ri = h.get("OBLAST")
    if ri is not None:
        for r in rows:
            k = str(r[ri]) or "—"
            reg[k] = reg.get(k, 0) + 1
    per_region = sorted(({"region": k, "visits": v} for k, v in reg.items()),
                        key=lambda x: -x["visits"])

    unique_pos = len({str(r[h["POS"]]) for r in rows})
    return {
        "scenario": {**meta, "startWeek": start_week, "length": length,
                     "visitsPerTechWeek": visits_per_tech_week, "capacityPerWeek": cap,
                     "techCount": tech_count or len(tech)},
        "plannedTotal": len(rows),
        "uniquePos": unique_pos,
        "plannedByWeek": {w: sum(1 for r in rows if int(r[h["WEEK"]]) == w) for w in weeks},
        "perTechnician": per_tech,
        "perRegion": per_region,
    }


def what_if(base: dict, scenario: dict) -> dict:
    """Run two scenarios and return both + deltas on headline numbers.
    Each dict: {mode,start_week,length,visits_per_tech_week,tech_count}."""
    a = simulate(**base)
    b = simulate(**scenario)
    delta = {
        "plannedTotal": b["plannedTotal"] - a["plannedTotal"],
        "uniquePos": b["uniquePos"] - a["uniquePos"],
        "technicians": len(b["perTechnician"]) - len(a["perTechnician"]),
    }
    return {"base": a, "scenario": b, "delta": delta}
