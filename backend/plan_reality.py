"""Plan vs. reality - compare the published TourPlan with actual SalesApp
visits, and surface real field activity. Read-only over SQLite.

Two lenses:
  * fulfillment(): published_plans (plan) vs salesapp_visits (reality) by POS
    and week -> done / missed / extra (mimořádné) / wrong-technician, per
    technician. Production-ready; lights up once SalesApp for the published
    weeks arrives (the published plan is normally in the future).
  * reality(): what technicians actually did (rich now - 38k visits): visits,
    unique POS, days worked, avg on-POS time, from SalesApp.

OZ stay informational (a POS visited by an OZ still counts as "covered", but
the planned work is the technician's). No planning logic here.
"""
from __future__ import annotations

import datetime

import db


def _iso_week(date_str) -> int | None:
    if not date_str:
        return None
    try:
        d = str(date_str)[:10]
        y, m, dd = (int(x) for x in d.split("-"))
        return datetime.date(y, m, dd).isocalendar()[1]
    except (ValueError, TypeError):
        return None


def _actual_by_pos_week(week_from, week_to):
    """{(pos_id, week): [technicians]} from real visits (any role)."""
    out: dict = {}
    for r in db.get("SELECT pos_id, technician, visitor_role, visit_date FROM salesapp_visits "
                    "WHERE pos_id IS NOT NULL AND visit_date IS NOT NULL"):
        wk = _iso_week(r["visit_date"])
        if wk is None or (week_from and wk < week_from) or (week_to and wk > week_to):
            continue
        out.setdefault((str(r["pos_id"]), wk), []).append(
            {"tech": r["technician"], "role": r["visitor_role"]})
    return out


def fulfillment(week_from: int, week_to: int, tolerance: int = 1) -> dict:
    """Compare the published plan with reality in [week_from, week_to]."""
    planned = db.get("SELECT pos_id, week, technician FROM published_plans "
                     "WHERE week BETWEEN ? AND ?", (week_from, week_to))
    actual = _actual_by_pos_week(week_from - tolerance, week_to + tolerance)

    per_tech: dict = {}

    def T(name):
        return per_tech.setdefault(name or "?", {"technician": name or "?", "planned": 0,
                                                 "done": 0, "doneShifted": 0, "missed": 0,
                                                 "wrongTech": 0})

    done = missed = shifted = wrong = 0
    planned_keys = set()
    for p in planned:
        pos, wk, tech = str(p["pos_id"]), p["week"], p["technician"]
        planned_keys.add((pos, wk))
        t = T(tech); t["planned"] += 1
        hit_here = actual.get((pos, wk))
        hit_near = hit_here or next((actual.get((pos, wk + d))
                                     for d in range(-tolerance, tolerance + 1)
                                     if actual.get((pos, wk + d))), None)
        if hit_here:
            done += 1; t["done"] += 1
            if not any(h["tech"] == tech for h in hit_here):
                wrong += 1; t["wrongTech"] += 1
        elif hit_near:
            shifted += 1; t["doneShifted"] += 1
        else:
            missed += 1; t["missed"] += 1

    # extra (mimořádné): actual visits to a POS in a week that was not planned
    extra = 0
    for (pos, wk), visits in actual.items():
        if week_from <= wk <= week_to and (pos, wk) not in planned_keys:
            extra += len(visits)

    total = len(planned)
    for t in per_tech.values():
        base = t["planned"] or 1
        t["fulfilmentPct"] = round(100 * (t["done"] + t["doneShifted"]) / base, 1)

    return {
        "weekFrom": week_from, "weekTo": week_to,
        "planned": total, "done": done, "doneShifted": shifted, "missed": missed,
        "wrongTechnician": wrong, "extraVisits": extra,
        "fulfilmentPct": round(100 * (done + shifted) / total, 1) if total else None,
        "perTechnician": sorted(per_tech.values(), key=lambda x: -x["planned"]),
        "note": ("Publikovaný plán a data ze SalesApp se v tomto rozsahu zatím "
                 "nepřekrývají (plán je typicky budoucnost). Naplní se, až dorazí "
                 "SalesApp za publikované týdny.") if total and (done + shifted) == 0 else None,
    }


def reality(week_from: int | None = None, week_to: int | None = None) -> dict:
    """What technicians actually did in the window (rich real data)."""
    rows = db.get("SELECT technician, pos_id, visitor_role, visit_date, real_duration "
                  "FROM salesapp_visits WHERE technician IS NOT NULL AND visit_date IS NOT NULL")
    per: dict = {}
    for r in rows:
        wk = _iso_week(r["visit_date"])
        if wk is None or (week_from and wk < week_from) or (week_to and wk > week_to):
            continue
        if r["visitor_role"] != "TECHNIK":
            continue
        d = per.setdefault(r["technician"], {"technician": r["technician"], "visits": 0,
                                             "posSet": set(), "days": set(), "durSum": 0.0, "durN": 0})
        d["visits"] += 1
        if r["pos_id"]:
            d["posSet"].add(str(r["pos_id"]))
        d["days"].add(str(r["visit_date"])[:10])
        if r["real_duration"] not in (None, ""):
            try:
                d["durSum"] += float(r["real_duration"]); d["durN"] += 1
            except (ValueError, TypeError):
                pass
    out = []
    for d in per.values():
        out.append({
            "technician": d["technician"], "visits": d["visits"],
            "uniquePos": len(d["posSet"]), "daysWorked": len(d["days"]),
            "avgVisitsPerDay": round(d["visits"] / max(len(d["days"]), 1), 1),
            "avgOnPosMinutes": round(60 * d["durSum"] / d["durN"], 1) if d["durN"] else None,
        })
    out.sort(key=lambda x: -x["visits"])
    return {"weekFrom": week_from, "weekTo": week_to,
            "technicians": out, "technicianCount": len(out)}
