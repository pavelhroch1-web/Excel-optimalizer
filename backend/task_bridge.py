"""Task Engine → Planning Engine bridge (config overlay only).

The Planning Engine must *account for* open tasks (service / campaign / material)
without ever changing its algorithm. We do it exactly like the OZ-campaign
priority overlay (`db_state._apply_priority`): a POS that has an open task which
needs its own visit is marked `managerOverrideType = FORCE_INCLUDE`, so the
engine guarantees it a slot. Everything else is left to the engine.

Two cases, one rule:
  * combinable task, deadline far off  -> DO NOTHING. It piggybacks for free when
    the POS is visited anyway (handled by the bundling layer at display/export).
  * task needs a dedicated visit (not combinable, or deadline within horizon)
    -> FORCE_INCLUDE, so a visit actually gets planned in time.

This module writes only into the in-memory planning `state`. It touches no
engine code and persists nothing.
"""
from __future__ import annotations

import datetime

import db

_URGENT_DAYS = 14   # keep in sync with tasks._URGENT_DAYS


def _needs_dedicated_pos() -> set:
    """POS ids with an open task that warrants its own visit."""
    today = datetime.date.today()
    rows = db.get(
        "SELECT t.pos_id, t.deadline, "
        "COALESCE(t.combinable, tt.combinable, 1) AS comb "
        "FROM tasks t LEFT JOIN task_types tt ON tt.id = t.type_id "
        "WHERE t.status = 'open'")
    out = set()
    for r in rows:
        comb = r["comb"]
        dtd = None
        if r["deadline"]:
            try:
                dtd = (datetime.date.fromisoformat(str(r["deadline"])[:10]) - today).days
            except (ValueError, TypeError):
                dtd = None
        needs = (not comb) or (dtd is not None and dtd <= _URGENT_DAYS)
        if needs:
            out.add(str(r["pos_id"]))
    return out


def apply_to_state(state: dict) -> int:
    """Mark needs-dedicated task POS as FORCE_INCLUDE in POS_MASTER. Returns how
    many rows were promoted. Never raises into the planning pipeline."""
    try:
        ids = _needs_dedicated_pos()
        if not ids:
            return 0
        pm = state.get("POS_MASTER")
        if not pm:
            return 0
        h = {n: i for i, n in enumerate(pm[0])}
        pi, ti = h.get("posId"), h.get("managerOverrideType")
        if pi is None or ti is None:
            return 0
        n = 0
        for row in pm[1:]:
            pid = str(row[pi]) if pi < len(row) else ""
            if pid in ids and str(row[ti]).upper() != "FORCE_EXCLUDE":  # explicit exclude wins
                row[ti] = "FORCE_INCLUDE"
                n += 1
        return n
    except Exception:  # noqa: BLE001 - config overlay must never block planning
        return 0
