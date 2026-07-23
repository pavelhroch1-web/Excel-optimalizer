"""Automatic interpretation of ONE technician vs. peers — sentences, not KPI.

A manager should not read a table and infer things. This answers, in plain
Czech: where the technician is BETTER than peers, where he lags, what to
improve, and which single lever gives the biggest performance gain (quantified
where possible). Everything is relative to the collective peer median, so it is
fair and self-explaining.

Deterministic, read-only over SQLite. Reuses the health-score metrics + the
single-purpose ratio; no new data, no ML.
"""
from __future__ import annotations

import db
import diagnostics

# metric key -> (label, good direction, recommendation when weak)
_METRICS = [
    ("visitsPerDay", "počet návštěv za den", "high",
     "zvýšit počet návštěv za den — plánovat hustěji a slučovat blízké POS"),
    ("onPosRatioPct", "podíl času na POS", "high",
     "zvýšit podíl času na POS — méně času v autě, kompaktnější trasy"),
    ("planFulfilmentPct", "plnění TourPlanu", "high",
     "lépe plnit naplánovaný TourPlan"),
    ("workHoursPerDay", "odpracované hodiny za den", "high",
     "prodloužit efektivní čas v terénu"),
    ("avgOnPosMin", "průměrný čas na jedné POS", "low",
     "zkrátit čas na POS tam, kde je neúměrný"),
    ("singlePurposePct", "podíl jednoúčelových cest", "low",
     "slučovat účely — méně jednoúčelových cest"),
]
_MIN_PCT = 12          # ignore deviations under 12 % (near-average, not a story)


def _single_purpose_ratios() -> dict:
    rows = db.get("SELECT technician, purpose FROM salesapp_visits "
                  "WHERE technician IS NOT NULL AND purpose IS NOT NULL AND purpose<>'' "
                  "AND visitor_role='TECHNIK'")
    tot: dict = {}
    single: dict = {}
    for r in rows:
        t = r["technician"]
        tot[t] = tot.get(t, 0) + 1
        if ";" not in (r["purpose"] or ""):
            single[t] = single.get(t, 0) + 1
    return {t: round(100 * single.get(t, 0) / n, 1) for t, n in tot.items() if n >= 20}


def _median(vals) -> float | None:
    vals = sorted(v for v in vals if v is not None)
    if not vals:
        return None
    n = len(vals)
    m = n // 2
    return vals[m] if n % 2 else (vals[m - 1] + vals[m]) / 2.0


def interpret(name: str, days_back: int = 120) -> dict:
    h = diagnostics.health_scores(days_back, "TECHNIK")
    techs = h.get("technicians", [])
    if not techs:
        return {"technician": name, "found": False}
    sp = _single_purpose_ratios()
    for t in techs:
        t["singlePurposePct"] = sp.get(t["technician"])
    me = next((t for t in techs if t["technician"] == name), None)
    if not me:
        return {"technician": name, "found": False, "reason": "insufficient"}

    med = {k: _median([t.get(k) for t in techs if t["technician"] != name])
           for k, *_ in _METRICS}

    strengths, weaknesses = [], []
    worst = []                       # (abs pct, spec) for biggest-impact pick
    for key, label, good, rec in _METRICS:
        val, m = me.get(key), med.get(key)
        if val is None or m is None or m == 0:
            continue
        pct = round(100 * (val - m) / abs(m))
        if abs(pct) < _MIN_PCT:
            continue
        favorable = (good == "high" and val > m) or (good == "low" and val < m)
        sign = "+" if pct > 0 else ""
        sentence = f"{label}: {round(val, 1)} vs. průměr {round(m, 1)} ({sign}{pct} %)"
        item = {"metric": key, "label": label, "sentence": sentence,
                "value": round(val, 1), "peerMedian": round(m, 1), "pct": pct}
        if favorable:
            strengths.append(item)
        else:
            item["recommendation"] = rec
            weaknesses.append(item)
            worst.append((abs(pct), key, label, val, m, rec))

    strengths.sort(key=lambda x: -abs(x["pct"]))
    weaknesses.sort(key=lambda x: -abs(x["pct"]))

    biggest = None
    if worst:
        worst.sort(reverse=True)
        _, key, label, val, m, rec = worst[0]
        est = None
        if key == "visitsPerDay":
            gain = round((m - val) * 5, 1)          # 5 work days -> per week
            if gain > 0:
                est = f"přibližně +{gain} návštěv týdně, kdyby se dostal na průměr týmu"
        biggest = {"metric": key,
                   "sentence": f"Největší přínos: {rec}." + (f" ({est})" if est else ""),
                   "estimate": est}

    return {"technician": name, "found": True, "daysBack": days_back,
            "healthScore": me.get("healthScore"), "region": me.get("region"),
            "strengths": strengths, "weaknesses": weaknesses,
            "improvements": [w["recommendation"] for w in weaknesses],
            "biggestImpact": biggest}
