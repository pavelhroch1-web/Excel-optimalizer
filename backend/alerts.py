"""Automatic anomaly alerts - the system surfaces what deserves attention.

recompute() scans reality (and the POS network) after each sync and writes
alerts into the generic `events` table; list_alerts() returns them. The manager
doesn't hunt - the platform flags: unusually long/short on-POS time, low
activity, chronically unserved POS, etc. Reliable signals only (km/route-based
alerts come once the route layer exists). Read-mostly; no planning logic.
"""
from __future__ import annotations

import json
import statistics

import db
import plan_reality


def _median(xs):
    xs = [x for x in xs if x is not None]
    return statistics.median(xs) if xs else None


def _add(conn, severity, kind_slug, entity_type, entity_id, message, metric=None, value=None):
    conn.execute(
        "INSERT INTO events (kind, entity_type, entity_id, payload) VALUES ('alert', ?, ?, ?)",
        (entity_type, str(entity_id) if entity_id is not None else None,
         json.dumps({"severity": severity, "type": kind_slug, "message": message,
                     "metric": metric, "value": value}, ensure_ascii=False)))


def recompute() -> int:
    """Rebuild the alert set from current data. Returns the number of alerts."""
    r = plan_reality.reality()
    techs = r["technicians"]
    conn = db.connect()
    n = 0
    try:
        conn.execute("DELETE FROM events WHERE kind='alert'")

        onpos = [t["avgOnPosMinutes"] for t in techs if t["avgOnPosMinutes"] is not None]
        med_onpos = _median(onpos)
        visits = [t["visits"] for t in techs]
        med_visits = _median(visits)

        for t in techs:
            m = t["avgOnPosMinutes"]
            if med_onpos and m is not None:
                if m > med_onpos * 1.6:
                    _add(conn, "warn", "onpos_long", "technician", t["technician"],
                         f"{t['technician']}: neobvykle dlouho na POS (~{m} min, medián {med_onpos}).",
                         "avgOnPosMinutes", m); n += 1
                elif m < med_onpos * 0.45:
                    _add(conn, "warn", "onpos_short", "technician", t["technician"],
                         f"{t['technician']}: velmi krátce na POS (~{m} min, medián {med_onpos}) – zkontrolovat.",
                         "avgOnPosMinutes", m); n += 1
            if med_visits and 10 <= t["visits"] < med_visits * 0.4:
                _add(conn, "info", "low_activity", "technician", t["technician"],
                     f"{t['technician']}: výrazně méně návštěv ({t['visits']}, medián {med_visits}).",
                     "visits", t["visits"]); n += 1

        # chronically unserved POS (from POS_MASTER neglect)
        thr = 40
        row = db.get("SELECT COUNT(*) AS c FROM pos_master WHERE active=1 AND "
                     "CAST(weeksSinceLastVisit AS INTEGER) > ?", (thr,)) if _has_col("pos_master", "weeksSinceLastVisit") else [{"c": 0}]
        long_unserved = row[0]["c"]
        if long_unserved:
            _add(conn, "warn", "pos_unserved", "network", None,
                 f"{long_unserved} aktivních POS nenavštíveno déle než {thr} týdnů.",
                 "count", long_unserved); n += 1

        conn.commit()
    finally:
        conn.close()
    return n


def _has_col(table, col) -> bool:
    return any(r["name"] == col for r in db.get(f"PRAGMA table_info({table})"))


def list_alerts(limit: int = 100) -> list[dict]:
    rows = db.get("SELECT id, ts, entity_type, entity_id, payload FROM events "
                  "WHERE kind='alert' ORDER BY id DESC LIMIT ?", (limit,))
    out = []
    for r in rows:
        d = dict(r)
        try:
            d["payload"] = json.loads(d["payload"]) if d["payload"] else {}
        except (ValueError, TypeError):
            d["payload"] = {}
        out.append(d)
    return out
