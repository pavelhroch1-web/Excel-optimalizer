"""Read-only POS insight queries over SQLite - the informational layer.

Serves POS Explorer and the technician planner. OZ are NOT planned; they are
purely informational here, so the planner can see that an OZ already covered a
POS (when, what, how many) and a technician need not re-drive it without
business value. Pure queries over existing tables - additive module, no schema
change, no engine change.
"""
from __future__ import annotations

import db


def _last_visit(pos_id: str, role: str) -> dict | None:
    r = db.get(
        "SELECT visit_date, technician, purpose FROM salesapp_visits "
        "WHERE pos_id = ? AND visitor_role = ? ORDER BY visit_date DESC LIMIT 1",
        (pos_id, role))
    return dict(r[0]) if r else None


def search(q: str, limit: int = 40) -> dict:
    """Full-text-ish POS search by number / name / city, with last visit.
    Powers the command-bar search on the main screen."""
    q = (q or "").strip()
    if not q:
        return {"query": q, "results": [], "count": 0}
    like = f"%{q}%"
    rows = db.get(
        "SELECT pos_id, name, city, technician, category, market, classification "
        "FROM pos_master WHERE active=1 AND "
        "(pos_id LIKE ? OR name LIKE ? OR city LIKE ?) "
        "ORDER BY (pos_id = ?) DESC, (pos_id LIKE ?) DESC, name LIMIT ?",
        (like, like, like, q, q + "%", limit))
    # last visit (any role) per matched POS in one pass
    ids = [str(r["pos_id"]) for r in rows]
    last: dict = {}
    if ids:
        marks = ",".join("?" for _ in ids)
        for r in db.get(
            f"SELECT pos_id, MAX(visit_date) AS lv FROM salesapp_visits "
            f"WHERE pos_id IN ({marks}) GROUP BY pos_id", tuple(ids)):
            last[str(r["pos_id"])] = r["lv"]
    results = []
    for r in rows:
        d = dict(r)
        d["lastVisit"] = last.get(str(r["pos_id"]))
        results.append(d)
    return {"query": q, "results": results, "count": len(results)}


def pos_visit_summary(pos_id: str) -> dict:
    """Everything the planner/POS Explorer needs about who has been at a POS."""
    counts = {row["visitor_role"] or "UNKNOWN": row["c"] for row in db.get(
        "SELECT visitor_role, COUNT(*) AS c FROM salesapp_visits "
        "WHERE pos_id = ? GROUP BY visitor_role", (pos_id,))}
    recent = [dict(r) for r in db.get(
        "SELECT visit_date, technician, visitor_role, purpose, started_at, finished_at "
        "FROM salesapp_visits WHERE pos_id = ? ORDER BY visit_date DESC LIMIT 20", (pos_id,))]
    return {
        "posId": pos_id,
        "lastTechnicianVisit": _last_visit(pos_id, "TECHNIK"),
        "lastOzVisit": _last_visit(pos_id, "OZ"),
        "technicianVisitCount": counts.get("TECHNIK", 0),
        "ozVisitCount": counts.get("OZ", 0),
        "totalVisitCount": sum(counts.values()),
        "recentVisits": recent,
    }
