"""
Python port of office-scripts/ActivatePOSEngine.ts's main(). See
import_engine.py's module docstring for the duplication rationale. See
ActivatePOSEngine.ts's own file header for the full business-rule rationale
(never reassigns a POS's technician, two mutually-exclusive selection
modes, idempotency) - this file intentionally does not re-explain it, only
mirrors the logic.
"""
from __future__ import annotations

import datetime

from .core_logic import category_rule, norm
from .js_compat import at as _at, num as _num
from .mock_workbook import MockWorkbook


def run(workbook: MockWorkbook) -> str:
    def read_table(sheet_name: str) -> list[list]:
        ws = workbook.get_worksheet(sheet_name)
        rng = ws.get_used_range()
        return rng.get_values() if rng else []

    pos_master = read_table("POS_MASTER")
    if len(pos_master) < 2:
        return "Activate POS Engine: POS_MASTER is empty - nothing to do."
    category_rules_raw = read_table("CATEGORY_RULES")
    activate_list_raw = read_table("POS_ACTIVATE_LIST")
    control = read_table("CONTROL")

    def setting(name: str, fallback: float) -> float:
        for i in range(1, len(control)):
            if norm(str(control[i][0])) == norm(name):
                try:
                    return float(control[i][1])
                except (TypeError, ValueError):
                    return fallback
        return fallback

    activate_count_by_ppt = int(setting("ACTIVATE_COUNT_BY_PPT", 0))

    category_rules_table: list[dict] = []
    for i in range(1, len(category_rules_raw)):
        category_rules_table.append({
            "key": norm(str(category_rules_raw[i][0])),
            "value": norm(str(category_rules_raw[i][1])),
        })

    pm_headers = [str(h) for h in pos_master[0]]

    def pm_idx(name: str) -> int:
        return pm_headers.index(name) if name in pm_headers else -1

    pos_id_col = pm_idx("posId")
    status_col = pm_idx("status")
    category_col = pm_idx("category")
    override_type_col = pm_idx("managerOverrideType")
    ppt_col = pm_idx("ppt")
    notes_col = pm_idx("plannerNotes")

    explicit_ids: set[str] = set()
    for i in range(1, len(activate_list_raw)):
        v = str(_at(activate_list_raw[i], 0) or "").strip()
        if v:
            explicit_ids.add(v)

    pos_ws = workbook.get_worksheet("POS_MASTER")
    today = datetime.date.today().isoformat()
    activated: list[str] = []
    already_active = 0
    skipped_force_exclude: list[str] = []

    def activate_row(row_index: int, row: list, via: str) -> None:
        nonlocal already_active
        current_override = norm(str(_at(row, override_type_col) or ""))
        if current_override == "FORCE_INCLUDE":
            already_active += 1
            return
        pos_ws.get_range_by_indexes(row_index, override_type_col, 1, 1).set_value("FORCE_INCLUDE")
        existing_notes = str(_at(row, notes_col) or "")
        pos_ws.get_range_by_indexes(row_index, notes_col, 1, 1).set_value(
            (existing_notes + " | " if existing_notes else "") + "Hromadně aktivováno" + via + " " + today
        )

    mode = ""
    if explicit_ids:
        mode = "seznam (POS_ACTIVATE_LIST)"
        for i in range(1, len(pos_master)):
            row = pos_master[i]
            pos_id = str(_at(row, pos_id_col))
            if pos_id not in explicit_ids:
                continue
            if str(_at(row, status_col)) != "Active":
                continue
            current_override = norm(str(_at(row, override_type_col) or ""))
            if current_override == "FORCE_EXCLUDE":
                skipped_force_exclude.append(pos_id)
                continue
            activate_row(i, row, "")
            activated.append(pos_id)
    elif activate_count_by_ppt > 0:
        mode = f"prvních {activate_count_by_ppt} podle PPT"
        pool: list[dict] = []
        for i in range(1, len(pos_master)):
            row = pos_master[i]
            if str(_at(row, status_col)) != "Active":
                continue
            current_override = norm(str(_at(row, override_type_col) or ""))
            if current_override in ("FORCE_EXCLUDE", "FORCE_INCLUDE"):
                continue
            category = str(_at(row, category_col))
            rule = category_rule(category_rules_table, norm(category))
            if rule != "EXCLUDE":
                continue
            pool.append({
                "index": i, "posId": str(_at(row, pos_id_col)),
                "ppt": _num(_at(row, ppt_col)) or 0, "row": row,
            })
        pool.sort(key=lambda item: item["ppt"], reverse=True)
        for item in pool[:activate_count_by_ppt]:
            activate_row(item["index"], item["row"], " (PPT)")
            activated.append(item["posId"])
    else:
        return "Activate POS Engine: POS_ACTIVATE_LIST is empty and CONTROL.ACTIVATE_COUNT_BY_PPT is 0 - nothing to activate."

    skipped_note = f": {', '.join(skipped_force_exclude)}" if skipped_force_exclude else ""
    return (
        f"Activate POS Engine: mode = {mode}. "
        f"{len(activated)} POS newly/already set to FORCE_INCLUDE ({already_active} already were), "
        f"{len(skipped_force_exclude)} skipped (explicit FORCE_EXCLUDE always wins){skipped_note}. "
        "Run Planning Engine next to see them enter this week's plan."
    )
