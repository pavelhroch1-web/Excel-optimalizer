"""Operational insight layer — anomaly / inefficiency / opportunity discovery.

Not a KPI dump: this surfaces things a manager would NOT notice in daily work,
each with a plain "why", and never proposes a move or decides — it shows, and
lets the manager drill in.

Design is deliberately general and extensible, so any valuable metric, relation
or pattern that helps run the network can be added without a rewrite:

  * benchmark(): a METRIC-AGNOSTIC peer comparison — feed it {entity: value} for
    ANY catalog metric and it returns each entity's z-score / percentile /
    outlier vs its peers (optionally within a group, e.g. region). This alone
    powers "similar visits but far higher travel time", "region differences",
    "long-term drift from the benchmark".
  * a DETECTOR REGISTRY: named detectors over the SalesApp truth, each emitting
    findings (entity, severity, headline, why=[evidence], drill). Adding a
    pattern = adding a detector, never touching the rest.
  * findings are ranked and each carries its evidence, so the cockpit shows the
    signal and the reason together.

SalesApp is the source of truth about the field; this is where we mine it.
"""
from __future__ import annotations

import statistics

import db

# metrics we benchmark across technicians out of the box (all live in the
# metric catalog; adding one here needs no other change). direction says which
# way is a problem.
_BENCH_METRICS = [
    ("km_per_day", "kmPerDay", "high", "km/den"),
    ("travel_per_visit", "travelPerVisit", "high", "min přejezdu / návštěvu"),
    ("on_pos_ratio", "onPosRatioPct", "low", "% času na POS"),
    ("visits_per_work_hour", "visitsPerWorkHour", "low", "návštěv / hodinu"),
    ("avg_on_pos_min", "avgOnPosMin", "high", "min na POS (prům.)"),
    ("long_transfers", "longTransfers", "high", "dlouhé přejezdy"),
]


def benchmark(values: dict, min_n: int = 5, z_flag: float = 1.5) -> dict:
    """Metric-agnostic peer comparison. `values`: {entity_id: number}. Returns
    peer stats + per-entity z-score / percentile / outlier flag. Works for any
    metric — this is the reusable core of all benchmarking."""
    pairs = [(k, float(v)) for k, v in values.items() if v is not None]
    n = len(pairs)
    if n < min_n:
        return {"n": n, "insufficient": True, "entities": {}}
    nums = sorted(v for _, v in pairs)
    median = statistics.median(nums)
    mean = statistics.fmean(nums)
    stdev = statistics.pstdev(nums) or 0.0
    ents = {}
    for k, v in pairs:
        z = (v - mean) / stdev if stdev else 0.0
        pct = round(100 * sum(1 for x in nums if x <= v) / n)
        ents[k] = {"value": round(v, 2), "z": round(z, 2), "percentile": pct,
                   "outlier": abs(z) >= z_flag}
    return {"n": n, "median": round(median, 2), "mean": round(mean, 2),
            "stdev": round(stdev, 2), "entities": ents}


# ---- detector context (computed once, shared by all detectors) -------------

def _context(days_back: int) -> dict:
    import team_analytics
    ov = team_analytics.overview(days_back=days_back)
    techs = [t for t in ov.get("technicians", []) if t.get("visits")]
    # derive a couple of ratio metrics not directly on the overview row
    for t in techs:
        v = t.get("visits") or 0
        t["travelPerVisit"] = round(t["travelMin"] / v, 1) if v else None
    excluded = {r["name"] for r in db.get("SELECT name FROM technicians WHERE excluded=1")}
    return {"overview": ov, "techs": techs, "days_back": days_back, "excluded": excluded}


# ---- detectors: each returns a list of findings ----------------------------

def _finding(detector, entity_id, severity, headline, why, entity_type="technician", drill=None):
    return {"detector": detector, "entityType": entity_type, "entityId": entity_id,
            "severity": severity, "headline": headline, "why": why, "drill": drill or {}}


def _d_benchmark_outliers(ctx) -> list:
    """The generic detector: any benchmarked metric where a technician deviates
    strongly from peers becomes a finding, with the peer median as evidence."""
    techs = ctx["techs"]
    out = []
    # collect per-tech deviations, then emit one finding per tech aggregating them
    per_tech: dict[str, list] = {}
    for mkey, field, bad_dir, label in _BENCH_METRICS:
        bench = benchmark({t["technician"]: t.get(field) for t in techs})
        if bench.get("insufficient"):
            continue
        for tid, e in bench["entities"].items():
            problem = (e["z"] >= 1.5 and bad_dir == "high") or (e["z"] <= -1.5 and bad_dir == "low")
            if problem:
                per_tech.setdefault(tid, []).append({
                    "metric": mkey, "label": label, "value": e["value"],
                    "peerMedian": bench["median"], "z": e["z"], "percentile": e["percentile"]})
    for tid, whys in per_tech.items():
        whys.sort(key=lambda w: -abs(w["z"]))
        sev = "risk" if len(whys) >= 3 or any(abs(w["z"]) >= 2.5 for w in whys) else "warn"
        top = whys[0]
        head = (f"{tid} pracuje dlouhodobě jinak než ostatní — "
                f"{top['label']} {top['value']} vs. medián {top['peerMedian']}"
                + (f" (+{len(whys) - 1} další odchylky)" if len(whys) > 1 else ""))
        out.append(_finding("benchmark_outlier", tid, sev, head, whys,
                            drill={"metrics": [w["metric"] for w in whys]}))
    return out


def _d_single_purpose(ctx) -> list:
    """High share of single-purpose visits — a visit made for one reason only,
    which is often an inefficient use of a trip. Benchmarked across peers."""
    rows = db.get(
        "SELECT technician, purpose FROM salesapp_visits "
        "WHERE technician IS NOT NULL AND purpose IS NOT NULL AND purpose<>'' "
        "AND visitor_role='TECHNIK'")
    tot: dict = {}
    single: dict = {}
    for r in rows:
        t = r["technician"]
        tot[t] = tot.get(t, 0) + 1
        if ";" not in (r["purpose"] or ""):
            single[t] = single.get(t, 0) + 1
    ratios = {t: round(100 * single.get(t, 0) / n, 1) for t, n in tot.items() if n >= 20}
    bench = benchmark(ratios)
    out = []
    if not bench.get("insufficient"):
        for tid, e in bench["entities"].items():
            if e["z"] >= 1.5:
                out.append(_finding("single_purpose", tid, "warn",
                    f"{tid} má vysoký podíl jednoúčelových návštěv — {e['value']}% vs. medián {bench['median']}%",
                    [{"metric": "single_purpose_pct", "label": "% jednoúčelových návštěv",
                      "value": e["value"], "peerMedian": bench["median"], "z": e["z"], "percentile": e["percentile"]}]))
    return out


def _d_repeated_area_returns(ctx) -> list:
    """Repeated returns to the same city/area within one week on different days —
    trips that likely could have been combined. Rate benchmarked across peers."""
    rows = db.get(
        "SELECT technician, city_key, wk, COUNT(DISTINCT visit_date) days FROM ("
        "  SELECT v.technician, COALESCE(p.city, v.store_name) city_key, v.visit_date, "
        "  strftime('%Y-%W', v.visit_date) wk "
        "  FROM salesapp_visits v LEFT JOIN pos_master p ON p.pos_id=v.pos_id "
        "  WHERE v.technician IS NOT NULL AND v.visitor_role='TECHNIK' AND v.visit_date IS NOT NULL) "
        "GROUP BY technician, city_key, wk HAVING days>=2")
    returns: dict = {}
    for r in rows:
        returns[r["technician"]] = returns.get(r["technician"], 0) + 1
    weeks = {r["technician"]: r["w"] for r in db.get(
        "SELECT technician, COUNT(DISTINCT strftime('%Y-%W', visit_date)) w FROM salesapp_visits "
        "WHERE technician IS NOT NULL AND visitor_role='TECHNIK' AND visit_date IS NOT NULL GROUP BY technician")}
    rate = {t: round(c / weeks.get(t, 1), 1) for t, c in returns.items() if weeks.get(t, 0) >= 2}
    bench = benchmark(rate)
    out = []
    if not bench.get("insufficient"):
        for tid, e in bench["entities"].items():
            if e["z"] >= 1.5:
                out.append(_finding("repeated_area_returns", tid, "info",
                    f"{tid} se často vrací do stejné oblasti během týdne — {e['value']}× za týden vs. medián {bench['median']}×",
                    [{"metric": "area_returns_per_week", "label": "návraty do oblasti / týden",
                      "value": e["value"], "peerMedian": bench["median"], "z": e["z"], "percentile": e["percentile"]}]))
    return out


def _d_missed_visibility_combination(ctx) -> list:
    """Visibility is the business priority; a service-only trip made separately
    while a visibility visit was nearby the same week is an avoidable second
    trip. Flags technicians with the most avoidable combining, with the impact
    estimate — never proposing a specific move."""
    import diagnostics
    combos = diagnostics.combination_analysis(ctx["days_back"])
    if not combos:
        return []
    bench = benchmark({t: c["savedKm"] for t, c in combos.items()})
    out = []
    for tid, c in combos.items():
        e = bench.get("entities", {}).get(tid, {}) if not bench.get("insufficient") else {}
        # flag a strong peer outlier, or simply a materially large absolute loss
        if e.get("z", 0) >= 1.5 or c["savedKm"] >= 60:
            out.append(_finding("missed_visibility_combination", tid,
                "warn" if c["savedKm"] >= 60 else "info",
                f"{tid} jezdil na ostatní návštěvy zvlášť poblíž visibilitní (náběh kampaně) — "
                f"potenciál spojit {c['savedTrips']} cest (~{c['savedKm']} km, ~{c['savedMin']} min)",
                [{"metric": "combinable_km", "label": "ušetřitelné km", "value": c["savedKm"],
                  "peerMedian": bench.get("median"), "z": e.get("z", 0), "percentile": e.get("percentile")},
                 {"metric": "combinable_trips", "label": "spojitelné cesty", "value": c["savedTrips"]}],
                drill={"combination": True}))
    return out


# registry — add a detector here and it flows into insights() automatically.
DETECTORS = [_d_benchmark_outliers, _d_missed_visibility_combination,
             _d_single_purpose, _d_repeated_area_returns]

_SEV_RANK = {"risk": 0, "warn": 1, "info": 2}


# A finding says WHAT is wrong; a manager wants WHAT TO DO. Map each detector /
# deviating metric to a plain recommendation + a concrete action the dashboard
# turns into a button (drill to the technician's "Kde ztrácí", jump to cadence
# config, or open planning). Business language, not metrics.
_REC_BY_METRIC = {
    "visits_per_work_hour": ("Zvyšte počet návštěv za den — slučte blízké POS a plánujte hustěji.", "cadence"),
    "travel_per_visit": ("Zkraťte přejezdy — přeskupte oblast a opravte pořadí zastávek.", "plan"),
    "km_per_day": ("Vysoké km/den — zvažte přeskupení oblasti nebo přesun části POS jinému technikovi.", "plan"),
    "on_pos_ratio": ("Nízký podíl času na POS — moc času v autě; přeskupte trasu, ať jsou zastávky blíž.", "plan"),
    "avg_on_pos_min": ("Dlouhé časy na POS — prověřte proč v záložce „Kde ztrácí“.", "tech"),
    "long_transfers": ("Mnoho dlouhých přejezdů — přeskupte oblast, ať jsou POS blíž u sebe.", "plan"),
}
_REC_BY_DETECTOR = {
    "single_purpose": ("Slučte jednoúčelové cesty — plánujte víc účelů na jednu návštěvu.", "plan"),
    "repeated_area_returns": ("Opakované návraty do stejné oblasti — slučte je do jednoho výjezdu.", "plan"),
    "missed_visibility_combination": ("Spojte visibility návštěvy s běžnými — ušetříte samostatné cesty.", "plan"),
    "benchmark_outlier": ("Otevřete rozbor a podívejte se, kde technik ztrácí čas a km.", "tech"),
}
_LEVER_ACTION = {
    "cadence": {"type": "nav", "target": "settings", "label": "Upravit frekvenci"},
    "plan": {"type": "nav", "target": "tourplan", "label": "Naplánovat / přeskupit"},
    "tech": None,  # the drill-to-technician action already covers it
}


def _recommend(f: dict) -> None:
    """Attach `recommendation` + `actions` to a finding in place."""
    rec, lever = None, "tech"
    metric = (f.get("why") or [{}])[0].get("metric") if f.get("why") else None
    if f.get("detector") == "benchmark_outlier" and metric in _REC_BY_METRIC:
        rec, lever = _REC_BY_METRIC[metric]
    elif f.get("detector") in _REC_BY_DETECTOR:
        rec, lever = _REC_BY_DETECTOR[f["detector"]]
    actions = []
    if f.get("entityType") == "technician" and f.get("entityId"):
        actions.append({"type": "technician", "target": f["entityId"], "label": "Otevřít rozbor"})
    lever_act = _LEVER_ACTION.get(lever)
    if lever_act:
        actions.append(lever_act)
    f["recommendation"] = rec or "Otevřete detail a rozhodněte o dalším kroku."
    f["actions"] = actions


def insights(days_back: int = 90) -> dict:
    """Run every detector and return a ranked, explainable list of findings —
    the anomalies / inefficiencies / opportunities to look at, worst first."""
    ctx = _context(days_back)
    findings: list = []
    for det in DETECTORS:
        try:
            findings.extend(det(ctx))
        except Exception as e:  # noqa: BLE001 - one detector must not break the rest
            findings.append(_finding(det.__name__, None, "info",
                                     f"Detektor selhal: {e}", [], entity_type="system"))
    # Drop findings about blacklisted technicians (test/service accounts) — one
    # choke point so every detector honors the exclusion, even those that query
    # salesapp_visits directly.
    excluded = ctx.get("excluded") or set()
    if excluded:
        findings = [f for f in findings
                    if not (f.get("entityType") == "technician" and f.get("entityId") in excluded)]
    findings.sort(key=lambda f: (_SEV_RANK.get(f["severity"], 3),
                                 -max((abs(w.get("z", 0)) for w in f["why"]), default=0)))
    for f in findings:
        _recommend(f)
    return {"daysBack": days_back, "count": len(findings),
            "detectors": [d.__name__ for d in DETECTORS], "findings": findings}
