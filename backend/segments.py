"""Planner [S] — configurable segment model + coverage state.

A segment is ANY combination of predicates over pos_master attributes (terminal
type, partner/chain, region, category, classification, PPT, city, technician, and
any future custom flag). Definitions live in `segment_definitions` and are fully
editable from the Velín — the code is a generic engine, the business strategy is
configuration. Each segment carries its own target cadence, priority, business
weight, campaign include/exclude, and minimum required coverage.

Coverage state answers, per segment: how much is within its target cadence, how
much is overdue or trending overdue, and the resulting business risk — the base
for cadence-risk forecasting and campaign feasibility (later parts of [S]).

Deterministic; no hardcoded segments, no ML.
"""
from __future__ import annotations

import datetime
import json

import db

# Friendly field name -> pos_master column. Any of these (or future columns) can
# be used in a segment rule, so segmentation is by any attribute combination.
FIELDS = {
    "terminal_type": "terminal_type", "partner": "market", "chain": "market",
    "region": "pos_area", "area": "area", "category": "category",
    "classification": "classification", "ppt": "ppt", "city": "city",
    "technician": "technician",
}
_NUM_OPS = {"gte", "lte", "between"}


def _col(field):
    return FIELDS.get(field, field if field.isidentifier() else None)


def _match(pos, rule) -> bool:
    """True if the POS satisfies every predicate in rule['all']."""
    for cond in (rule or {}).get("all", []):
        col = _col(cond.get("field"))
        if not col or col not in pos.keys():
            return False
        val = pos[col]
        op, target = cond.get("op", "eq"), cond.get("value")
        if op in _NUM_OPS:
            try:
                v = float(val)
            except (TypeError, ValueError):
                return False
            if op == "gte" and not (v >= float(target)):
                return False
            if op == "lte" and not (v <= float(target)):
                return False
            if op == "between" and not (float(target[0]) <= v <= float(target[1])):
                return False
        else:
            s = "" if val is None else str(val)
            if op == "eq" and s != str(target):
                return False
            if op == "ne" and s == str(target):
                return False
            if op == "in" and s not in [str(x) for x in (target or [])]:
                return False
            if op == "not_in" and s in [str(x) for x in (target or [])]:
                return False
            if op == "contains" and str(target).lower() not in s.lower():
                return False
    return True


# ------------------------------------------------------------------ config CRUD
def definitions(active_only: bool = False) -> list:
    q = "SELECT * FROM segment_definitions"
    if active_only:
        q += " WHERE active=1"
    q += " ORDER BY sort_order, id"
    out = []
    for r in db.get(q):
        d = dict(r)
        d["rule"] = json.loads(d["rule"]) if d.get("rule") else {"all": []}
        out.append(d)
    return out


def upsert(seg: dict) -> dict:
    rule = json.dumps(seg.get("rule") or {"all": []})
    fields = (seg.get("name"), rule, seg.get("target_cadence_weeks"), seg.get("priority", 3),
              seg.get("business_weight", 1.0), 1 if seg.get("include_in_campaign", True) else 0,
              seg.get("min_coverage_pct", 80.0), 1 if seg.get("active", True) else 0,
              seg.get("sort_order", 100))
    if seg.get("id"):
        db.run("UPDATE segment_definitions SET name=?, rule=?, target_cadence_weeks=?, priority=?, "
               "business_weight=?, include_in_campaign=?, min_coverage_pct=?, active=?, sort_order=?, "
               "updated_at=datetime('now') WHERE id=?", fields + (seg["id"],))
        return {"id": seg["id"], "updated": True}
    db.run("INSERT INTO segment_definitions(name, rule, target_cadence_weeks, priority, business_weight, "
           "include_in_campaign, min_coverage_pct, active, sort_order) VALUES(?,?,?,?,?,?,?,?,?)", fields)
    return {"id": db.get("SELECT last_insert_rowid() id")[0]["id"], "created": True}


def delete(seg_id: int) -> dict:
    db.run("DELETE FROM segment_definitions WHERE id=?", (seg_id,))
    return {"deleted": seg_id}


def fields_meta() -> dict:
    """Available fields + operators + distinct values — for the Velín rule editor."""
    out = {}
    for friendly, col in FIELDS.items():
        if friendly in ("chain", "area"):  # aliases / skip duplicate
            continue
        vals = [r[0] for r in db.get(
            f"SELECT DISTINCT {col} v FROM pos_master WHERE {col} IS NOT NULL AND {col}<>'' "
            f"ORDER BY {col} LIMIT 40")]
        out[friendly] = {"column": col, "values": vals if len(vals) <= 40 else []}
    return {"fields": out, "operators": ["eq", "ne", "in", "not_in", "gte", "lte", "between", "contains"]}


# ------------------------------------------------------------------ defaults
def seed_defaults() -> dict:
    """If no segments exist, create sensible data-derived ones. Starting points
    only — everything is editable in the Velín."""
    if db.get("SELECT id FROM segment_definitions LIMIT 1"):
        return {"seeded": False, "reason": "already configured"}
    defaults = [
        ("Velké terminály", {"all": [{"field": "terminal_type", "op": "eq", "value": "VELKY TERMINAL"}]}, 3, 1, 1.3, 85),
        ("Malé terminály", {"all": [{"field": "terminal_type", "op": "eq", "value": "SMALL TERMINAL"}]}, 6, 3, 1.0, 70),
        ("LI terminály", {"all": [{"field": "terminal_type", "op": "eq", "value": "LI"}]}, 4, 2, 1.1, 75),
        ("B terminály", {"all": [{"field": "classification", "op": "eq", "value": "B"}]}, 4, 2, 1.0, 80),
    ]
    # one segment per major partner, from the data
    for m in db.get("SELECT market, COUNT(*) c FROM pos_master WHERE market IS NOT NULL AND market<>'' "
                    "GROUP BY market HAVING c>=50 ORDER BY c DESC"):
        defaults.append((f"Partner: {m['market']}",
                         {"all": [{"field": "partner", "op": "eq", "value": m["market"]}]}, 4, 3, 1.0, 75))
    for i, (name, rule, cad, prio, w, mincov) in enumerate(defaults):
        upsert({"name": name, "rule": rule, "target_cadence_weeks": cad, "priority": prio,
                "business_weight": w, "min_coverage_pct": mincov, "sort_order": (i + 1) * 10})
    return {"seeded": True, "count": len(defaults)}


# ------------------------------------------------------------------ coverage
def _pos_with_last_visit():
    return db.get(
        "SELECT p.pos_id, p.name, p.city, p.terminal_type, p.market, p.pos_area, p.area, "
        "p.category, p.classification, p.ppt, p.technician, p.gps_x, p.gps_y, "
        "p.first_seen first_seen, lv.last last_visit "
        "FROM pos_master p LEFT JOIN (SELECT pos_id, MAX(visit_date) last FROM salesapp_visits "
        "GROUP BY pos_id) lv ON lv.pos_id=p.pos_id WHERE p.active=1")


def _weeks_since(last, today):
    if not last:
        return None
    try:
        d = datetime.date.fromisoformat(str(last)[:10])
        return round((today - d).days / 7.0, 1)
    except (ValueError, TypeError):
        return None


def coverage() -> dict:
    """Per-segment coverage state + business risk (as of today)."""
    if not db.get("SELECT id FROM segment_definitions LIMIT 1"):
        seed_defaults()
    segs = definitions(active_only=True)
    pos = _pos_with_last_visit()
    today = datetime.date.today()
    out = []
    for s in segs:
        target = s.get("target_cadence_weeks") or None
        members = [p for p in pos if _match(p, s["rule"])]
        n = len(members)
        if n == 0:
            continue
        within = overdue = approaching = never = 0
        weeks = []
        overdue_list = []
        for p in members:
            visited = p["last_visit"] is not None
            # POS lifecycle: coverage counts from first_seen until the first real
            # visit, so a freshly-imported POS isn't instantly "overdue".
            ref = p["last_visit"] if visited else p["first_seen"]
            w = _weeks_since(ref, today)
            if not visited:
                never += 1
            if visited and w is not None:
                weeks.append(w)                 # real cadence uses visited only
            if w is None:
                overdue += 1; overdue_list.append((None, p, visited))
            elif target:
                if w > target:
                    overdue += 1; overdue_list.append((w, p, visited))
                elif w > 0.8 * target:
                    approaching += 1
                else:
                    within += 1
            else:
                within += 1
        cov_pct = round(100 * within / n, 1)
        overdue_pct = round(100 * overdue / n, 1)
        min_cov = s.get("min_coverage_pct") or 0
        # business risk: below the floor or lots overdue = high; approaching = medium
        if cov_pct < min_cov * 0.9 or overdue_pct >= 25:
            risk = "high"
        elif cov_pct < min_cov or approaching >= 0.2 * n:
            risk = "medium"
        else:
            risk = "low"
        overdue_list.sort(key=lambda x: (x[0] is not None, -(x[0] or 1e9)))
        examples = [{"pos": str(p["pos_id"]), "name": p["name"], "city": p["city"],
                     "weeksSince": w, "visited": vis, "lat": p["gps_x"], "lon": p["gps_y"]}
                    for w, p, vis in overdue_list[:8]]
        out.append({
            "id": s["id"], "name": s["name"], "priority": s["priority"],
            "businessWeight": s["business_weight"], "targetCadenceWeeks": target,
            "minCoveragePct": min_cov, "includeInCampaign": bool(s["include_in_campaign"]),
            "posCount": n, "coveragePct": cov_pct, "overduePct": overdue_pct,
            "withinCadence": within, "approaching": approaching, "overdue": overdue, "never": never,
            "avgWeeksSince": round(sum(weeks) / len(weeks), 1) if weeks else None,
            "maxWeeksSince": round(max(weeks), 1) if weeks else None,
            "risk": risk, "examples": examples,
        })
    order = {"high": 0, "medium": 1, "low": 2}
    out.sort(key=lambda x: (order[x["risk"]], x["priority"], -x["overduePct"]))
    return {"asOf": today.isoformat(), "segments": out,
            "counts": {"high": sum(1 for s in out if s["risk"] == "high"),
                       "medium": sum(1 for s in out if s["risk"] == "medium"),
                       "low": sum(1 for s in out if s["risk"] == "low")}}
