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
              payload: dict | None = None, conn=None) -> None:
    """Append one event. `kind`: import | publish | planner_run | config_change
    | recompute | override ... Safe to call from anywhere; never raises on the
    caller's behalf beyond a normal DB error."""
    sql = ("INSERT INTO events (kind, entity_type, entity_id, payload) VALUES (?,?,?,?)")
    args = (kind, entity_type, entity_id, json.dumps(payload, ensure_ascii=False) if payload else None)
    if conn is not None:
        conn.execute(sql, args)
    else:
        db.run(sql, args)


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
                  entity_id: str | None = None, year: int | None = None,
                  week: int | None = None, period: str | None = None,
                  value_text: str | None = None) -> None:
    conn.execute(
        "INSERT INTO metrics (entity_type, entity_id, metric_key, year, week, period, value_num, value_text) "
        "VALUES (?,?,?,?,?,?,?,?)",
        (entity_type, entity_id, metric_key, year, week, period, value_num, value_text))


def metric_series(entity_type: str, metric_key: str, entity_id: str | None = None,
                  limit: int = 400) -> list[dict]:
    q = ("SELECT year, week, period, value_num, value_text, computed_at FROM metrics "
         "WHERE entity_type=? AND metric_key=?")
    args: list = [entity_type, metric_key]
    if entity_id is not None:
        q += " AND entity_id=?"
        args.append(entity_id)
    q += " ORDER BY year, week, computed_at LIMIT ?"
    args.append(limit)
    return [dict(r) for r in db.get(q, tuple(args))]


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
    if conn is not None:
        cur = conn.execute(sql, args)
    else:
        import db as _db
        c = _db.connect()
        try:
            cur = c.execute(sql, args)
            c.commit()
            rid = cur.lastrowid
        finally:
            c.close()
        log_event("planner_run", "planner", str(rid),
                  {"kind": kind, "mode": mode, "startWeek": start_week,
                   "length": length, "configFingerprint": fp,
                   "planned": (result or {}).get("planned")})
        return rid
    log_event("planner_run", "planner", None,
              {"kind": kind, "mode": mode, "startWeek": start_week,
               "length": length, "configFingerprint": fp}, conn=conn)
    return cur.lastrowid


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
