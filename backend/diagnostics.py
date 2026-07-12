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
import travel_model
from desktop_client.engines.core_logic import GeoPoint, compute_optimal_route_km, distance_km

_ISOLATED_KM = 15.0          # a stop this far from all same-day stops is "isolated"
_COMBINE_KM = 6.0            # two POS within this are the "same micro-area"
_AVG_SPEED_KMH = 45.0        # for turning saved km into saved minutes
_profiles_cache: dict = {}
_combo_cache: dict = {}

# Visibility is THE primary business purpose — the visit that gets planned into
# the TourPlan (campaign launch / "Náběh kampaně"). Every other purpose
# (zásobování, ostatní, kontroly, stahování losů…) is secondary and should
# ideally ride along with a visibility trip in the same area. (Easily made
# configurable later — kept as a named constant reflecting the business rule.)
_VISIBILITY_TOKENS = ("náběh kampaně",)


def _is_visibility(purpose: str) -> bool:
    p = (purpose or "").lower()
    return any(tok in p for tok in _VISIBILITY_TOKENS)


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
    import travel_model
    pos_counts, spans, travel_shares, leg_kms = [], [], [], []
    tot_actual = tot_optimal = 0.0
    tot_travel_min = tot_onpos_min = 0.0
    tot_opt_travel_min = tot_act_model_min = 0.0
    isolated_days = geo_days = 0
    for d in days:
        pos_counts.append(len(d["stops"]))
        spans.append(d.get("workHours"))
        tr, op = d.get("travelMin") or 0, d.get("onPosMin") or 0
        tot_travel_min += tr
        tot_onpos_min += op
        if tr + op > 0:
            travel_shares.append(100 * tr / (tr + op))
        pts = [GeoPoint(s["lat"], s["lon"]) for s in d["stops"]
               if s.get("lat") is not None and s.get("lon") is not None]
        for lg in d.get("legs", []):
            if lg.get("km") is not None:
                leg_kms.append(lg["km"])
        if len(pts) >= 2:
            geo_days += 1
            actual_legs = [distance_km(pts[i].x, pts[i].y, pts[i + 1].x, pts[i + 1].y)
                           for i in range(len(pts) - 1)]
            actual = sum(actual_legs)
            optimal = compute_optimal_route_km(pts)
            tot_actual += actual
            tot_optimal += optimal
            # travel TIME, modelled consistently for actual vs optimal ordering,
            # so the difference isolates the effect of the order alone.
            opt_order = _nn_order(pts)
            opt_legs = [distance_km(pts[opt_order[i]].x, pts[opt_order[i]].y,
                                    pts[opt_order[i + 1]].x, pts[opt_order[i + 1]].y)
                        for i in range(len(opt_order) - 1)]
            tot_act_model_min += travel_model.minutes_for_legs(actual_legs)
            tot_opt_travel_min += travel_model.minutes_for_legs(opt_legs)
            for i, a in enumerate(pts):
                nn = min((distance_km(a.x, a.y, b.x, b.y) for j, b in enumerate(pts) if j != i), default=0)
                if nn > _ISOLATED_KM:
                    isolated_days += 1
                    break
    excess_km = round(tot_actual - tot_optimal, 1) if tot_optimal else None
    saved_min = round(tot_act_model_min - tot_opt_travel_min) if tot_opt_travel_min else None
    return {
        "technician": name, "days": len(days),
        "posPerDay": _mean(pos_counts),
        "avgLegKm": _mean(leg_kms),
        "workHours": _mean(spans),
        "travelShare": _mean(travel_shares),
        "orderingRatio": round(tot_actual / tot_optimal, 2) if tot_optimal else None,
        "excessKm": excess_km, "savedMinOrdering": saved_min,
        "actualKm": round(tot_actual, 1), "optimalKm": round(tot_optimal, 1),
        "travelHoursActual": round(tot_travel_min / 60.0, 1),
        "onPosHoursActual": round(tot_onpos_min / 60.0, 1),
        "isolatedRate": round(isolated_days / geo_days, 2) if geo_days else None,
    }


def _fmt_hm(minutes) -> str:
    m = int(round(minutes or 0))
    return f"{m//60} h {m%60} min" if m >= 60 else f"{m} min"


_CZ_DAYS = ["v pondělí", "v úterý", "ve středu", "ve čtvrtek", "v pátek", "v sobotu", "v neděli"]


def _weekday_cz(date_str) -> str:
    import datetime
    try:
        return _CZ_DAYS[datetime.date.fromisoformat(str(date_str)[:10]).weekday()]
    except (ValueError, TypeError):
        return ""


def _purpose_short(p: str) -> str:
    p = (p or "").replace("Technik - ", "")
    if "zásobování" in p.lower():
        return "zásobování"
    if "ostatní" in p.lower():
        return "ostatní úkony"
    if "kontrola" in p.lower():
        return "kontrolu"
    if "los" in p.lower():
        return "materiály (losy)"
    return p.split(";")[0].strip().lower() or "jiný účel"


def _nn_order(pts) -> list:
    """Nearest-neighbour visit order (for a consistent optimal-time estimate)."""
    remaining = list(range(len(pts)))
    order = [remaining.pop(0)]
    while remaining:
        last = pts[order[-1]]
        nxt = min(remaining, key=lambda i: distance_km(last.x, last.y, pts[i].x, pts[i].y))
        order.append(nxt)
        remaining.remove(nxt)
    return order


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

    # Visibility-combination opportunity (business priority): service trips that
    # could have ridden along with a nearby visibility visit the same week.
    combo = combination_analysis(days_back).get(name)
    if combo:
        causes.append({
            "factor": "missed_visibility_combination", "label": "Promarněné spojení s visibilitou",
            "value": combo["savedTrips"], "peerMedian": None, "unit": "cest", "z": 1.5,
            "severity": 1.5,
            "note": f"{combo['savedTrips']} nevisibilitních cest mohlo jet společně s visibilitní "
                    f"návštěvou (náběh kampaně) poblíž (~{combo['savedKm']} km, ~{combo['savedMin']} min navíc)"})
        causes.sort(key=lambda c: -c["severity"])

    opportunity = None
    if combo and combo["savedKm"] >= 20:
        opportunity = {"type": "visibility_combine", "km": combo["savedKm"], "trips": combo["savedTrips"],
                       "note": f"Ostatní návštěvy plánovat spolu s visibilitními (náběh kampaně) ve stejné "
                               f"oblasti — potenciál ~{combo['savedKm']} km, ~{combo['savedMin']} min a "
                               f"{combo['savedTrips']} jízd za období.", "examples": combo["examples"]}
    elif me.get("excessKm") and me["excessKm"] > 0 and me.get("orderingRatio", 1) > 1.15:
        mins = me.get("savedMinOrdering") or 0
        opportunity = {"type": "ordering", "km": me["excessKm"], "min": mins,
                       "note": f"Kdyby byly stejné návštěvy seřazené optimálně, ušetřilo by se "
                               f"přibližně {me['excessKm']} km"
                               + (f" a {_fmt_hm(mins)}" if mins else "") + " za období."}
    elif causes and causes[0]["factor"] == "singlePurposePct":
        opportunity = {"type": "combine",
                       "note": "Spojení jednoúčelových návštěv do společných cest sníží počet přejezdů."}

    # ---- manager narrative: not "could save X km" but "lost ~N hours, here's
    # the cause, and here's the capacity we'd get back". Time is the headline. ----
    combo_min = combo["savedMin"] if combo else 0
    order_min = me.get("savedMinOrdering") or 0
    lost_hours = round((combo_min + order_min) / 60.0, 1)
    recoverable_hours = round((combo_min / 2 + order_min / 2) / 60.0, 1)
    combo_dominant = combo_min >= order_min
    lever_causes = [c["label"].lower() for c in causes
                    if c["factor"] in ("missed_visibility_combination", "repeatedAreaReturns",
                                       "isolatedRate", "avgLegKm")][:2]
    narrative = None
    if lost_hours >= 2:
        parts = [f"{name} za sledované období pravděpodobně ztratil přibližně "
                 f"{lost_hours:.0f} hodin čistého pracovního času."]
        if combo_dominant and lever_causes:
            parts.append(f"Největší příčinou nejsou samotné dlouhé přejezdy, ale "
                         f"{' a '.join(lever_causes)}.")
        elif causes:
            parts.append(f"Hlavní příčinou je {causes[0]['label'].lower()}.")
        if combo and combo["savedMin"] >= 60:
            parts.append(f"Kdyby se podařilo spojit alespoň polovinu těchto cest s visibilitními "
                         f"návštěvami, získali bychom přibližně {round(combo_min/2/60):.0f} hodin "
                         f"kapacity navíc.")
        narrative = " ".join(parts)

    return {"technician": name, "profile": me, "peerMedians": {k: peer_med(k) for k, *_ in _FACTORS},
            "causes": causes, "combination": combo,
            "lostHours": lost_hours, "recoverableHours": recoverable_hours,
            "lostHoursOrdering": round(order_min / 60.0, 1),
            "lostHoursCombination": round(combo_min / 60.0, 1),
            "narrative": narrative,
            "summary": (f"Hlavní příčina: {causes[0]['label'].lower()} — {causes[0]['note']}."
                        if causes else "Bez výrazné příčiny v rámci sledovaných faktorů."),
            "opportunity": opportunity}


def combination_analysis(days_back: int = 90) -> dict:
    """Missed opportunities to combine trips AROUND visibility visits.

    Visibility (campaign launch) is the business priority and the visit that
    gets planned. A NON-visibility visit made on a separate day, while the same
    technician had a visibility visit to a nearby POS in the same week, is an
    avoidable second trip into that micro-area. We estimate the avoidable detour
    (km / time / trips) - never proposing a specific move, just quantifying the
    wasted potential. Computed once over all technicians."""
    if days_back in _combo_cache:
        return _combo_cache[days_back]
    import datetime
    from collections import defaultdict
    start = (datetime.date.today() - datetime.timedelta(days=days_back)).isoformat()
    rows = db.get(
        "SELECT v.technician t, v.pos_id pos, v.visit_date d, v.purpose pu, "
        "p.city city, p.gps_x gx, p.gps_y gy, strftime('%Y-%W', v.visit_date) wk "
        "FROM salesapp_visits v LEFT JOIN pos_master p ON p.pos_id=v.pos_id "
        "WHERE v.visitor_role='TECHNIK' AND v.visit_date IS NOT NULL "
        "AND v.purpose IS NOT NULL AND v.purpose<>'' AND v.visit_date>=? "
        "AND p.gps_x IS NOT NULL", (start,))
    per = defaultdict(list)
    for r in rows:
        per[r["t"]].append(r)
    out: dict = {}
    for tech, visits in per.items():
        byweek = defaultdict(list)
        for v in visits:
            byweek[v["wk"]].append(v)
        missed, saved_km = [], 0.0
        for wk, vs in byweek.items():
            vis = [v for v in vs if _is_visibility(v["pu"])]
            other = [v for v in vs if not _is_visibility(v["pu"])]
            if not vis or not other:
                continue
            for s in other:
                near = min((V for V in vis if V["d"] != s["d"]),
                           key=lambda V: distance_km(s["gx"], s["gy"], V["gx"], V["gy"]),
                           default=None)
                if not near or distance_km(s["gx"], s["gy"], near["gx"], near["gy"]) > _COMBINE_KM:
                    continue
                # detour proxy: how far s sat from the rest of its own day
                sameday = [o for o in vs if o["d"] == s["d"] and o["pos"] != s["pos"]]
                if sameday:
                    cx = sum(o["gx"] for o in sameday) / len(sameday)
                    cy = sum(o["gy"] for o in sameday) / len(sameday)
                    detour = distance_km(s["gx"], s["gy"], cx, cy)
                else:
                    detour = distance_km(s["gx"], s["gy"], near["gx"], near["gy"])
                sk = min(2 * detour, 80.0)
                saved_km += sk
                area = s["city"] or "stejné oblasti"
                sentence = (f"Do {'oblasti ' + area if s['city'] else area} se jelo "
                            f"{_weekday_cz(near['d'])} kvůli kampani (visibilita) a "
                            f"{_weekday_cz(s['d'])} znovu kvůli {_purpose_short(s['pu'])}. "
                            f"Obě návštěvy šly pravděpodobně spojit.")
                missed.append({"week": wk, "otherPos": s["pos"], "otherPurpose": s["pu"],
                               "visibilityPos": near["pos"], "city": s["city"],
                               "km": round(sk, 1), "minutes": travel_model.estimate_minutes(sk),
                               "apartKm": round(distance_km(s["gx"], s["gy"], near["gx"], near["gy"]), 1),
                               "sentence": sentence})
        if missed:
            trips = len({(m["week"], m["otherPos"]) for m in missed})
            out[tech] = {"missedPairs": len(missed), "savedTrips": trips,
                         "savedKm": round(saved_km, 1),
                         "savedMin": round(sum(m["minutes"] for m in missed)),
                         "examples": sorted(missed, key=lambda m: -m["km"])[:5]}
    _combo_cache[days_back] = out
    return out


# Health Score components: each is a per-technician metric with the direction
# that means "worse", a weight, and a short label for the "why". Higher weight =
# bigger pull on the overall score. Deliberately combines several dimensions so a
# quietly weak technician (few visits, slow on POS, short day, low utilisation)
# surfaces even without extreme transfers.
_HEALTH_COMPS = [
    ("visitsPerDay", "low", 1.4, "málo návštěv/den"),
    ("loadPct", "low", 1.3, "nízké využití kapacity"),
    ("workHoursPerDay", "low", 1.2, "krátká pracovní doba"),
    ("visitsPerWorkHour", "low", 1.2, "nízká produktivita"),
    ("avgOnPosMin", "high", 1.0, "dlouhé časy na POS"),
    ("onPosRatioPct", "low", 0.9, "hodně času na cestě"),
    ("areaReturnsPerWeek", "high", 0.8, "opakované návraty do oblasti"),
]


def _area_returns_per_week() -> dict:
    """Light query: how often a technician returns to the same area on different
    days of the same week (a combining opportunity signal)."""
    rows = db.get(
        "SELECT technician, SUM(CASE WHEN dd>=2 THEN 1 ELSE 0 END)*1.0/COUNT(DISTINCT wk) rate FROM ("
        "  SELECT v.technician technician, COALESCE(p.city, v.store_name) ck, "
        "  strftime('%Y-%W', v.visit_date) wk, COUNT(DISTINCT v.visit_date) dd "
        "  FROM salesapp_visits v LEFT JOIN pos_master p ON p.pos_id=v.pos_id "
        "  WHERE v.visitor_role='TECHNIK' AND v.visit_date IS NOT NULL "
        "  GROUP BY v.technician, ck, wk) GROUP BY technician")
    return {r["technician"]: (r["rate"] or 0) for r in rows}


def health_scores(days_back: int = 90) -> dict:
    """A composite 0-100 Health Score per technician (100 = healthy, low =
    critical). Combines work time, time on POS, visits, travel share, capacity
    utilisation and repeated area returns - so overall weakest technicians
    surface, not just single-metric outliers. Fast (no route reconstruction)."""
    import team_analytics
    ov = team_analytics.overview(days_back=days_back)
    # Only real, active technicians — sparse records (a handful of visits, no
    # timestamps) would otherwise dominate the critical cases with data noise.
    techs = [dict(t) for t in ov.get("technicians", [])
             if (t.get("visits") or 0) >= 30 and (t.get("daysWorked") or 0) >= 10]
    if len(techs) < 5:
        return {"technicians": [], "insufficient": True}
    ar = _area_returns_per_week()
    for t in techs:
        dw = t.get("daysWorked") or 0
        t["visitsPerDay"] = round(t["visits"] / dw, 2) if dw else None
        t["workHoursPerDay"] = round(t["avgWorkHours"] / dw, 2) if (dw and t.get("avgWorkHours")) else None
        t["areaReturnsPerWeek"] = round(ar.get(t["technician"], 0), 2)

    # peer stats per component
    stats = {}
    for field, *_ in _HEALTH_COMPS:
        vals = [t[field] for t in techs if t.get(field) is not None]
        if vals:
            stats[field] = (statistics.median(vals),
                            statistics.pstdev(vals) if len(vals) > 1 else 0)

    out = []
    total_w = sum(w for _, _, w, _ in _HEALTH_COMPS)
    for t in techs:
        badness = 0.0
        why = []
        for field, bad_dir, w, label in _HEALTH_COMPS:
            if field not in stats or t.get(field) is None:
                continue
            med, sd = stats[field]
            if not sd:
                continue
            z = (t[field] - med) / sd
            z_bad = z if bad_dir == "high" else -z
            contrib = max(0.0, min(z_bad, 3.0)) / 3.0  # 0..1
            badness += w * contrib
            if z_bad >= 1.0:
                why.append({"label": label, "value": t[field], "peerMedian": round(med, 1)})
        score = round(100 * (1 - badness / total_w))
        why.sort(key=lambda x: 0)  # keep insertion (weight) order
        out.append({
            "technician": t["technician"], "region": t.get("region"),
            "healthScore": max(0, min(100, score)),
            "visits": t["visits"], "visitsPerDay": t.get("visitsPerDay"),
            "workHoursPerDay": t.get("workHoursPerDay"), "avgOnPosMin": t.get("avgOnPosMin"),
            "onPosRatioPct": t.get("onPosRatioPct"), "loadPct": t.get("loadPct"),
            "why": why[:3],
        })
    out.sort(key=lambda x: x["healthScore"])
    return {"technicians": out, "worst": out[:5]}


def company_overview(days_back: int = 90) -> dict:
    """The whole-company view in the language of TIME: how much net working-time
    capacity the network is losing to avoidable travel, where the reserves are
    (by region), and which technicians represent the biggest opportunity. Time
    is the headline metric; km are secondary."""
    profs = _all_profiles(days_back)
    combos = combination_analysis(days_back)
    # Region comes from the SalesApp truth (Agency region on each visit) — the
    # technicians table isn't reliably filled. Use each technician's most common
    # region (rows arrive most-frequent first per technician; keep the first).
    regions: dict = {}
    for r in db.get(
            "SELECT technician, region, COUNT(*) n FROM salesapp_visits "
            "WHERE technician IS NOT NULL AND region IS NOT NULL AND region<>'' "
            "GROUP BY technician, region ORDER BY technician, n DESC"):
        regions.setdefault(r["technician"], r["region"] or "—")

    techs = []
    for name, p in profs.items():
        combo = combos.get(name)
        order_min = p.get("savedMinOrdering") or 0
        combo_min = combo["savedMin"] if combo else 0
        techs.append({
            "technician": name, "region": regions.get(name, "—"),
            "lostHours": round((order_min + combo_min) / 60.0, 1),
            "avoidableKm": round((p.get("excessKm") or 0) + (combo["savedKm"] if combo else 0), 1),
            "travelHours": p.get("travelHoursActual") or 0,
            "onPosHours": p.get("onPosHoursActual") or 0,
            "travelShare": p.get("travelShare"),
        })
    techs.sort(key=lambda t: -t["lostHours"])

    from collections import defaultdict
    reg = defaultdict(lambda: {"lostHours": 0.0, "avoidableKm": 0.0, "travelHours": 0.0,
                               "onPosHours": 0.0, "techs": 0, "shares": []})
    for t in techs:
        r = reg[t["region"]]
        r["lostHours"] += t["lostHours"]; r["avoidableKm"] += t["avoidableKm"]
        r["travelHours"] += t["travelHours"]; r["onPosHours"] += t["onPosHours"]
        r["techs"] += 1
        if t["travelShare"] is not None:
            r["shares"].append(t["travelShare"])
    region_rows = []
    for rname, r in reg.items():
        active = r["travelHours"] + r["onPosHours"]
        region_rows.append({
            "region": rname, "technicians": r["techs"],
            "lostHours": round(r["lostHours"], 1),
            "lostPerTech": round(r["lostHours"] / r["techs"], 1) if r["techs"] else 0,
            "avoidableKm": round(r["avoidableKm"], 1),
            "travelSharePct": round(sum(r["shares"]) / len(r["shares"]), 1) if r["shares"] else None,
            "efficiencyPct": round(100 * r["onPosHours"] / active, 1) if active else None,
        })
    # biggest reserves first (most lost hours per technician)
    region_rows.sort(key=lambda x: -x["lostPerTech"])

    total_lost = round(sum(t["lostHours"] for t in techs), 1)
    total_travel = round(sum(t["travelHours"] for t in techs), 1)
    total_km = round(sum(t["avoidableKm"] for t in techs), 1)
    return {
        "daysBack": days_back, "technicianCount": len(techs),
        "totalLostHours": total_lost, "totalAvoidableKm": total_km,
        "totalTravelHours": total_travel,
        "lostSharePct": round(100 * total_lost / total_travel, 1) if total_travel else None,
        "regions": region_rows,
        "bestRegion": min(region_rows, key=lambda x: x["lostPerTech"]) if region_rows else None,
        "worstRegion": region_rows[0] if region_rows else None,
        "topTechnicians": techs[:8],
    }


def invalidate_cache():
    _profiles_cache.clear()
    _combo_cache.clear()
