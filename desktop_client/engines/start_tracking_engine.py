"""
Python port of office-scripts/StartTrackingEngine.ts's main(). See
import_engine.py's module docstring for the duplication rationale.
"""
from __future__ import annotations

from .core_logic import iso_now
from .js_compat import at as _at
from .mock_workbook import MockWorkbook


def run(workbook: MockWorkbook) -> str:
    def read_table(sheet_name: str) -> list[list]:
        ws = workbook.get_worksheet(sheet_name)
        rng = ws.get_used_range()
        return rng.get_values() if rng else []

    plan_lifecycle = read_table("PLAN_LIFECYCLE")
    if len(plan_lifecycle) < 2:
        return "Start Tracking Engine: PLAN_LIFECYCLE is empty - nothing to start."

    headers = [str(h) for h in plan_lifecycle[0]]

    def idx(name: str) -> int:
        return headers.index(name) if name in headers else -1

    status_col = idx("status")
    tracking_col = idx("trackingStartedAt")
    if tracking_col < 0:
        return "Start Tracking Engine: PLAN_LIFECYCLE has no trackingStartedAt column - nothing to do."

    now = iso_now()
    pl_ws = workbook.get_worksheet("PLAN_LIFECYCLE")
    started: list[str] = []
    for i in range(1, len(plan_lifecycle)):
        row = plan_lifecycle[i]
        status = str(_at(row, status_col))
        already_started = str(_at(row, tracking_col) or "") != ""
        if status in ("Published", "Active", "Closed") and not already_started:
            pl_ws.get_range_by_indexes(i, tracking_col, 1, 1).set_value(now)
            started.append(f"{_at(row, idx('year'))}/W{_at(row, idx('week'))}")

    if started:
        return f"Start Tracking Engine: started tracking {len(started)} week(s): {', '.join(started)}."
    return "Start Tracking Engine: no Published/Active/Closed week is waiting to start tracking - nothing to do."
