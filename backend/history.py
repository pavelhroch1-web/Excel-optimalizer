"""Historical memory for the manager operating system.

The schema already ships the right substrate (pos_master_history, metrics,
events) - it was just never written to. This module populates it, so the app
gains a memory instead of only ever showing the current state:

  * pos_master_history - every field-level change to a POS (esp. PPT and
    active/closed), so PPT development and network churn are queryable over time.
  * events             - a unified activity/audit timeline (import, publish,
    planner run, config change), so "what changed and when" is answerable.
  * metrics            - point-in-time KPI snapshots (network + per technician)
    for week/month/quarter/year trends.

Nothing here changes planning; it only records what happens. "Planner rozhoduje,
historie vysvětluje."
"""
from __future__ import annotations

import json

import db

# POS columns whose changes are worth remembering (business-relevant history).
POS_TRACKED = ("ppt", "active", "technician", "terminal_type",
               "classification", "market", "category", "name")


# ---- events: unified activity / audit timeline -----------------------------

def log_event(kind: str, entity_type: str | None = None, entity_id: str | None = None,
              payload: dict | None = None, conn=None) -> int | None:
    """Append one event and return its id. `kind`: import | publish |
    planner_run | config_change | recompute | override ..."""
    sql = ("INSERT INTO events (kind, entity_type, entity_id, payload) VALUES (?,?,?,?)")
    args = (kind, entity_type, entity_id, json.dumps(payload, ensure_ascii=False) if payload else None)
    if conn is not None:
        return conn.execute(sql, args).lastrowid
    c = db.connect()
    try:
        rid = c.execute(sql, args).lastrowid
        c.commit()
        return rid
    finally:
        c.close()


def events(kind: str | None = None, limit: int = 200) -> list[dict]:
    if kind:
        rows = db.get("SELECT * FROM events WHERE kind=? ORDER BY id DESC LIMIT ?", (kind, limit))
    else:
        rows = db.get("SELECT * FROM events ORDER BY id DESC LIMIT ?", (limit,))
    out = []
    for r in rows:
        d = dict(r)
        if d.get("payload"):
            try:
                d["payload"] = json.loads(d["payload"])
            except (ValueError, TypeError):
                pass
        out.append(d)
    return out


# ---- POS change history ----------------------------------------------------

def record_pos_changes(conn, pos_id: str, old: dict | None, new: dict,
                       source: str = "import") -> int:
    """Diff tracked fields of one POS and append a history row per change.
    `old` is the pre-import row (or None for a brand-new POS). Returns changes."""
    n = 0
    for f in POS_TRACKED:
        if f not in new:
            continue
        ov = None if old is None else old.get(f)
        nv = new.get(f)
        if _norm(ov) == _norm(nv):
            continue
        conn.execute(
            "INSERT INTO pos_master_history (pos_id, field, old_value, new_value, source) "
            "VALUES (?,?,?,?,?)",
            (str(pos_id), f, _txt(ov), _txt(nv), source))
        n += 1
    return n


def mark_missing_inactive(conn, seen_ids: set, source: str = "import") -> int:
    """POS that exist in the DB as active but were NOT in this import are marked
    inactive (not deleted) - the manager keeps them for audit and trends. Each
    deactivation is logged to pos_master_history. Returns count."""
    rows = conn.execute("SELECT pos_id FROM pos_master WHERE active=1").fetchall()
    missing = [str(r[0]) for r in rows if str(r[0]) not in seen_ids]
    for pid in missing:
        conn.execute("UPDATE pos_master SET active=0, updated_at=datetime('now') WHERE pos_id=?", (pid,))
        conn.execute(
            "INSERT INTO pos_master_history (pos_id, field, old_value, new_value, source) "
            "VALUES (?,?,?,?,?)", (pid, "active", "1", "0", source + ":missing"))
    return len(missing)


def pos_history(pos_id: str, limit: int = 100) -> list[dict]:
    return [dict(r) for r in db.get(
        "SELECT changed_at, field, old_value, new_value, source FROM pos_master_history "
        "WHERE pos_id=? ORDER BY id DESC LIMIT ?", (str(pos_id), limit))]


# ---- metrics: KPI time-series ----------------------------------------------

def record_metric(conn, entity_type: str, metric_key: str, value_num=None,
                  entity_id: str | None = None, period_type: str | None = None,
                  period_key: str | None = None, dims: str | None = None,
                  source_kind: str | None = None, source_id: int | None = None,
                  value_text: str | None = None) -> None:
    conn.execute(
        "INSERT INTO metrics (entity_type, entity_id, metric_key, period_type, period_key, "
        "dims, source_kind, source_id, value_num, value_text) VALUES (?,?,?,?,?,?,?,?,?,?)",
        (entity_type, entity_id, metric_key, period_type, period_key, dims,
         source_kind, source_id, value_num, value_text))


def metric_series(entity_type: str, metric_key: str, entity_id: str | None = None,
                  limit: int = 400) -> list[dict]:
    q = ("SELECT period_type, period_key, dims, source_kind, source_id, "
         "value_num, value_text, computed_at FROM metrics "
         "WHERE entity_type=? AND metric_key=?")
    args: list = [entity_type, metric_key]
    if entity_id is not None:
        q += " AND entity_id=?"
        args.append(entity_id)
    q += " ORDER BY period_key, computed_at LIMIT ?"
    args.append(limit)
    return [dict(r) for r in db.get(q, tuple(args))]


# Which team-overview fields become which catalog metrics (semantics as data).
_NET_METRICS = {"total_visits": "totalVisits", "total_km": "totalKm",
                "coverage_overdue": "totalOverdue", "avg_on_pos_ratio": "avgOnPosRatioPct"}
_TECH_METRICS = {"visits": "visits", "km_per_day": "kmPerDay", "avg_work_hours": "avgWorkHours",
                 "travel_min": "travelMin", "on_pos_min": "onPosMin", "on_pos_ratio": "onPosRatioPct",
                 "visits_per_work_hour": "visitsPerWorkHour", "long_transfers": "longTransfers",
                 "load_pct": "loadPct", "attention": "attention"}


def _iso_week_key(d=None) -> str:
    import datetime
    d = d or datetime.date.today()
    iso = d.isocalendar()
    return f"{iso[0]}-W{iso[1]:02d}"


def capture_metrics(source_kind: str, source_id: int | None = None, days_back: int = 21) -> str:
    """Snapshot the network + per-technician KPIs into the append-only metrics
    store (weekly bucket, exact as-of via computed_at, provenance to the event
    that triggered it). Reuses team_analytics - no new analytics logic. Returns
    the week key."""
    import team_analytics
    ov = team_analytics.overview(days_back=days_back)
    wk = _iso_week_key()
    conn = db.connect()
    try:
        team = ov.get("team", {})
        for mk, src in _NET_METRICS.items():
            v = team.get(src)
            if v is not None:
                record_metric(conn, "network", mk, float(v), period_type="week",
                              period_key=wk, source_kind=source_kind, source_id=source_id)
        for t in ov.get("technicians", []):
            dims = json.dumps({"region": t.get("region")}, ensure_ascii=False) if t.get("region") else None
            for mk, src in _TECH_METRICS.items():
                v = t.get(src)
                if v is not None:
                    record_metric(conn, "technician", mk, float(v), entity_id=t["technician"],
                                  period_type="week", period_key=wk, dims=dims,
                                  source_kind=source_kind, source_id=source_id)
        conn.commit()
    finally:
        conn.close()
    return wk


def run_assessment_from_candidates(cands: list, rejected: list | None) -> dict:
    """Derive a planner run's assessment (planned / unserved by reason / score
    distribution) from the SAME engine run's observability output - no re-run."""
    import statistics
    from collections import Counter
    sel = [c for c in cands if c.get("status") == "Vybráno"]
    held = [c for c in cands if c.get("status", "").startswith("Odloženo")]
    notsel = [c for c in cands if c.get("status") == "Nevybráno"]
    scores = sorted(c["score"] for c in sel if c.get("score") is not None)

    def pct(p):
        if not scores:
            return None
        return round(scores[min(len(scores) - 1, int(p * len(scores)))], 1)

    reasons = dict(Counter((r.get("rejectReason") or "")[:48] for r in (rejected or [])).most_common(8))
    ppts = [c["ppt"] for c in sel if c.get("ppt") is not None]
    return {
        "planned": len(sel), "heldBack": len(held), "notSelected": len(notsel),
        "unserved": len(held) + len(notsel) + len(rejected or []),
        "mandatory": sum(1 for c in sel if c.get("mandatoryRuleId")),
        "core": sum(1 for c in sel if c.get("core")),
        "scoreMedian": round(statistics.median(scores), 1) if scores else None,
        "scoreMin": scores[0] if scores else None, "scoreMax": scores[-1] if scores else None,
        "scoreP25": pct(0.25), "scoreP75": pct(0.75),
        "pptMedianSelected": round(statistics.median(ppts), 1) if ppts else None,
        "unservedByReason": reasons,
    }


# ---- planner-run memory (append-only) --------------------------------------

def config_fingerprint() -> tuple[str, dict]:
    """A canonical snapshot of the EFFECTIVE planning config + a stable hash of
    it, so every planner run records which configuration produced it. Lets the
    system later answer 'what changed between these two runs' and attribute a
    different outcome to a config change vs a data change."""
    import hashlib
    snap = {
        "control": {r["key"]: r["value"] for r in db.get("SELECT key, value FROM config")},
        "business_rules": [
            {"code": r["code"], "enabled": r["enabled"], "params": r["params"]}
            for r in db.get("SELECT code, enabled, params FROM business_rules ORDER BY code, scope, scope_value")],
        "cadence_overrides": [dict(r) for r in db.get(
            "SELECT rule_id, min_gap_weeks, max_interval_weeks, active, priority FROM cadence_overrides ORDER BY rule_id")],
        "model_overrides": [dict(r) for r in db.get(
            "SELECT sheet, match_key, col, value FROM model_overrides ORDER BY sheet, match_key, col")],
        "settings": [dict(r) for r in db.get(
            "SELECT namespace, key, value FROM settings WHERE namespace IN ('engine','scoring','planner','optimization') "
            "AND scope='global' ORDER BY namespace, key")],
    }
    blob = json.dumps(snap, ensure_ascii=False, sort_keys=True)
    return hashlib.sha256(blob.encode("utf-8")).hexdigest()[:16], snap


def record_planner_run(kind: str, mode: str, start_week: int, length: int,
                       visits_per_tech_week=None, tech_count=None,
                       result: dict | None = None, conn=None) -> int:
    """Append one planner-run record (never updated/deleted). Returns row id."""
    fp, snap = config_fingerprint()
    sql = ("INSERT INTO planner_runs (kind, mode, start_week, length, "
           "visits_per_tech_week, tech_count, config_fingerprint, config_snapshot, result) "
           "VALUES (?,?,?,?,?,?,?,?,?)")
    args = (kind, mode, start_week, length, visits_per_tech_week, tech_count, fp,
            json.dumps(snap, ensure_ascii=False),
            json.dumps(result, ensure_ascii=False) if result else None)
    own = conn is None
    c = db.connect() if own else conn
    try:
        rid = c.execute(sql, args).lastrowid
        # Headline decision metrics into the time-series (provenance = this run).
        for mk in ("planned", "unserved", "scoreMedian"):
            v = (result or {}).get(mk)
            if v is not None:
                record_metric(c, "planner_run",
                              "score_median" if mk == "scoreMedian" else mk,
                              float(v), entity_id=str(rid), period_type="asof",
                              period_key=_iso_week_key(), source_kind="planner_run", source_id=rid)
        log_event("planner_run", "planner", str(rid),
                  {"kind": kind, "mode": mode, "startWeek": start_week, "length": length,
                   "configFingerprint": fp, "planned": (result or {}).get("planned")}, conn=c)
        if own:
            c.commit()
        return rid
    finally:
        if own:
            c.close()


def planner_runs(limit: int = 100) -> list[dict]:
    out = []
    for r in db.get("SELECT id, ran_at, kind, mode, start_week, length, "
                    "visits_per_tech_week, tech_count, config_fingerprint, result "
                    "FROM planner_runs ORDER BY id DESC LIMIT ?", (limit,)):
        d = dict(r)
        if d.get("result"):
            try:
                d["result"] = json.loads(d["result"])
            except (ValueError, TypeError):
                pass
        out.append(d)
    return out


# ---- helpers ----------------------------------------------------------------

def _norm(v):
    if v is None:
        return None
    if isinstance(v, float) and v.is_integer():
        return str(int(v))
    return str(v).strip()


def _txt(v):
    return None if v is None else str(v)
