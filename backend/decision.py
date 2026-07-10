"""Decision Support layer - the "engine that thinks about the plan".

This module adds NO business logic and never changes the Planning Engine. It
only INTERPRETS and SIMULATES over the exact data the engine already
produces:

  recommend()  - turns a POS's own score components (the ones the engine
                 computed) into a plain-language "recommend / don't recommend
                 yet, because ..." read-out.

  what_if()    - runs the engine ONCE for a week (both observability hooks)
                 and derives the impact of manager levers from that single
                 capture. A filter change (partner off, terminal on, category)
                 is a deterministic set operation, so the candidate-count
                 delta is exact - no re-running, no new logic, just counting
                 the engine's own candidate pool and rejection reasons.

Everything here is a view over decisions the Planning Engine already made.
"""
from __future__ import annotations

import copy
from collections import Counter

from desktop_client import xlsx_engine_io
from desktop_client.engines import planning_engine
from desktop_client.engines.core_logic import norm
from desktop_client.engines.mock_workbook import MockWorkbook


# --------------------------------------------------------------------------
# per-POS recommendation (pure interpretation of the engine's components)
# --------------------------------------------------------------------------

def recommend(detail: dict) -> dict:
    """Given a candidates.pos_detail() dict, return {verdict, reasons[]} -
    a plain-language explanation of the engine's OWN decision, built only
    from the score components / status the engine already produced."""
    status = str(detail.get("status", ""))
    ws = detail.get("weeksSinceLastVisit")
    campaigns = detail.get("activeCampaigns") or []
    reasons: list[str] = []

    if status not in ("Vybráno", "Nezařazeno", "Nevybráno") and not status.startswith("Odloženo"):
        return {"verdict": "Nelze vyhodnotit", "reasons": ["POS nebyl v tomto běhu vyhodnocen enginem."]}

    if status == "Vybráno":
        verdict = "Doporučuji naplánovat"
        if detail.get("core"):
            reasons.append("je CORE (garantovaná kategorie)")
        if detail.get("mandatoryRuleId"):
            reasons.append(f"spadá pod povinné pravidlo {detail['mandatoryRuleId']} (musí být navštíven na cadenci)")
        if detail.get("neglectedBonus"):
            reasons.append(f"dlouho nebyl navštíven ({ws} týdnů)")
        if campaigns:
            reasons.append(f"má aktivní kampaň ({len(campaigns)} tento týden)")
        if detail.get("urgencyBoost"):
            reasons.append("blíží se termín návštěvy (urgence)")
        if detail.get("premium"):
            reasons.append("vysoké PPT (top 20 %)")
        if detail.get("gpsBonus"):
            reasons.append("zapadá do trasy (GPS shluk)")
        if not reasons:
            reasons.append("má nejvyšší skóre mezi dostupnými POS")

    elif status.startswith("Odloženo"):
        verdict = "Doporučuji odložit (Smart Hold-back)"
        reasons.append("blíží se kampaň – návštěvu je lepší načasovat na ni")
        if ws is not None:
            reasons.append(f"má ještě rezervu do termínu (naposledy před {ws} týdny)")

    elif status == "Nezařazeno":
        verdict = "Nelze naplánovat (vyřazeno filtrem)"
        if detail.get("rejectReason"):
            reasons.append(detail["rejectReason"])

    else:  # Nevybráno
        verdict = "Zatím nedoporučuji plánovat"
        if (detail.get("gapPenalty") or 0) < 0:
            reasons.append(f"byl navštíven nedávno (nesplněn minimální rozestup, {ws} týdnů)")
        if not campaigns:
            reasons.append("nemá aktivní kampaň")
        if not detail.get("core") and not detail.get("mandatoryRuleId"):
            reasons.append("není prioritní (není CORE ani povinné pravidlo)")
        reasons.append("existují důležitější POS (vyšší skóre vyplnilo kapacitu)")

    return {"verdict": verdict, "reasons": reasons}


def include_lever(detail: dict) -> str | None:
    """For a POS the engine filtered out, the manager lever that would bring
    it back into the candidate pool - read straight from its reject reason."""
    if detail.get("status") != "Nezařazeno":
        return None
    reason = str(detail.get("rejectReason", ""))
    parts = []
    if "vypnutý typ terminálu" in reason:
        parts.append(f"zapnout typ terminálu „{detail.get('terminalType')}“")
    if "vypnutý partner" in reason:
        parts.append(f"zapnout partnera „{detail.get('market')}“")
    if "EXCLUDE" in reason:
        parts.append(f"změnit pravidlo kategorie „{detail.get('kategorie')}“ z EXCLUDE")
    if "blacklist" in reason.lower():
        parts.append("odebrat POS z blacklistu")
    if "FORCE_EXCLUDE" in reason:
        parts.append("zrušit ruční vyřazení (FORCE_EXCLUDE)")
    return " a ".join(parts) if parts else None


# --------------------------------------------------------------------------
# what-if simulation (all derived from ONE engine run's capture)
# --------------------------------------------------------------------------

def _set_control(state: dict, key: str, value) -> None:
    control = state.setdefault("CONTROL", [["KEY", "VALUE", "NOTE"]])
    kn = norm(key)
    for row in control[1:]:
        if norm(str(row[0])) == kn:
            row[1] = value
            return
    control.append([key, value, ""])


def _control_value(state: dict, key: str):
    for row in state.get("CONTROL", [])[1:]:
        if norm(str(row[0])) == norm(key):
            return row[1]
    return None


def what_if(path: str, week: int) -> dict:
    """Runs the engine once for `week`, then derives manager-lever impacts
    from its candidate pool + rejection reasons. Returns a baseline plus a
    list of scenarios with exact candidate-count deltas."""
    state = xlsx_engine_io.read_state(path)
    _set_control(state, "CAMPAIGN_START_WEEK", week)
    _set_control(state, "CAMPAIGN_LENGTH", 1)

    cap: list[dict] = []
    rej: list[dict] = []
    planning_engine.run(MockWorkbook(copy.deepcopy(state)), candidates_out=cap, rejected_out=rej)

    pool = len(cap)
    selected = sum(1 for c in cap if c["status"] == "Vybráno")
    held = sum(1 for c in cap if str(c["status"]).startswith("Odloženo"))

    scenarios: list[dict] = []

    # Partners currently in the pool - turning one off removes EXACTLY its POS
    # (market_ok is exact set membership, so this delta is precise).
    market_counts = Counter(c["market"] for c in cap if c.get("market"))
    for market, cnt in market_counts.most_common(4):
        scenarios.append({
            "lever": "partner_off",
            "label": f"Vypnout partnera „{market}“",
            "impact": f"−{cnt} kandidátů",
            "delta": -cnt,
            "metric": "kandidátů (POS v úvahu)",
            "exact": True,
        })

    # Terminal types that are OFF: POS rejected ONLY for the terminal filter
    # would enter the pool if that type were enabled. terminal_ok uses
    # SUBSTRING matching, so this is a close ESTIMATE, not an exact count.
    term_only = Counter(
        r["terminalType"] for r in rej
        if r.get("rejectReason") == "vypnutý typ terminálu" and r.get("terminalType")
    )
    for ttype, cnt in term_only.most_common(4):
        scenarios.append({
            "lever": "terminal_on",
            "label": f"Povolit typ terminálu „{ttype}“",
            "impact": f"≈ +{cnt} kandidátů",
            "delta": cnt,
            "metric": "kandidátů (POS v úvahu)",
            "exact": False,
        })

    # Minimum-gap: POS currently blocked by the min-rozestup penalty that
    # would unblock at a lower threshold (their weeksSince is already >= it).
    gap_now = _control_value(state, "STANDARD_VISIT_GAP")
    try:
        gap_now = int(float(gap_now))
    except (TypeError, ValueError):
        gap_now = None
    blocked = [c for c in cap if (c.get("gapPenalty") or 0) < 0]
    for target in (6, 4):
        if gap_now is not None and target >= gap_now:
            continue
        unblocked = sum(
            1 for c in blocked
            if c.get("weeksSinceLastVisit") is not None and c["weeksSinceLastVisit"] >= target
        )
        if unblocked:
            frm = f"z {gap_now} " if gap_now else ""
            scenarios.append({
                "lever": "min_gap",
                "label": f"Snížit minimální rozestup {frm}na {target} týdnů",
                "impact": f"≈ +{unblocked} POS přestane blokovat pravidlo rozestupu",
                "delta": unblocked,
                "metric": "POS odblokovaných rozestupem",
                "exact": False,
            })

    # Campaign timing: POS currently held back (Smart Hold-back) are exactly
    # those a campaign shift would re-decide.
    if held:
        scenarios.append({
            "lever": "campaign_shift",
            "label": "Posunout kampaň o týden",
            "impact": f"{held} POS je teď odloženo kvůli blížící se kampani – posun by tato rozhodnutí přepočítal",
            "delta": held,
            "metric": "POS ovlivněných hold-backem",
            "exact": False,
        })

    return {
        "week": week,
        "baseline": {"candidates": pool, "selected": selected, "heldBack": held},
        "scenarios": scenarios,
    }
