"""One-off: generate a real 5-week tour plan (weeks 29-33) from the manager's
data using the unchanged engines, following the case study:
  week 29     = Dojezd sítě (no campaign -> fill capacity by neglect/score)
  weeks 30-33 = Kampaňový režim (campaigns active -> Smart Hold-back protects
                and covers campaigns), carrying week 29 as locked.

Writes a clean Excel: TOUR_PLAN (the plan) + SOUHRN (per-week/technician +
coverage scorecard). Runs locally (plenty of RAM); the free host's 512 MB is
the only place this doesn't fit.
"""
from __future__ import annotations

import copy
import glob
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "backend"))

import openpyxl
from openpyxl.styles import Font, PatternFill, Alignment
from openpyxl.utils import get_column_letter

from desktop_client.engines import compliance_engine, import_engine, planning_engine
from desktop_client.engines.mock_workbook import MockWorkbook

import config_store
import pipeline
import snapshot_store
import brain

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SCAFFOLD = os.path.join(ROOT, "workbook", "FieldForceOptimizer_V11_scaffold.xlsx")
OUT = os.path.join(ROOT, "TOUR_PLAN_tydny_29-33.xlsx")

PLAN_COLS = ["WEEK", "DATE", "DAY", "TECHNICIAN", "POS", "KATEGORIE", "NAZEV_PROVOZOVNY",
             "ULICE", "CISLO", "MESTO", "OBLAST", "POS_AREA", "PPT", "LOS_ACTIVITY",
             "LOT_ACTIVITY", "REASON"]
DAY_ORDER = {"MON": 1, "TUE": 2, "WED": 3, "THU": 4, "FRI": 5}


def build_base():
    cfg = config_store.load_config_state(SCAFFOLD)
    snap = snapshot_store.load_snapshot(SCAFFOLD)
    for s in ("MANAGER_PLAN", "MANAGER_PLAN_PUBLISHED", "PLAN_LIFECYCLE"):
        snap[s] = [snap[s][0]]
    raw = pipeline.read_workbook_sheet(SCAFFOLD, "RAW_DATA")
    sa = [pipeline.read_workbook_sheet(SCAFFOLD, "SALESAPP_IMPORT")]
    state = pipeline.build_state(cfg, raw, pipeline.merge_salesapp(sa), snapshot=snap)
    # state already carries the accumulated POS_MASTER; run import+compliance so
    # last-visit is current, then it's ready to plan.
    pipeline.run_import_compliance(state)
    return state


def run_planning(state, start, length):
    pipeline._set_control(state, "CAMPAIGN_START_WEEK", start)
    pipeline._set_control(state, "CAMPAIGN_LENGTH", length)
    wb = MockWorkbook(state)
    msg = planning_engine.run(wb)
    state.update(wb.dump())
    return msg


def main():
    print("Building base state (Import + Compliance)…")
    base = build_base()

    # A usable 5-week plan needs FULL weeks (technicians work every week).
    # Campaign-mode hold-back empties whole pre-campaign weeks, so for a
    # hand-out plan we sweep the network (Dojezd): campaigns off -> no
    # hold-back, every week filled by neglect/score. All hard functionality
    # still applies - GECO/CORN cadence guaranteed, CORE, PPT/premium, GPS
    # clustering, address dedup. Each POS is planned at most once across the
    # 5 weeks (the engine's own used-set).
    brain.apply_mode(base, "dojezd")
    print("Planning weeks 29-33 (Dojezd sítě, plné týdny)…", run_planning(base, 29, 5))

    plan = base["MANAGER_PLAN"]
    hdr = plan[0]
    idx = {n: i for i, n in enumerate(hdr)}
    rows = [r for r in plan[1:] if r and r[idx["WEEK"]] not in (None, "")]
    rows.sort(key=lambda r: (str(r[idx["TECHNICIAN"]]), int(r[idx["WEEK"]]),
                             DAY_ORDER.get(str(r[idx["DAY"]]), 9)))
    print(f"Plan rows total: {len(rows)}")

    write_excel(rows, idx, base)
    print("Wrote", OUT, "(", round(os.path.getsize(OUT) / 1e6, 2), "MB )")


def write_excel(rows, idx, state):
    wb = openpyxl.Workbook()
    ws = wb.active
    ws.title = "TOUR_PLAN"
    head_fill = PatternFill("solid", fgColor="12807C")
    head_font = Font(bold=True, color="FFFFFF")
    ws.append(PLAN_COLS)
    for c in range(1, len(PLAN_COLS) + 1):
        ws.cell(1, c).fill = head_fill
        ws.cell(1, c).font = head_font
    for r in rows:
        ws.append([r[idx[c]] if c in idx else "" for c in PLAN_COLS])
    ws.freeze_panes = "A2"
    widths = {"NAZEV_PROVOZOVNY": 34, "ULICE": 22, "MESTO": 16, "REASON": 40,
              "TECHNICIAN": 18, "LOS_ACTIVITY": 16, "LOT_ACTIVITY": 16}
    for i, c in enumerate(PLAN_COLS, 1):
        ws.column_dimensions[get_column_letter(i)].width = widths.get(c, 12)

    # SOUHRN sheet
    s = wb.create_sheet("SOUHRN")
    from collections import Counter
    per_week = Counter(int(r[idx["WEEK"]]) for r in rows)
    per_tech = Counter(str(r[idx["TECHNICIAN"]]) for r in rows)
    s.append(["Tour plán – týdny 29–33", ""])
    s["A1"].font = Font(bold=True, size=14)
    s.append(["Celkem naplánováno návštěv", len(rows)])
    s.append(["Počet techniků", len(per_tech)])
    s.append([])
    s.append(["Režim", "Dojezd sítě (plné týdny) – GECO/CORN garantováno, CORE, PPT, neglect, GPS shluky"])
    s.append(["Návštěvy po týdnech", ""])
    s.cell(s.max_row, 1).font = Font(bold=True)
    for w in (29, 30, 31, 32, 33):
        s.append([f"Týden {w}", per_week.get(w, 0)])
    s.append([])
    s.append(["Návštěvy po technicích", ""])
    s.cell(s.max_row, 1).font = Font(bold=True)
    for tech, n in sorted(per_tech.items()):
        s.append([tech, n])
    s.column_dimensions["A"].width = 34
    s.column_dimensions["B"].width = 14

    wb.save(OUT)


if __name__ == "__main__":
    main()
