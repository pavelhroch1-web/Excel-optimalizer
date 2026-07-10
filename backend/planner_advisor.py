"""Planner advisor - turns the assess() numbers into ANSWERS.

Not another KPI dump: this reads a scenario's assessment and says whether the
plan makes business sense, what its weakest link is, what limits further
growth, and what to change to hit a goal. Pure interpretation over existing
metrics (config-driven engine decides; nothing here changes the algorithm).
"""
from __future__ import annotations

import math

import planner_sim


def _region_imbalance(per_region: list[dict]) -> dict | None:
    if not per_region:
        return None
    loads = [r["visits"] for r in per_region]
    avg = sum(loads) / len(loads)
    top = per_region[0]
    ratio = round(top["visits"] / avg, 2) if avg else None
    return {"topRegion": top["region"], "topVisits": top["visits"],
            "avg": round(avg, 1), "ratio": ratio,
            "overloaded": bool(ratio and ratio >= 2.0)}


def interpret(a: dict) -> dict:
    """verdict + weakest link + binding constraint + recommendations."""
    cap = a["capacity"]
    util = cap.get("utilizationPct")
    over = cap.get("overloadedTechnicians", 0)
    under = cap.get("underloadedTechnicians", 0)
    cadence = a["coverage"]["cadence"]
    cadence_gap = max(cadence.get("overdue", 0) - cadence.get("covered", 0), 0)
    region = _region_imbalance(a.get("perRegion", []))  # informational only for now

    # ---- weakest link: only RELIABLE signals (capacity, GECO/CORN, workload).
    # Campaign coverage is pending (targets + scope) -> not a decision driver.
    candidates = []
    if util is not None and util > 100:
        candidates.append((util - 100, f"Kapacita přetížená ({util} %) — plán se nevejde do lidí."))
    if cadence_gap:
        candidates.append((cadence_gap, f"{cadence_gap} POS mimo GECO/CORN cadence."))
    if over:
        candidates.append((over, f"{over} techniků přetíženo (nerovnoměrné rozložení práce)."))
    weakest = max(candidates, key=lambda x: x[0])[1] if candidates else "Žádné výrazné slabé místo v rámci ověřených metrik."

    # ---- binding constraint ----
    if util is not None and util > 100:
        binding = "Kapacita techniků (plán přesahuje dostupné návštěvy)."
    elif over:
        binding = (f"Rozložení práce / geografie — {over} techniků přetíženo, "
                   f"i když celková kapacita není vyčerpaná (util {util} %). "
                   "Roste to nerovnoměrně, ne kvůli celkovému nedostatku lidí.")
    elif util is not None and util < 70:
        binding = "Ne kapacita — plán nevyužívá lidi; limit je v cílech/pravidlech (rozsah, priority)."
    else:
        binding = "Vyvážené — žádné jedno tvrdé omezení."

    # ---- verdict (reliable signals) ----
    if (util is not None and util > 110) or cadence_gap:
        level, summary = "risk", "Plán má rizika — viz nejslabší místo a doporučení."
    elif over or (util is not None and util > 100):
        level, summary = "ok", "Plán zhruba sedí, ale má slabá místa ke zlepšení."
    else:
        level, summary = "good", "Plán dává obchodní smysl a je v rámci kapacity."

    # ---- recommendations ----
    recs = []
    if util is not None and util > 100:
        recs.append(f"Kapacita přetížená ({util} %): zvyš návštěvy/technik/týden, přidej techniky, "
                    f"nebo zúži rozsah (méně POS – uber prioritu slabších cílů).")
    if over and under:
        recs.append(f"{over} techniků přetíženo a {under} nevyužito: přerozděl POS/regiony mezi techniky.")
    elif over:
        recs.append(f"{over} techniků přetíženo: vyrovnej rozložení POS/regionů nebo zvyš jejich kapacitu.")
    if cadence_gap:
        recs.append(f"{cadence_gap} POS mimo GECO/CORN: zvyš kapacitu nebo prioritu cadence (Business Rule CADENCE).")
    if util is not None and util < 70 and not recs:
        recs.append(f"Kapacita využita jen na {util} %: můžeš rozšířit cíle (víc POS/kampaní) nebo ubrat techniky.")
    recs.extend(a.get("notes", []))  # e.g. campaign targets pending

    return {"verdict": {"level": level, "summary": summary},
            "weakestLink": weakest, "bindingConstraint": binding,
            "recommendations": recs, "region": region}


def goal_seek(a: dict, clear_neglect_weeks: int | None = None) -> dict:
    """Answer 'what do I need to change to hit a goal' — analytic estimates
    from the projection (no extra engine runs)."""
    out = {}
    proj = a.get("projection", {})
    cap = a["capacity"]
    per_tech = cap.get("visitsPerTechWeek")
    backlog = proj.get("neglectBacklog") or 0
    if clear_neglect_weeks and backlog and per_tech:
        need_weekly = math.ceil(backlog / clear_neglect_weeks)
        need_techs = math.ceil(need_weekly / per_tech)
        out["clearNeglect"] = {
            "targetWeeks": clear_neglect_weeks, "backlog": backlog,
            "neededVisitsPerWeek": need_weekly, "neededTechnicians": need_techs,
            "currentTechnicians": cap.get("technicians"),
        }
    return out


def advise(mode: str, start_week: int, length: int,
           visits_per_tech_week: float | None = None,
           tech_count: int | None = None,
           clear_neglect_weeks: int | None = None) -> dict:
    a = planner_sim.assess(mode, start_week, length, visits_per_tech_week, tech_count)
    a["advice"] = interpret(a)
    gs = goal_seek(a, clear_neglect_weeks)
    if gs:
        a["goalSeek"] = gs
    return a
