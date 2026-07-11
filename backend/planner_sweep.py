"""Predictions / scenarios - the automatic planner's forward view.

Answers the manager's planning questions directly by running the (config-
driven) engine across capacities / technician counts:
  - how many POS get served at 35 / 40 / 45 per technician-week,
  - how many weeks to cover the whole servable network,
  - what changes when capacity or the number of technicians changes.

Servable network = POS that pass the rules (active, not filtered/blacklisted) -
i.e. the ones the engine could ever plan. Weeks-to-cover is a transparent
estimate: network / (unique POS served per week). No planning logic here.
"""
from __future__ import annotations

import math

import planner_sim
import planner_unserved


def _servable_network(mode, start_week, length, cap) -> int:
    """Unique POS the engine could plan (served + not-yet-served candidates).
    Rule-filtered / blacklisted POS are excluded by definition."""
    u = planner_unserved.unserved(mode, start_week, length, cap)
    ua = u["unservedActionable"]
    return u["served"] + ua["capacity"]["count"] + ua["holdback"]["count"] + ua["mingap"]["count"]


def sweep(mode: str, start_week: int, length: int,
          capacities: list[int] | None = None,
          tech_count: int | None = None) -> dict:
    capacities = capacities or [35, 40, 45]
    network = _servable_network(mode, start_week, length, max(capacities))

    rows = []
    for cap in capacities:
        sim = planner_sim.simulate(mode, start_week, length, cap, tech_count)
        unique = sim["uniquePos"]
        per_week = round(unique / length, 0) if length else None
        weeks_to_cover = (math.ceil(network / per_week) if per_week else None)
        rows.append({
            "capacityPerTechWeek": cap,
            "technicians": len(sim["perTechnician"]) or tech_count,
            "plannedVisits": sim["plannedTotal"],
            "uniquePosServed": unique,
            "uniquePerWeek": per_week,
            "coveragePctOfNetwork": (round(100 * unique / network, 1) if network else None),
            "estWeeksToCoverNetwork": weeks_to_cover,
        })
    return {
        "scenario": {"mode": mode, "startWeek": start_week, "length": length,
                     "techCount": tech_count},
        "servableNetwork": network,
        "capacities": rows,
        "assumptions": "Síť = POS, které projdou pravidly (bez uzavřených/"
                       "vyfiltrovaných/blacklistu). Týdny na pokrytí = síť / "
                       "(unikátní POS obsloužené za týden). Bez uvažování nových "
                       "POS a cadence-opakování v čase.",
    }
