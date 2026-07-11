"""Cause analysis — WHY a technician is inefficient, not just THAT they are.

The insight layer flags anomalies; this explains them. For a technician it
reconstructs the real driven days (route_actual) and decomposes the loss of
efficiency into named CAUSES, each with evidence vs peers, then points at the
biggest room for improvement:

  * scattered area        - long average leg between stops
  * bad visit ordering    - actual km vs the optimal open path (excess km)
  * few POS per day        - the route is spread thin
  * single-purpose visits  - trips made for one reason (could combine)
  * isolated visits        - a stop far from the rest of the day (forces a detour)
  * short time in the field - first POS -> last visit span well below peers

It never proposes a move; it says "the long transfers are driven mainly by X,
and better Y would save ~Z km". Read-only over SalesApp. No engine change.
"""
from __future__ import annotations

import datetime
import statistics

import db
import route_actual
from desktop_client.engines.core_logic import GeoPoint, compute_optimal_route_km, distance_km

_ISOLATED_KM = 15.0          # a stop this far from all same-day stops is "isolated"
_profiles_cache: dict = {}


def _mean(xs):
    xs = [x for x in xs if x is not None]
    return round(statistics.fmean(xs), 2) if xs else None


def route_profile(name: str, days_back: int = 90) -> dict | None:
    """Per-technician route efficiency profile aggregated over the window."""
    end = datetime.date.today()
    start = end - datetime.timedelta(days=days_back)
    data = route_actual.technician_route(name, start.isoformat(), end.isoformat())
    days = [d for d in data.get("days", []) if d.get("stops")]
    if not days:
        return None
    pos_counts, spans, travel_shares, leg_kms = [], [], [], []
    tot_actual = tot_optimal = 0.0
    isolated_days = geo_days = 0
    for d in days:
        pos_counts.append(len(d["stops"]))
        spans.append(d.get("workHours"))
        tr, op = d.get("travelMin") or 0, d.get("onPosMin") or 0
        if tr + op > 0:
            travel_shares.append(100 * tr / (tr + op))
        pts = [GeoPoint(s["lat"], s["lon"]) for s in d["stops"]
               if s.get("lat") is not None and s.get("lon") is not None]
        for lg in d.get("legs", []):
            if lg.get("km") is not None:
                leg_kms.append(lg["km"])
        if len(pts) >= 2:
            geo_days += 1
            actual = sum(distance_km(pts[i].x, pts[i].y, pts[i + 1].x, pts[i + 1].y)
                         for i in range(len(pts) - 1))
            optimal = compute_optimal_route_km(pts)
            tot_actual += actual
            tot_optimal += optimal
            for i, a in enumerate(pts):
                nn = min((distance_km(a.x, a.y, b.x, b.y) for j, b in enumerate(pts) if j != i), default=0)
                if nn > _ISOLATED_KM:
                    isolated_days += 1
                    break
    return {
        "technician": name, "days": len(days),
        "posPerDay": _mean(pos_counts),
        "avgLegKm": _mean(leg_kms),
        "workHours": _mean(spans),
        "travelShare": _mean(travel_shares),
        "orderingRatio": round(tot_actual / tot_optimal, 2) if tot_optimal else None,
        "excessKm": round(tot_actual - tot_optimal, 1) if tot_optimal else None,
        "actualKm": round(tot_actual, 1), "optimalKm": round(tot_optimal, 1),
        "isolatedRate": round(isolated_days / geo_days, 2) if geo_days else None,
    }


def _single_purpose_pct(name: str) -> float | None:
    r = db.get(
        "SELECT COUNT(*) tot, SUM(CASE WHEN purpose NOT LIKE '%;%' THEN 1 ELSE 0 END) sp "
        "FROM salesapp_visits WHERE technician=? AND purpose IS NOT NULL AND purpose<>'' "
        "AND visitor_role='TECHNIK'", (name,))
    if not r or not r[0]["tot"]:
        return None
    return round(100 * (r[0]["sp"] or 0) / r[0]["tot"], 1)


def _all_profiles(days_back: int) -> dict:
    if days_back in _profiles_cache:
        return _profiles_cache[days_back]
    names = [r["name"] for r in db.get(
        "SELECT name FROM technicians WHERE role='TECHNIK' AND active=1")]
    profs = {}
    for n in names:
        p = route_profile(n, days_back)
        if p:
            p["singlePurposePct"] = _single_purpose_pct(n)
            profs[n] = p
    _profiles_cache[days_back] = profs
    return profs


# factor definitions: key, label, bad direction, unit, note builder
_FACTORS = [
    ("avgLegKm", "Rozptýlená oblast", "high", "km",
     lambda v, m: f"průměrný přejezd {v} km mezi zastávkami vs. {m} km u ostatních"),
    ("orderingRatio", "Špatné pořadí návštěv", "high", "×",
     lambda v, m: f"reálná trasa je {v}× delší než optimální (medián {m}×)"),
    ("posPerDay", "Málo POS za den", "low", "",
     lambda v, m: f"jen {v} POS/den vs. {m} u ostatních — trasa je řídká"),
    ("singlePurposePct", "Jednoúčelové návštěvy", "high", "%",
     lambda v, m: f"{v}% návštěv za jediným účelem (šly spojit) vs. {m}%"),
    ("isolatedRate", "Izolované návštěvy", "high", "",
     lambda v, m: f"{int(v*100)}% dní obsahuje osamocenou návštěvu daleko od zbytku"),
    ("workHours", "Krátký čas v terénu", "low", "h",
     lambda v, m: f"od první POS po poslední jen {v} h vs. {m} h u ostatních"),
    ("travelShare", "Vysoký podíl času na cestě", "high", "%",
     lambda v, m: f"{v}% času tráví přejezdy vs. {m}% u ostatních"),
]


def diagnose(name: str, days_back: int = 90) -> dict | None:
    """Full cause decomposition for one technician: ranked causes (each vs the
    peer median) + the biggest improvement opportunity."""
    profs = _all_profiles(days_back)
    me = profs.get(name) or route_profile(name, days_back)
    if not me:
        return None
    if "singlePurposePct" not in me:
        me["singlePurposePct"] = _single_purpose_pct(name)
    peers = [p for n, p in profs.items() if n != name]

    def peer_med(k):
        vals = [p.get(k) for p in peers if p.get(k) is not None]
        return round(statistics.median(vals), 2) if vals else None

    causes = []
    for key, label, bad_dir, unit, note in _FACTORS:
        v, m = me.get(key), peer_med(key)
        if v is None or m is None:
            continue
        vals = [p.get(key) for p in peers if p.get(key) is not None]
        sd = statistics.pstdev(vals) if len(vals) > 1 else 0
        z = (v - m) / sd if sd else 0
        abnormal = (z >= 1.0 and bad_dir == "high") or (z <= -1.0 and bad_dir == "low")
        if abnormal:
            causes.append({"factor": key, "label": label, "value": v, "peerMedian": m,
                           "unit": unit, "z": round(z, 2), "severity": abs(z),
                           "note": note(v, m)})
    causes.sort(key=lambda c: -c["severity"])

    opportunity = None
    if me.get("excessKm") and me["excessKm"] > 0 and me.get("orderingRatio", 1) > 1.15:
        wk = me["days"] / 5.0 if me["days"] else 1
        opportunity = {"type": "ordering", "km": me["excessKm"],
                       "note": f"Lepší pořadí návštěv by ušetřilo ~{me['excessKm']} km "
                               f"za období (~{round(me['excessKm']/wk,0):.0f} km/týden)."}
    elif causes and causes[0]["factor"] == "singlePurposePct":
        opportunity = {"type": "combine",
                       "note": "Spojení jednoúčelových návštěv do společných cest sníží počet přejezdů."}

    return {"technician": name, "profile": me, "peerMedians": {k: peer_med(k) for k, *_ in _FACTORS},
            "causes": causes,
            "summary": (f"Hlavní příčina: {causes[0]['label'].lower()} — {causes[0]['note']}."
                        if causes else "Bez výrazné příčiny v rámci sledovaných faktorů."),
            "opportunity": opportunity}


def invalidate_cache():
    _profiles_cache.clear()
