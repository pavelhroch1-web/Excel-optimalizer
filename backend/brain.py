"""Field Brain - the manager brain ON TOP of the (unchanged) Planning Engine.

Division of responsibility (product owner's vision):
  Planning Engine = EXECUTION: given this week's rules, build the best tour
                    plan (the "how").
  Field Brain     = GOALS: decide what the objective is, over a multi-week
                    horizon, and explain the business consequences (the
                    "what" / "why").

The Brain adds NO selection logic and never edits the engine. It works by:
  1. STRATEGY MODES - named presets that only change weights/goals (which
     campaigns are active, neglect emphasis, capacity), never the algorithm.
  2. CAPACITY as a real calculation - technicians x visits/tech x weeks - so
     the manager sees what is actually achievable, not a guess.
  3. PRE-FLIGHT SCORECARD - simulate the horizon with the UNCHANGED engine
     (one multi-week run) and COMPUTE per-objective coverage (CORE, cadence
     GECO/CORN, neglect backlog, campaign coverage, capacity utilisation)
     BEFORE the plan is generated, so it can be approved up front.
  4. A plain-language MANAGERIAL RECOMMENDATION derived from those numbers.

Everything is interpretation + simulation over data the engine already
produces. Publishing stays immutable; the Brain only advises.
"""
from __future__ import annotations

import copy

from desktop_client import xlsx_engine_io
from desktop_client.engines import planning_engine
from desktop_client.engines.core_logic import category_rule, norm
from desktop_client.engines.mock_workbook import MockWorkbook

# Cadence gaps (weeks) for the hard recurring rules - read from CADENCE_RULES
# at runtime; these are only the fallback if a rule is missing.
_DEFAULT_NEGLECTED_AFTER = 26


# --------------------------------------------------------------------------
# Strategy modes: presets that change GOALS/WEIGHTS via config only.
# --------------------------------------------------------------------------

STRATEGY_MODES = {
    "dojezd": {
        "label": "Dojezd sítě",
        "desc": "Priorita: dokončit dlouho nenavštívené POS. Kampaně se nechrání "
                "(nezdržuje se hold-backem), kapacita se plní podle zanedbanosti a skóre. "
                "Mandatorní cadence (GECO/CORN) platí vždy.",
        "campaigns_active": False,   # clears ACTIVITY_PLAN windows -> no hold-back
    },
    "kampan": {
        "label": "Kampaňový režim",
        "desc": "Priorita: připravit a pokrýt kampaně. Smart Hold-back chrání vhodné "
                "POS pro nadcházející kampaně; silné POS se nespotřebují předčasně.",
        "campaigns_active": True,
    },
    "vyvazeny": {
        "label": "Vyvážený režim",
        "desc": "Výchozí chování enginu: kampaně aktivní, ale bez extra důrazu - "
                "kombinuje cadence, neglect i kampaně podle skóre.",
        "campaigns_active": True,
    },
}


def _set_control(state: dict, key: str, value) -> None:
    control = state.setdefault("CONTROL", [["KEY", "VALUE", "NOTE"]])
    kn = norm(key)
    for row in control[1:]:
        if norm(str(row[0])) == kn:
            row[1] = value
            return
    control.append([key, value, ""])


def _control_value(state: dict, key: str, default=None):
    for row in state.get("CONTROL", [])[1:]:
        if norm(str(row[0])) == norm(key):
            return row[1]
    return default


def apply_mode(state: dict, mode: str) -> dict:
    """Applies a strategy mode to `state` in place (config only). Returns the
    mode meta. Unknown mode -> 'vyvazeny'."""
    meta = STRATEGY_MODES.get(mode, STRATEGY_MODES["vyvazeny"])
    if not meta["campaigns_active"]:
        # Cleanup mode: remove ACTIVITY_PLAN windows so Smart Hold-back / the
        # campaign-change premium hold do not defer POS - the engine then fills
        # capacity by neglect/score. Mandatory cadence rules are untouched.
        ap = state.get("ACTIVITY_PLAN")
        if ap and len(ap) > 1:
            h = [str(x) for x in ap[0]]
            if "START_WEEK" in h and "END_WEEK" in h:
                sc, ec = h.index("START_WEEK"), h.index("END_WEEK")
                for row in ap[1:]:
                    row[sc], row[ec] = "", ""
    return meta


def apply_capacity(state: dict, visits_per_tech_week: float | None) -> None:
    """Sets a flat weekly capacity per technician (CONTROL.TARGET_VISITS_WEEK).
    None -> leave the workbook's own capacity model untouched."""
    if visits_per_tech_week is not None and visits_per_tech_week > 0:
        _set_control(state, "TARGET_VISITS_WEEK", visits_per_tech_week)


# --------------------------------------------------------------------------
# Horizon simulation + scorecard
# --------------------------------------------------------------------------

def _sheet_idx(state, sheet):
    rows = state.get(sheet, [])
    return rows, ({str(n): i for i, n in enumerate(rows[0])} if rows else {})


def preflight(path: str | None, start_week: int, length: int, mode: str,
              visits_per_tech_week: float | None, tech_count_override: int | None = None,
              state: dict | None = None) -> dict:
    """Simulate the horizon with the unchanged engine under `mode`+capacity and
    COMPUTE the business scorecard (coverage of every objective) BEFORE the
    plan is committed. Read-only: nothing is persisted. Pass `state` to work on
    an already-assembled engine state (e.g. the SQLite runtime state) and skip
    the xlsx round-trip; otherwise the state is read from `path`."""
    state = state if state is not None else xlsx_engine_io.read_state(path)
    meta = apply_mode(state, mode)
    apply_capacity(state, visits_per_tech_week)
    _set_control(state, "CAMPAIGN_START_WEEK", start_week)
    _set_control(state, "CAMPAIGN_LENGTH", length)

    # POS attributes from POS_MASTER
    pm, pmi = _sheet_idx(state, "POS_MASTER")
    crt = [{"key": norm(str(r[0])), "value": norm(str(r[1]))} for r in state.get("CATEGORY_RULES", [])[1:]]

    active, pos_attr = 0, {}
    for r in pm[1:]:
        pid = str(r[pmi["posId"]])
        if not pid or str(r[pmi["status"]]) != "Active":
            continue
        active += 1
        try:
            ws = int(float(r[pmi["weeksSinceLastVisit"]]))
        except (TypeError, ValueError):
            ws = None
        pos_attr[pid] = {
            "ws": ws,
            "core": category_rule(crt, norm(str(r[pmi["category"]]))) == "CORE",
            "market": str(r[pmi["market"]]),
            "category": str(r[pmi["category"]]),
            "street": str(r[pmi["street"]]), "city": str(r[pmi["city"]]),
        }

    # Run the engine once across the whole horizon (multi-week), capturing the
    # candidate pool + rejections.
    cap, rej = [], []
    wb = MockWorkbook(copy.deepcopy(state))
    planning_engine.run(wb, candidates_out=cap, rejected_out=rej)
    planned = {c["pos"] for c in cap if c["status"] == "Vybráno"}
    planned_by_week = {}
    for c in cap:
        if c["status"] == "Vybráno":
            planned_by_week.setdefault(c["week"], set()).add(c["pos"])

    neglected_after = int(float(_control_value(state, "NEGLECTED_AFTER_WEEKS", _DEFAULT_NEGLECTED_AFTER)))

    # --- CORE coverage: of due CORE POS (ws >= neglected_after or ws None),
    #     how many are planned. "Due" = neglected or never measured.
    core_due = [p for p, a in pos_attr.items() if a["core"] and (a["ws"] is None or a["ws"] >= 1)]
    core_planned = [p for p in core_due if p in planned]

    # --- Cadence coverage: the engine tags a POS mandatoryRuleId only when it
    #     is OVERDUE for a HARD recurring rule (GECO/CORN) or due for a
    #     once-per-campaign rule, so the mandatory-tagged set IS the overdue
    #     set. Covered = those actually planned.
    mand_planned = {c["pos"] for c in cap if c["status"] == "Vybráno" and c.get("mandatoryRuleId")}
    cadence_total = len({c["pos"] for c in cap if c.get("mandatoryRuleId")}) + \
        len({r["pos"] for r in rej if r.get("mandatoryRuleId")})
    cadence_planned = len(mand_planned)

    # --- Neglect backlog remaining after the horizon
    neglect_all = [p for p, a in pos_attr.items() if a["ws"] is not None and a["ws"] >= neglected_after]
    neglect_remaining = [p for p in neglect_all if p not in planned]

    # --- Campaign coverage (transparent default: a campaign active in the
    #     horizon is network-wide; demand = ODHAD if present else # active POS;
    #     covered = distinct POS planned during the campaign's weeks).
    campaigns = _campaign_coverage(state, start_week, length, planned_by_week, active)

    # --- Capacity as a real calculation
    techs = sorted({str(r[pmi["assignedTechnician"]]) for r in pm[1:]
                    if str(r[pmi["status"]]) == "Active" and r[pmi["assignedTechnician"]] not in (None, "")})
    n_tech = tech_count_override or len(techs)
    per_tech = visits_per_tech_week if visits_per_tech_week else None
    total_capacity = (n_tech * per_tech * length) if per_tech else None
    used = len(planned)

    scorecard = {
        "mode": mode, "modeLabel": meta["label"], "modeDesc": meta["desc"],
        "startWeek": start_week, "length": length,
        "weeks": list(range(start_week, start_week + length)),
        "activePos": active,
        "capacity": {
            "technicians": n_tech, "visitsPerTechWeek": per_tech, "weeks": length,
            "totalCapacity": total_capacity, "plannedVisits": used,
            "utilizationPct": (round(100 * used / total_capacity, 1) if total_capacity else None),
        },
        "core": {"due": len(core_due), "covered": len(core_planned),
                 "pct": _pct(len(core_planned), len(core_due))},
        "cadence": {"overdue": cadence_total, "covered": cadence_planned,
                    "pct": _pct(cadence_planned, cadence_total)},
        "neglect": {"backlogBefore": len(neglect_all), "remainingAfter": len(neglect_remaining),
                    "cleared": len(neglect_all) - len(neglect_remaining)},
        "campaigns": campaigns,
        "plannedTotal": used,
        "plannedByWeek": {w: len(s) for w, s in sorted(planned_by_week.items())},
    }
    scorecard["recommendation"] = recommendation(scorecard)
    return scorecard


def _campaign_coverage(state, start_week, length, planned_by_week, active_pos):
    rows, idx = _sheet_idx(state, "ACTIVITY_PLAN")
    if not idx or "START_WEEK" not in idx:
        return []
    weeks = set(range(start_week, start_week + length))
    out = []
    for r in rows[1:]:
        try:
            sw, ew = int(float(r[idx["START_WEEK"]])), int(float(r[idx["END_WEEK"]]))
        except (TypeError, ValueError):
            continue
        cw = weeks & set(range(sw, ew + 1))
        if not cw:
            continue
        demand = None
        if "ODHAD_NAVSTEV_ZA_KAMPAN" in idx:
            try:
                demand = int(float(r[idx["ODHAD_NAVSTEV_ZA_KAMPAN"]]))
            except (TypeError, ValueError):
                demand = None
        if not demand:
            demand = active_pos
        covered = len(set().union(*[planned_by_week.get(w, set()) for w in cw])) if cw else 0
        out.append({
            "type": str(r[idx["TYPE"]]) if "TYPE" in idx else "",
            "name": str(r[idx["ACTIVITY"]]) if "ACTIVITY" in idx else "",
            "startWeek": sw, "endWeek": ew,
            "weeksInHorizon": sorted(cw),
            "demand": demand, "covered": covered,
            "pct": _pct(covered, demand), "riskPct": max(0, 100 - _pct(covered, demand)),
        })
    return out


def _pct(a, b):
    return round(100.0 * a / b, 1) if b else 100.0


def recommendation(sc: dict) -> str:
    """Plain-language managerial recommendation from the computed scorecard."""
    parts = []
    cap = sc["capacity"]
    mode = sc["modeLabel"]
    if cap["utilizationPct"] is not None:
        if cap["utilizationPct"] > 100:
            parts.append(
                f"Kapacita nestačí: plán potřebuje {cap['plannedVisits']} návštěv, ale "
                f"{cap['technicians']} techniků × {cap['visitsPerTechWeek']}/týd × {cap['weeks']} = "
                f"{cap['totalCapacity']:.0f}. Doporučuji přidat kapacitu nebo zkrátit rozsah.")
        else:
            parts.append(
                f"Kapacita stačí ({cap['plannedVisits']} z {cap['totalCapacity']:.0f}, "
                f"{cap['utilizationPct']} %).")
    if sc["cadence"]["overdue"]:
        parts.append(f"Cadence (GECO/CORN/9PODNIK) pokryto {sc['cadence']['pct']} %.")
    parts.append(f"Neglect: z {sc['neglect']['backlogBefore']} zanedbaných POS jich plán dojede "
                 f"{sc['neglect']['cleared']}, zůstane {sc['neglect']['remainingAfter']}.")
    for c in sc["campaigns"]:
        flag = "" if c["pct"] >= 90 else "  ⚠ riziko"
        parts.append(f"Kampaň {c['name']} ({c['type']}): pokrytí ≈ {c['pct']} %{flag}.")
    if sc["campaigns"] and any(c["pct"] < 90 for c in sc["campaigns"]):
        parts.append("Doporučení: kampaň v riziku – zvaž přepnutí do Kampaňového režimu dřív, "
                     "přidání kapacity, nebo posun termínu.")
    elif sc["neglect"]["remainingAfter"] > sc["neglect"]["backlogBefore"] * 0.5 and mode != "Dojezd sítě":
        parts.append("Doporučení: zůstává velký neglect – zvaž režim Dojezd sítě v tomto okně.")
    return " ".join(parts)
