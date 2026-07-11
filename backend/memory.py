"""The read side of the operational memory — the system's stable query contract.

The closed loop is: import -> plan -> reality -> measure deviations -> remember
(history.py writes) -> use next time (this module reads). Everything the cockpit
and every FUTURE layer (AI recommendations, alerts, technician benchmarking,
capacity / PPT / visit prediction, anomaly detection, campaign simulation) needs
from the memory goes through the functions here.

Why this boundary matters: future layers attach to memory.query_* — a small,
stable API — NOT to raw tables. So the storage can evolve and new layers can be
added without any of them rewriting SQL or the schema. This module never makes a
decision; it reads, aggregates and explains what already happened.
"""
from __future__ import annotations

import json

import db
import history


# ---- catalog: what every metric MEANS (semantics as data) ------------------

def catalog() -> list[dict]:
    return [dict(r) for r in db.get(
        "SELECT metric_key, label, description, unit, entity_types, direction, category "
        "FROM metric_definitions WHERE active=1 ORDER BY category, metric_key")]


def _meta(metric_key: str) -> dict:
    r = db.get("SELECT label, unit, direction FROM metric_definitions WHERE metric_key=?", (metric_key,))
    return dict(r[0]) if r else {"label": metric_key, "unit": None, "direction": "neutral"}


# ---- trends: a metric's development over time (week/month/quarter/year) -----

_PERIOD_LEN = {"month": 7, "quarter": None, "year": 4}  # slicing of 'YYYY-Www' / date keys


def trend(entity_type: str, metric_key: str, entity_id: str | None = None,
          grain: str = "week") -> dict:
    """A metric's time-series for one entity, optionally rolled up to
    month/quarter/year (aggregated from the stored weekly points - months etc.
    are derived, never stored twice)."""
    series = history.metric_series(entity_type, metric_key, entity_id)
    points = [{"period": s["period_key"], "value": s["value_num"],
               "source": s["source_kind"], "at": s["computed_at"]}
              for s in series if s["value_num"] is not None]
    if grain != "week":
        points = _rollup(points, grain)
    first = points[0]["value"] if points else None
    last = points[-1]["value"] if points else None
    return {
        "entityType": entity_type, "entityId": entity_id, "metric": metric_key,
        "meta": _meta(metric_key), "grain": grain, "points": points,
        "latest": last, "first": first,
        "changePct": (round(100 * (last - first) / first, 1) if first not in (None, 0) and last is not None else None),
    }


def _period_bucket(period_key: str, grain: str) -> str:
    """'2026-W30' / '2026-07-08' -> a month/quarter/year bucket label."""
    if not period_key:
        return period_key
    year = period_key[:4]
    if grain == "year":
        return year
    # derive month from an ISO-week key or a date key
    import datetime
    try:
        if "-W" in period_key:
            wk = int(period_key.split("-W")[1])
            d = datetime.date.fromisocalendar(int(year), wk, 1)
        else:
            d = datetime.date.fromisoformat(period_key[:10])
    except (ValueError, TypeError):
        return period_key
    if grain == "quarter":
        return f"{year}-Q{(d.month - 1) // 3 + 1}"
    return f"{year}-{d.month:02d}"  # month


def _rollup(points: list[dict], grain: str) -> list[dict]:
    from statistics import mean
    buckets: dict[str, list[float]] = {}
    order: list[str] = []
    for p in points:
        b = _period_bucket(p["period"], grain)
        if b not in buckets:
            buckets[b] = []
            order.append(b)
        buckets[b].append(p["value"])
    return [{"period": b, "value": round(mean(buckets[b]), 2), "source": "rollup",
             "at": None} for b in order]


# ---- POS evolution: PPT + attribute history over years ---------------------

def pos_evolution(pos_id: str) -> dict:
    """How a POS developed: its field-level history (esp. PPT) with change
    timestamps - the raw material for 'how did this POS evolve over 3 years'."""
    hist = history.pos_history(pos_id, limit=500)
    ppt = [{"at": h["changed_at"], "from": h["old_value"], "to": h["new_value"]}
           for h in hist if h["field"] == "ppt"]
    return {"pos": pos_id, "pptChanges": ppt, "allChanges": hist}


# ---- planner decision replay: why the planner decided, on what basis --------

def planner_run_explain(run_id: int) -> dict | None:
    """Reopen any past planner run: its inputs, the exact config that produced
    it, and its assessment (planned / unserved by reason / score distribution).
    'Nejen co naplánoval, ale proč a na základě čeho.'"""
    r = db.get("SELECT * FROM planner_runs WHERE id=?", (run_id,))
    if not r:
        return None
    d = dict(r[0])
    for f in ("config_snapshot", "result"):
        if d.get(f):
            try:
                d[f] = json.loads(d[f])
            except (ValueError, TypeError):
                pass
    # the metric snapshot captured under the same run (provenance link)
    d["metrics"] = [dict(m) for m in db.get(
        "SELECT entity_type, entity_id, metric_key, value_num FROM metrics "
        "WHERE source_kind='planner_run' AND source_id=?", (run_id,))]
    return d


def config_diff(run_id_a: int, run_id_b: int) -> dict:
    """What changed in the planning config between two runs - so a different
    outcome can be attributed to a config change vs a data change."""
    def snap(rid):
        r = db.get("SELECT config_snapshot, config_fingerprint FROM planner_runs WHERE id=?", (rid,))
        if not r:
            return None, None
        s = r[0]["config_snapshot"]
        try:
            return (json.loads(s) if s else {}), r[0]["config_fingerprint"]
        except (ValueError, TypeError):
            return {}, r[0]["config_fingerprint"]
    a, fpa = snap(run_id_a)
    b, fpb = snap(run_id_b)
    if a is None or b is None:
        return {"error": "run not found"}
    return {"runA": run_id_a, "runB": run_id_b, "fingerprintA": fpa, "fingerprintB": fpb,
            "identical": fpa == fpb, "diff": _dict_diff(a, b)}


def _dict_diff(a, b, path="") -> list[dict]:
    out = []
    sa = json.dumps(a, sort_keys=True, ensure_ascii=False)
    sb = json.dumps(b, sort_keys=True, ensure_ascii=False)
    if sa != sb:
        out.append({"path": path or "(root)", "a": a, "b": b})
    return out
