"""Per-technician SLA + a composite Technician Score.

Both are built ONLY from metrics the engine / analytics already compute — no
new business logic, no fresh engine run:

  * Plan SLA        -> plan_reality.fulfillment (published plan vs reality)
  * past-due breach -> team_analytics.overview `overdue` (planned POS in a past
                       week, never visited)
  * productivity    -> team_analytics.overview `visitsPerWorkHour`
  * time efficiency -> team_analytics.overview `onPosRatioPct`
  * technician-scoped alerts -> alerts.list_alerts filtered to this person

The Technician Score is deliberately ORTHOGONAL to the existing Health Score
(diagnostics = routing efficiency): it measures plan DELIVERY —
  40 % plan fulfilment (incl. shifted), 30 % productivity percentile,
  30 % on-POS time ratio — reweighted to productivity 55 % / on-POS 45 %
when there is no published plan to measure against. All objective, all from
existing aggregates; the weights are the only choice here and live in one
place (WEIGHTS) so they are easy to tune.
"""
from __future__ import annotations

import db

# Score component weights. Two profiles: with a published plan to measure
# against, and without (fall back to the field-work signals only).
WEIGHTS = {
    "withPlan": {"fulfilment": 0.40, "productivity": 0.30, "onPos": 0.30},
    "noPlan": {"productivity": 0.55, "onPos": 0.45},
}


def _published_week_range():
    r = db.get("SELECT MIN(week) a, MAX(week) b FROM published_plans")
    return (r[0]["a"], r[0]["b"]) if r and r[0]["a"] is not None else (None, None)


def _percentile(values: list[float], v: float) -> float:
    """% of peers at or below v (0..100). Empty / singleton -> 50 (neutral)."""
    xs = [x for x in values if x is not None]
    if len(xs) < 2 or v is None:
        return 50.0
    at_or_below = sum(1 for x in xs if x <= v)
    return round(100.0 * at_or_below / len(xs), 1)


def compute(name: str, days_back: int = 120, team=None, fulfil_all=None) -> dict:
    """SLA + Technician Score for one person. `team` / `fulfil_all` may be
    passed in when a caller already has them (the whole-team score sweep), to
    avoid recomputing the heavy aggregates per technician."""
    import team_analytics

    ov = team if team is not None else team_analytics.overview(days_back=days_back)
    techs = ov.get("technicians", [])
    me = next((t for t in techs if t["technician"] == name), None)

    # --- productivity + time-efficiency percentiles among ACTIVE technicians
    active = [t for t in techs if t.get("visits")]
    prod_vals = [t.get("visitsPerWorkHour") for t in active]
    onpos_vals = [t.get("onPosRatioPct") for t in active]
    prod_pct = _percentile(prod_vals, (me or {}).get("visitsPerWorkHour")) if me else None
    onpos = (me or {}).get("onPosRatioPct")
    onpos_pct = _percentile(onpos_vals, onpos) if me else None

    # --- Plan SLA from the published plan (if any)
    wa, wb = _published_week_range()
    plan_sla = None
    if wa is not None:
        if fulfil_all is None:
            import plan_reality
            fulfil_all = plan_reality.fulfillment(int(wa), int(wb))
        f = next((t for t in fulfil_all.get("perTechnician", []) if t["technician"] == name), None)
        if f:
            planned = f.get("planned") or 0
            met = f.get("done", 0) + f.get("doneShifted", 0)
            plan_sla = {
                "planned": planned,
                "onTime": f.get("done", 0),
                "shifted": f.get("doneShifted", 0),
                "missed": f.get("missed", 0),
                "wrongTech": f.get("wrongTech", 0),
                "extra": f.get("extra", 0),
                "onTimePct": round(100 * f.get("done", 0) / planned, 1) if planned else None,
                "metPct": round(100 * met / planned, 1) if planned else None,
                "pastDue": (me or {}).get("overdue", 0),
            }

    # --- Technician Score: objective weighted blend
    if plan_sla and plan_sla["planned"]:
        w = WEIGHTS["withPlan"]
        parts = {
            "fulfilment": (plan_sla["metPct"] or 0) * w["fulfilment"],
            "productivity": (prod_pct or 0) * w["productivity"],
            "onPos": (onpos or 0) * w["onPos"],
        }
        basis = "withPlan"
    else:
        w = WEIGHTS["noPlan"]
        parts = {
            "productivity": (prod_pct or 0) * w["productivity"],
            "onPos": (onpos or 0) * w["onPos"],
        }
        basis = "noPlan"
    score = round(sum(parts.values())) if me else None

    return {
        "technician": name,
        "technicianScore": {
            "score": score, "basis": basis, "weights": w,
            "components": {
                "fulfilmentPct": plan_sla["metPct"] if plan_sla else None,
                "productivityPercentile": prod_pct,
                "onPosRatioPct": onpos,
            },
        },
        "planSla": plan_sla,
        "productivity": {
            "visitsPerWorkHour": (me or {}).get("visitsPerWorkHour"),
            "percentile": prod_pct,
            "onPosRatioPct": onpos,
            "onPosPercentile": onpos_pct,
            "visits": (me or {}).get("visits"),
            "daysWorked": (me or {}).get("daysWorked"),
        },
        "alerts": technician_alerts(name),
    }


def technician_alerts(name: str, limit: int = 40) -> list[dict]:
    """Alerts the engine already raised for THIS technician (entity-scoped),
    newest first — the 'proof' behind the warnings shown on the detail."""
    import alerts
    out = []
    for a in alerts.list_alerts(limit=200):
        if a.get("entity_type") == "technician" and a.get("entity_id") == name:
            p = a.get("payload") or {}
            out.append({
                "ts": a.get("ts"),
                "severity": p.get("severity") or a.get("severity"),
                "message": p.get("message") or p.get("msg") or "",
                "kind": p.get("kind"),
                "metric": p.get("metric"), "value": p.get("value"),
            })
            if len(out) >= limit:
                break
    return out
