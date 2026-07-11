"""What stays UNSERVED and WHY - the planner's control surface.

The engine (config-driven, agreed rules) auto-selects the right POS. This runs
it over the horizon with the observability hooks and reports which POS did NOT
get planned, grouped by the reason the engine itself gave:
  - Nevešlo do kapacity        (passed filters, scored, but capacity ran out)
  - Odloženo hold-backem       (deferred because a campaign is near)
  - Pod minimálním rozestupem  (visited too recently; gap penalty)
  - Vyřazeno filtrem/pravidlem (blacklist / FORCE_EXCLUDE / terminal / partner /
                                kategorie EXCLUDE) - counts only
Important POS (CORE / high PPT / cadence-due) are surfaced first so the manager
checks exceptions, not thousands of irrelevant rows. No planning logic here.
"""
from __future__ import annotations

import planner_sim
import db_state

from desktop_client.engines import planning_engine
from desktop_client.engines.mock_workbook import MockWorkbook

_DETAIL_LIMIT = 60


def _importance(c) -> tuple:
    # CORE first, then cadence-due, then PPT desc, then neglect desc
    return (1 if c.get("core") else 0,
            1 if c.get("mandatoryRuleId") else 0,
            c.get("ppt") or 0,
            c.get("weeksSinceLastVisit") or 0)


def unserved(mode: str, start_week: int, length: int,
             visits_per_tech_week: float | None = None,
             tech_count: int | None = None) -> dict:
    state = planner_sim._base_state()
    db_state.configure(state, mode, start_week, length, visits_per_tech_week)

    cands: list = []
    rej: list = []
    wb = MockWorkbook(state)
    planning_engine.run(wb, candidates_out=cands, rejected_out=rej)

    served = {c["pos"] for c in cands if c["status"] == "Vybráno"}

    # candidate-but-never-selected across the horizon -> actionable reason
    groups = {"capacity": [], "holdback": [], "mingap": []}
    seen = set()
    for c in cands:
        pid = c["pos"]
        if pid in served or pid in seen:
            continue
        seen.add(pid)
        if c["status"] == "Odloženo (hold-back)":
            key = "holdback"
        elif (c.get("gapPenalty") or 0) < 0:
            key = "mingap"
        else:
            key = "capacity"
        groups[key].append(c)

    def pack(items):
        items = sorted(items, key=_importance, reverse=True)
        head = [{
            "pos": c["pos"], "nazev": c.get("nazev"), "kategorie": c.get("kategorie"),
            "market": c.get("market"), "ppt": c.get("ppt"), "core": c.get("core"),
            "cadence": c.get("mandatoryRuleId"), "weeksSinceLastVisit": c.get("weeksSinceLastVisit"),
            "score": c.get("score"),
        } for c in items[:_DETAIL_LIMIT]]
        core = sum(1 for c in items if c.get("core"))
        cadence = sum(1 for c in items if c.get("mandatoryRuleId"))
        return {"count": len(items), "core": core, "cadenceDue": cadence, "items": head}

    # filter-stage rejections: counts by reason (skip inactive/closed noise in
    # the headline, but still count it).
    rej_counts: dict = {}
    for r in rej:
        reason = r.get("rejectReason", "?")
        rej_counts[reason] = rej_counts.get(reason, 0) + 1

    return {
        "scenario": {"mode": mode, "startWeek": start_week, "length": length,
                     "visitsPerTechWeek": visits_per_tech_week},
        "served": len(served),
        "unservedActionable": {
            "capacity": pack(groups["capacity"]),
            "holdback": pack(groups["holdback"]),
            "mingap": pack(groups["mingap"]),
        },
        "filteredByRule": [{"reason": k, "count": v}
                           for k, v in sorted(rej_counts.items(), key=lambda x: -x[1])],
    }
