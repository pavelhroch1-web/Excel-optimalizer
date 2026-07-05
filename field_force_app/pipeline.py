"""
Runs the already-tested engine ports (desktop_client/engines/) against the
managed workbook, grouped into the 3 stages this app exposes as buttons -
see app.py's module docstring for why 3, not 1 or 8: two of the original 8
office-scripts/*.ts steps are deliberate human checkpoints (review before
Publish, decide when to Start Tracking - see StartTrackingEngine.ts's file
header), so collapsing everything into a single click would silently remove
a checkpoint the product owner explicitly asked for earlier in this project,
not simplify it.

Does not duplicate any business logic - every stage below is just sequencing
calls into desktop_client/engines/*.py and desktop_client/xlsx_engine_io.py,
exactly like desktop_client/distribution_client.py's "Lokální spuštění
enginů" panel does.
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from desktop_client import xlsx_engine_io  # noqa: E402
from desktop_client.engines import (  # noqa: E402
    advisor_engine,
    compliance_engine,
    import_engine,
    performance_engine,
    planning_engine,
    publish_engine,
    reporting_engine,
    start_tracking_engine,
)
from desktop_client.engines.mock_workbook import MockWorkbook  # noqa: E402


def _run_stage(path: str, steps: list[tuple[str, callable, set[str]]]) -> list[str]:
    xlsx_engine_io.backup_workbook(path)
    state = xlsx_engine_io.read_state(path)
    workbook = MockWorkbook(state)
    log: list[str] = []
    all_output_sheets: set[str] = set()
    for name, fn, output_sheets in steps:
        log.append(f"[{name}] {fn(workbook)}")
        all_output_sheets |= output_sheets
    xlsx_engine_io.write_state(path, workbook.dump(), all_output_sheets)
    return log


def run_planning_stage(path: str) -> list[str]:
    """Import + Planning - produces/updates the Draft tour plan. Safe to
    re-run any time (Planning Engine never touches a locked week)."""
    return _run_stage(path, [
        ("Import", import_engine.run, {"POS_MASTER"}),
        ("Planning", planning_engine.run, {"MANAGER_PLAN", "PLAN_LIFECYCLE"}),
    ])


def run_publish_stage(path: str) -> list[str]:
    """Publish (locks the nearest Draft week, sends it to technicians) +
    Start Tracking (tells the manager screens which weeks count) - kept as
    an explicit separate action, not folded into run_planning_stage, since
    both are deliberate manager decisions, not automatic steps."""
    return _run_stage(path, [
        ("Publish", publish_engine.run, {"MANAGER_PLAN_PUBLISHED", "PLAN_LIFECYCLE"}),
        ("Start Tracking", start_tracking_engine.run, {"PLAN_LIFECYCLE"}),
    ])


def run_evaluation_stage(path: str) -> list[str]:
    """Compliance + Advisor + Performance + Reporting - run together after
    a new SalesApp report, since none of these has an independent "wait
    for a human decision" reason to be separated (unlike Publish/Start
    Tracking above)."""
    return _run_stage(path, [
        ("Compliance", compliance_engine.run, {
            "VISIT_HISTORY_ACTUAL", "OTHER_VISIT_LOG", "COMPLIANCE_LOG", "PLAN_LIFECYCLE", "POS_MASTER",
        }),
        ("Advisor", advisor_engine.run, {"ADVISOR_LOG"}),
        ("Performance", performance_engine.run, {
            "TECHNICIAN_PERFORMANCE_LOG", "TECHNICIAN_PERFORMANCE_SUMMARY", "TECHNICIAN_TOP_ISSUES",
        }),
        ("Reporting", reporting_engine.run, {"DASHBOARD", "POS_MAP_DATA"}),
    ])
