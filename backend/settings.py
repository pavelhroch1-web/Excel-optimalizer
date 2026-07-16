"""Settings platform - everything configurable from the app, not the code.

setting_definitions = catalog (drives a generic admin UI); settings = values/
overrides. Effective value = override (most-specific scope) else the
definition default, typed per the definition. Engine / dashboards / reports /
maps only READ effective settings, so adding a KPI/weight/metric is data-only.
"""
from __future__ import annotations

import json

import db

_SCOPE_ORDER = {"global": 0, "market": 1, "category": 2, "technician": 3, "pos": 4}


def _cast(value, value_type):
    if value is None:
        return None
    try:
        if value_type == "number":
            f = float(value)
            return int(f) if f.is_integer() else f
        if value_type == "bool":
            return str(value).lower() in ("1", "true", "yes", "ano", "on")
        if value_type == "json":
            return json.loads(value) if isinstance(value, str) else value
    except (ValueError, TypeError):
        return value
    return value


def definitions(namespace: str | None = None) -> list[dict]:
    q = ("SELECT namespace, key, label, description, value_type, default_value, "
         "min_value, max_value, options, ui_group, sort_order FROM setting_definitions "
         "WHERE active=1")
    params: tuple = ()
    if namespace:
        q += " AND namespace=?"
        params = (namespace,)
    q += " ORDER BY namespace, sort_order, key"
    out = []
    for r in db.get(q, params):
        d = dict(r)
        if d.get("options"):
            try:
                d["options"] = json.loads(d["options"])
            except (ValueError, TypeError):
                pass
        d["default"] = _cast(d.pop("default_value"), d["value_type"])
        out.append(d)
    return out


def effective(namespace: str, context: dict | None = None) -> dict:
    """key -> typed effective value for a namespace (override else default)."""
    context = context or {}
    defs = {d["key"]: d for d in definitions(namespace)}
    result = {k: d["default"] for k, d in defs.items()}
    rows = sorted(db.get("SELECT key, value, scope, scope_value FROM settings WHERE namespace=?",
                         (namespace,)),
                  key=lambda r: _SCOPE_ORDER.get(r["scope"], 0))
    for r in rows:
        if r["scope"] != "global":
            if context.get(r["scope"]) is None or str(context[r["scope"]]) != str(r["scope_value"]):
                continue
        vtype = defs[r["key"]]["value_type"] if r["key"] in defs else "string"
        result[r["key"]] = _cast(r["value"], vtype)
    return result


def get(namespace: str, key: str, context: dict | None = None):
    return effective(namespace, context).get(key)


def set_value(namespace: str, key: str, value, scope: str = "global", scope_value=None) -> None:
    stored = json.dumps(value, ensure_ascii=False) if isinstance(value, (dict, list)) else \
        ("true" if value is True else "false" if value is False else str(value))
    # SQLite treats NULL as distinct in a UNIQUE index, so a NULL scope_value
    # would make ON CONFLICT never match and every save would append a duplicate
    # row. Global settings therefore store '' (not NULL) as the scope_value so
    # the upsert updates in place.
    db.run(
        "INSERT INTO settings (namespace, key, value, scope, scope_value) VALUES (?,?,?,?,?) "
        "ON CONFLICT(namespace, key, scope, scope_value) DO UPDATE SET "
        "value=excluded.value, updated_at=datetime('now')",
        (namespace, key, stored, scope, scope_value if scope_value is not None else ""))


# ---- saved views (dashboard / report / map) -------------------------------

def list_views(namespace: str) -> list[dict]:
    rows = db.get("SELECT id, namespace, name, definition, is_default FROM saved_views "
                  "WHERE namespace=? ORDER BY name", (namespace,))
    out = []
    for r in rows:
        d = dict(r)
        if d.get("definition"):
            try:
                d["definition"] = json.loads(d["definition"])
            except (ValueError, TypeError):
                pass
        out.append(d)
    return out


def save_view(namespace: str, name: str, definition, is_default: bool = False) -> None:
    db.run(
        "INSERT INTO saved_views (namespace, name, definition, is_default) VALUES (?,?,?,?) "
        "ON CONFLICT(namespace, name) DO UPDATE SET definition=excluded.definition, "
        "is_default=excluded.is_default, updated_at=datetime('now')",
        (namespace, name, json.dumps(definition, ensure_ascii=False), 1 if is_default else 0))


def delete_view(namespace: str, name: str) -> None:
    db.run("DELETE FROM saved_views WHERE namespace=? AND name=?", (namespace, name))
