"""'Kandidáti POS' read-out: runs the REAL Planning Engine
(desktop_client/engines/planning_engine.py) for one week with its
observability hook enabled, and returns every candidate POS with the score
and component breakdown the engine itself computed - plus each POS's real
last-visit date and recent SalesApp visit history.

This module contains NO scoring logic. It calls planning_engine.run() with
candidates_out=[] - the identical function, identical algorithm, that
writes MANAGER_PLAN. The engine's own _assert_breakdown() guarantees the
component numbers shown here sum to the score it actually used. Confirmed
byte-equivalent to PlanningEngine.ts via tools/sim/compare_engines.py.
"""
from __future__ import annotations

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import openpyxl

from desktop_client import xlsx_engine_io
from desktop_client.engines import planning_engine
from desktop_client.engines.mock_workbook import MockWorkbook


def _set_control(state: dict, key: str, value) -> None:
    control = state["CONTROL"]
    key_norm = key.strip().upper()
    for row in control[1:]:
        if str(row[0]).strip().upper() == key_norm:
            row[1] = value
            return
    control.append([key, value, ""])


def _pos_master_extras(path: str) -> tuple[dict, dict]:
    """Returns (lastRealVisitDate by posId, sorted recent visit-date list by
    posId from VISIT_HISTORY_ACTUAL - the real SalesApp visit history)."""
    wb = openpyxl.load_workbook(path, read_only=True, data_only=True)
    try:
        pm = wb["POS_MASTER"]
        h = [c.value for c in next(pm.iter_rows(min_row=1, max_row=1))]
        idx = {n: i for i, n in enumerate(h)}
        last_visit: dict[str, str] = {}
        for row in pm.iter_rows(min_row=2, values_only=True):
            pid = row[idx["posId"]]
            if pid:
                lv = row[idx["lastRealVisitDate"]]
                last_visit[str(pid)] = "" if lv in (None, "") else str(lv)[:10]

        history: dict[str, list] = {}
        vh = wb["VISIT_HISTORY_ACTUAL"]
        vh_h = [c.value for c in next(vh.iter_rows(min_row=1, max_row=1))]
        vidx = {n: i for i, n in enumerate(vh_h)}
        for row in vh.iter_rows(min_row=2, values_only=True):
            pid = row[vidx["posId"]]
            if not pid:
                continue
            d = row[vidx["date"]]
            ex = row[vidx["executor"]]
            if d:
                history.setdefault(str(pid), []).append({"date": str(d)[:10], "executor": ex})
        for pid in history:
            history[pid].sort(key=lambda v: v["date"], reverse=True)
        return last_visit, history
    finally:
        wb.close()


def list_candidates(path: str, week: int, technician: str | None = None) -> dict:
    """Runs the engine for `week` and returns its scored candidate pool. Does
    NOT persist anything - the engine writes MANAGER_PLAN into the in-memory
    MockWorkbook only, which is discarded."""
    state = xlsx_engine_io.read_state(path)
    _set_control(state, "CAMPAIGN_START_WEEK", week)
    _set_control(state, "CAMPAIGN_LENGTH", 1)

    wb = MockWorkbook(state)
    captured: list[dict] = []
    planning_engine.run(wb, candidates_out=captured)

    last_visit, history = _pos_master_extras(path)
    for c in captured:
        c["lastRealVisitDate"] = last_visit.get(str(c["pos"]), "")
        c["visitHistory"] = history.get(str(c["pos"]), [])

    if technician:
        captured = [c for c in captured if c["tech"] == technician]

    captured.sort(key=lambda c: (0 if c["status"] == "Vybráno" else 1, -c["score"]))
    techs = sorted({c["tech"] for c in captured})
    return {
        "week": week,
        "technicians": techs,
        "total": len(captured),
        "selected": sum(1 for c in captured if c["status"] == "Vybráno"),
        "candidates": captured,
    }
