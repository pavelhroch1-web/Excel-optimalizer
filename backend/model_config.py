"""Planning-model configurator: turn the engine's config sheets into
UI-editable sections (mostly checkboxes / choices), backed by a generic
override table.

The base data lives in the exact config sheets the engine already reads
(TERMINAL_RULES, MARKET_RULES, CATEGORY_RULES, ACTIVITY_PLAN). This module
reads those base rows (via rules_io - the same reader used everywhere),
merges the manager's edits from the `model_overrides` table, and
`apply_to_state` overlays those edits onto the engine's in-memory config
sheets right before planning. Result: the whole planning model is
configurable from the UI - which terminals / markets / categories /
activities are active, a category's rule, an activity's priority/window -
with no algorithm change and no workbook write. Adding a new terminal type,
market or activity is data (a new row in the imported config), not code.

Design mirrors cadence_config.py exactly (SQLite override + apply_to_state),
so the two together cover the whole model: cadence_config = customer types
(CADENCE_RULES), model_config = terminals / partners / categories /
activities.
"""
from __future__ import annotations

import db
import store

# Every section maps to one config sheet. `keys` identify a row; `fields`
# are the editable columns and how the UI renders them. A field marked
# virtual=True is not a real sheet column - it is interpreted at apply time
# (used for an activity's on/off, which the sheet expresses by presence).
SECTIONS = {
    "terminals": {
        "sheet": "TERMINAL_RULES",
        "label": "Terminály",
        "help": "Které typy terminálů se plánují.",
        "keys": ["TYP TERMINALU"],
        "fields": [{"col": "ACTIVE", "type": "bool", "label": "Plánovat"}],
    },
    "partners": {
        "sheet": "MARKET_RULES",
        "label": "Partneři / trhy",
        "help": "Které trhy (partneři) jsou v plánu.",
        "keys": ["MARKET"],
        "fields": [{"col": "ACTIVE", "type": "bool", "label": "Plánovat"}],
    },
    "categories": {
        "sheet": "CATEGORY_RULES",
        "label": "Kategorie",
        "help": "Jak se s kategorií POS zachází.",
        "keys": ["CATEGORY"],
        "fields": [{
            "col": "RULE", "type": "choice", "label": "Pravidlo",
            "choices": ["CORE", "NORMAL", "EXCLUDE"],
        }],
    },
    "activities": {
        "sheet": "ACTIVITY_PLAN",
        "label": "Activity plán",
        "help": "Kampaně / aktivity, jejich okno, priorita a zapnutí.",
        "keys": ["TYPE", "ACTIVITY"],
        "fields": [
            {"col": "ACTIVE", "type": "bool", "label": "Zapnuto", "virtual": True},
            {"col": "PRIORITY", "type": "number", "label": "Priorita"},
            {"col": "START_WEEK", "type": "number", "label": "Od týdne"},
            {"col": "END_WEEK", "type": "number", "label": "Do týdne"},
            {"col": "OVERRIDE_GAP", "type": "bool", "label": "Přebít rozestup"},
        ],
    },
}

_BOOL_TRUE = {"YES", "1", "TRUE", "ANO", "Y", "ON"}


def _mk(keys: list[str], row: dict) -> str:
    return "|".join(str(row.get(k, "")).strip() for k in keys)


def _is_true(v) -> bool:
    return str(v).strip().upper() in _BOOL_TRUE


def _overrides(sheet: str) -> dict:
    """{match_key: {col: value}} for one sheet."""
    out: dict[str, dict] = {}
    for r in db.get("SELECT match_key, col, value FROM model_overrides WHERE sheet=?", (sheet,)):
        out.setdefault(r["match_key"], {})[r["col"]] = r["value"]
    return out


def _base_rows() -> dict[str, list[dict]]:
    import rules_io
    path = store.snapshot_temp()
    try:
        return rules_io.read_all_rule_sheets(path)
    finally:
        import os
        try:
            os.remove(path)
        except OSError:
            pass


def sections() -> list[dict]:
    """All configurator sections with effective values (base + overrides)."""
    base = _base_rows()
    out = []
    for sid, spec in SECTIONS.items():
        ov = _overrides(spec["sheet"])
        rows = []
        for row in base.get(spec["sheet"], []):
            mk = _mk(spec["keys"], row)
            if not mk.strip("|"):
                continue
            o = ov.get(mk, {})
            item = {"key": mk, "label": " · ".join(
                str(row.get(k, "")) for k in spec["keys"] if row.get(k) not in (None, ""))}
            for f in spec["fields"]:
                col = f["col"]
                if col == "ACTIVE" and f.get("virtual"):
                    base_val = True  # an activity present in the sheet is active
                elif f["type"] == "bool":
                    base_val = _is_true(row.get(col))
                else:
                    base_val = row.get(col)
                if col in o:
                    val = _is_true(o[col]) if f["type"] == "bool" else o[col]
                    item[col] = val
                else:
                    item[col] = base_val
                item[col + "_overridden"] = col in o
            rows.append(item)
        out.append({
            "id": sid, "label": spec["label"], "help": spec["help"],
            "keys": spec["keys"], "fields": spec["fields"], "rows": rows,
        })
    return out


def set_override(section: str, match_key: str, col: str, value) -> None:
    spec = SECTIONS.get(section)
    if not spec:
        raise ValueError(f"Neznámá sekce: {section}")
    field = next((f for f in spec["fields"] if f["col"] == col), None)
    if not field:
        raise ValueError(f"Sekce {section} nemá pole {col}")
    if field["type"] == "bool":
        stored = "YES" if (value in (True, 1) or str(value).strip().upper() in _BOOL_TRUE) else "NO"
    else:
        stored = None if value in (None, "") else str(value)
    db.run(
        "INSERT INTO model_overrides (sheet, match_key, col, value, updated_at) "
        "VALUES (?, ?, ?, ?, datetime('now')) "
        "ON CONFLICT(sheet, match_key, col) DO UPDATE SET "
        "value=excluded.value, updated_at=datetime('now')",
        (spec["sheet"], match_key, col, stored))


def enable_all() -> dict:
    """"Plan the whole network": turn every terminal type and partner ON, and
    flip any EXCLUDE category to NORMAL. This is what lets the engine consider
    the full POS master instead of the currently-enabled subset. Returns what it
    changed so the UI can say so. Categories already CORE/NORMAL are left as-is
    (their cadence rule is a business choice, not an on/off)."""
    changed = {"terminals": 0, "partners": 0, "categories": 0}
    for sec in sections():
        sid = sec["id"]
        for row in sec["rows"]:
            if sid in ("terminals", "partners"):
                if row.get("ACTIVE") is not True:
                    set_override(sid, row["key"], "ACTIVE", True)
                    changed[sid] += 1
            elif sid == "categories":
                if str(row.get("RULE") or "").upper() == "EXCLUDE":
                    set_override("categories", row["key"], "RULE", "NORMAL")
                    changed["categories"] += 1
    changed["total"] = sum(changed.values())
    return changed


def reset(section: str, match_key: str, col: str | None = None) -> None:
    spec = SECTIONS.get(section)
    if not spec:
        return
    if col:
        db.run("DELETE FROM model_overrides WHERE sheet=? AND match_key=? AND col=?",
               (spec["sheet"], match_key, col))
    else:
        db.run("DELETE FROM model_overrides WHERE sheet=? AND match_key=?",
               (spec["sheet"], match_key))


def apply_to_state(state: dict) -> int:
    """Overlay model_overrides onto the engine's config sheets in `state`.
    Called by db_state.configure before the engine runs. Returns cells set."""
    n = 0
    for spec in SECTIONS.values():
        ov = _overrides(spec["sheet"])
        if not ov:
            continue
        sheet = state.get(spec["sheet"])
        if not sheet:
            continue
        h = {str(c): i for i, c in enumerate(sheet[0])}
        key_idx = [h.get(k) for k in spec["keys"]]
        if any(i is None for i in key_idx):
            continue
        for row in sheet[1:]:
            mk = "|".join(str(row[i]).strip() if i < len(row) and row[i] is not None else ""
                          for i in key_idx)
            o = ov.get(mk)
            if not o:
                continue
            for col, val in o.items():
                field = next((f for f in spec["fields"] if f["col"] == col), None)
                if not field:
                    continue
                if col == "ACTIVE" and field.get("virtual"):
                    # An activity is turned off by clearing its TYPE key cell:
                    # the engine skips ACTIVITY_PLAN rows with an empty TYPE.
                    if not _is_true(val):
                        ti = h.get(spec["keys"][0])
                        if ti is not None and ti < len(row):
                            row[ti] = ""
                            n += 1
                    continue
                ci = h.get(col)
                if ci is None or ci >= len(row):
                    continue
                if field["type"] == "bool":
                    row[ci] = "YES" if _is_true(val) else "NO"
                else:
                    row[ci] = val
                n += 1
    return n
