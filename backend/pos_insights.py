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
