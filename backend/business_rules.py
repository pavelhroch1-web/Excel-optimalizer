"""Business Rules loader - planning logic as data.

The Planning Engine only READS rules; here is where they live and get merged.
Scope precedence (most specific wins): global < market < category <
technician < pos. The db_state layer (Priority 2) calls `effective()` and
translates the enabled rules into the config the engine already consumes, so
the engine's algorithm never changes - toggling/editing a rule here changes
planning behaviour without touching code.
"""
from __future__ import annotations

import json

import db

_SCOPE_ORDER = {"global": 0, "market": 1, "category": 2, "technician": 3, "pos": 4}


def _parse(row) -> dict:
    d = dict(row)
    try:
        d["params"] = json.loads(d["params"]) if d.get("params") else {}
    except (ValueError, TypeError):
        d["params"] = {}
    return d


def list_rules() -> list[dict]:
    return [_parse(r) for r in db.get(
        "SELECT id, code, name, description, category, enabled, params, scope, "
        "scope_value, priority FROM business_rules ORDER BY code, scope")]


def effective(context: dict | None = None) -> dict:
    """Merged, enabled-aware rules keyed by code. `context` may carry
    {market, category, technician, pos} to apply matching scoped overrides;
    without it, only global rows apply. Most-specific scope wins; params are
    shallow-merged onto the global base."""
    context = context or {}
    by_code: dict[str, dict] = {}
    for r in sorted(list_rules(), key=lambda x: _SCOPE_ORDER.get(x["scope"], 0)):
        if r["scope"] != "global":
            if context.get(r["scope"]) is None or str(context[r["scope"]]) != str(r["scope_value"]):
                continue  # scoped rule doesn't apply to this context
        cur = by_code.get(r["code"])
        if cur is None:
            by_code[r["code"]] = {"code": r["code"], "enabled": bool(r["enabled"]),
                                  "category": r["category"], "params": dict(r["params"])}
        else:
            cur["enabled"] = bool(r["enabled"])
            cur["params"].update(r["params"])
    return by_code


def set_enabled(code: str, enabled: bool, scope: str = "global", scope_value=None) -> None:
    db.run("UPDATE business_rules SET enabled=?, updated_at=datetime('now') "
           "WHERE code=? AND scope=? AND (scope_value IS ? OR scope_value=?)",
           (1 if enabled else 0, code, scope, scope_value, scope_value))


def set_params(code: str, params: dict, scope: str = "global", scope_value=None) -> None:
    db.run("UPDATE business_rules SET params=?, updated_at=datetime('now') "
           "WHERE code=? AND scope=? AND (scope_value IS ? OR scope_value=?)",
           (json.dumps(params, ensure_ascii=False), code, scope, scope_value, scope_value))


def upsert(code: str, params: dict, *, name=None, category=None, enabled=True,
           scope="global", scope_value=None, priority=100) -> None:
    """Add or replace a rule (incl. a new scoped override) - no schema change.

    NULL-safe: ON CONFLICT(code, scope, scope_value) never fires when
    scope_value IS NULL (SQLite treats NULLs as distinct), which is exactly how
    the global rules got duplicated. So do an explicit NULL-safe UPDATE and only
    INSERT when nothing matched - one logical row per (code, scope, scope_value).
    """
    pj = json.dumps(params, ensure_ascii=False)
    conn = db.connect()
    try:
        cur = conn.execute(
            "UPDATE business_rules SET params=?, enabled=?, "
            "name=COALESCE(?, name), category=COALESCE(?, category), "
            "priority=?, updated_at=datetime('now') "
            "WHERE code=? AND scope=? AND (scope_value IS ? OR scope_value=?)",
            (pj, 1 if enabled else 0, name, category, priority,
             code, scope, scope_value, scope_value))
        if cur.rowcount == 0:
            conn.execute(
                "INSERT INTO business_rules (code, name, category, enabled, params, scope, scope_value, priority) "
                "VALUES (?,?,?,?,?,?,?,?)",
                (code, name, category, 1 if enabled else 0, pj, scope, scope_value, priority))
        conn.commit()
    finally:
        conn.close()
