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


def _dt(s):
    if not s:
        return None
    try:
        return datetime.datetime.fromisoformat(str(s))
    except ValueError:
        for fmt in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%d %H:%M"):
            try:
                return datetime.datetime.strptime(str(s), fmt)
            except ValueError:
                continue
    return None


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
    """Compare the published plan with reality in [week_from, week_to].

    published_plans keeps one row-set per publish, so a week can appear under
    several snapshots; join plan_lifecycle to take only the snapshot currently
    locked for each week (the same source of truth board() uses), else planned
    counts double."""
    planned = db.get(
        "SELECT pp.pos_id, pp.week, pp.technician FROM published_plans pp "
        "JOIN plan_lifecycle pl ON pl.week=pp.week AND pl.snapshot_id=pp.snapshot_id "
        "AND pl.status='Published' WHERE pp.week BETWEEN ? AND ?", (week_from, week_to))
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
    """What ACTIVE technicians actually did in the window (rich real data).
    Only role=TECHNIK & active=1 count - OZ/Admin/Manager are excluded per the
    technician configuration."""
    tech_ok = {r["name"] for r in db.get(
        "SELECT name FROM technicians WHERE role='TECHNIK' AND active=1 AND excluded=0")}
    rows = db.get("SELECT technician, pos_id, visitor_role, visit_date, real_duration, "
                  "started_at, finished_at FROM salesapp_visits "
                  "WHERE technician IS NOT NULL AND visit_date IS NOT NULL")
    per: dict = {}
    for r in rows:
        wk = _iso_week(r["visit_date"])
        if wk is None or (week_from and wk < week_from) or (week_to and wk > week_to):
            continue
        if r["technician"] not in tech_ok:
            continue
        d = per.setdefault(r["technician"], {"technician": r["technician"], "visits": 0,
                                             "posSet": set(), "days": {}, "durSum": 0.0, "durN": 0})
        d["visits"] += 1
        if r["pos_id"]:
            d["posSet"].add(str(r["pos_id"]))
        day = str(r["visit_date"])[:10]
        span = d["days"].setdefault(day, [None, None])  # [min_start, max_finish]
        st, fin = _dt(r["started_at"]), _dt(r["finished_at"])
        if st and (span[0] is None or st < span[0]):
            span[0] = st
        if fin and (span[1] is None or fin > span[1]):
            span[1] = fin
        if r["real_duration"] not in (None, ""):
            try:
                d["durSum"] += float(r["real_duration"]); d["durN"] += 1
            except (ValueError, TypeError):
                pass
    out = []
    for d in per.values():
        day_hours = [(s[1] - s[0]).total_seconds() / 3600.0
                     for s in d["days"].values() if s[0] and s[1]]
        out.append({
            "technician": d["technician"], "visits": d["visits"],
            "uniquePos": len(d["posSet"]), "daysWorked": len(d["days"]),
            "avgVisitsPerDay": round(d["visits"] / max(len(d["days"]), 1), 1),
            "avgOnPosMinutes": round(60 * d["durSum"] / d["durN"], 1) if d["durN"] else None,
            "avgHoursPerDay": round(sum(day_hours) / len(day_hours), 1) if day_hours else None,
        })
    out.sort(key=lambda x: -x["visits"])
    return {"weekFrom": week_from, "weekTo": week_to,
            "technicians": out, "technicianCount": len(out)}
