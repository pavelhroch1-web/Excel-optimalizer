"""
Python port of office-scripts/PublishEngine.ts's main(). Line-for-line
translation - see import_engine.py's module docstring for the duplication
rationale and mock_workbook.py for why the class-based Range API is kept.
"""
from __future__ import annotations

import datetime

from .core_logic import iso_now
from .js_compat import at as _at
from .mock_workbook import MockWorkbook


def run(workbook: MockWorkbook) -> str:
    def read_table(sheet_name: str) -> list[list]:
        ws = workbook.get_worksheet(sheet_name)
        rng = ws.get_used_range()
        return rng.get_values() if rng else []

    manager_plan = read_table("MANAGER_PLAN")
    plan_lifecycle = read_table("PLAN_LIFECYCLE")
    control = read_table("CONTROL")

    def setting(name: str, fallback: float) -> float:
        for i in range(1, len(control)):
            if str(control[i][0]).upper().strip() == name.upper():
                try:
                    return float(control[i][1])
                except (TypeError, ValueError):
                    return fallback
        return fallback

    year = int(setting("YEAR", datetime.date.today().year))

    if len(manager_plan) < 2:
        return "Publish Engine: MANAGER_PLAN is empty - run Planning Engine first."

    locked_weeks: set[int] = set()
    pl_headers: list[str] = [str(h) for h in plan_lifecycle[0]] if len(plan_lifecycle) >= 2 else []

    def pl_idx(name: str) -> int:
        return pl_headers.index(name) if name in pl_headers else -1

    if len(plan_lifecycle) >= 2:
        for i in range(1, len(plan_lifecycle)):
            row = plan_lifecycle[i]
            if int(float(_at(row, pl_idx("year")) or 0)) != year:
                continue
            status = str(_at(row, pl_idx("status")))
            if status in ("Published", "Active", "Closed"):
                locked_weeks.add(int(float(_at(row, pl_idx("week")) or 0)))

    draft_weeks: set[int] = set()
    for i in range(1, len(manager_plan)):
        week_val = manager_plan[i][0]
        try:
            week = int(float(week_val)) if week_val not in (None, "") else 0
        except (TypeError, ValueError):
            week = 0
        if week and week not in locked_weeks:
            draft_weeks.add(week)

    if len(draft_weeks) == 0:
        return "Publish Engine: no Draft week found to publish (everything is already locked, or MANAGER_PLAN is empty)."

    week_to_publish = min(draft_weeks)

    rows_to_publish = [row for row in manager_plan[1:] if int(float(row[0])) == week_to_publish]

    now = iso_now()
    published_rows = [list(row) + [now] for row in rows_to_publish]

    published_ws = workbook.get_worksheet("MANAGER_PLAN_PUBLISHED")
    existing_published = published_ws.get_used_range()
    start_row = existing_published.get_row_count() if existing_published else 1
    published_ws.get_range_by_indexes(start_row, 0, len(published_rows), 18).set_values(published_rows)

    pl_ws = workbook.get_worksheet("PLAN_LIFECYCLE")
    existing_row_index = -1
    for i in range(1, len(plan_lifecycle)):
        row = plan_lifecycle[i]
        if int(float(_at(row, pl_idx("year")) or 0)) == year and int(float(_at(row, pl_idx("week")) or 0)) == week_to_publish:
            existing_row_index = i
            break
    if existing_row_index >= 0:
        pl_ws.get_range_by_indexes(existing_row_index, 2, 1, 2).set_values([["Published", now]])
    else:
        start_pl_row = len(plan_lifecycle) if len(plan_lifecycle) > 0 else 1
        pl_ws.get_range_by_indexes(start_pl_row, 0, 1, 5).set_values([[year, week_to_publish, "Published", now, ""]])

    return (
        f"Publish Engine: week {week_to_publish}/{year} published "
        f"({len(published_rows)} visits snapshotted to MANAGER_PLAN_PUBLISHED). "
        "This week is now locked - Planning Engine will not regenerate it."
    )
