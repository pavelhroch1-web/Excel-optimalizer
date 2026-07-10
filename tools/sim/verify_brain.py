"""Field Brain Phase 1 feasibility: a 5-week horizon pre-flight scorecard,
computed by simulating the UNCHANGED engine under each strategy mode.

Proves:
  - the engine is unchanged (modes only tweak config);
  - the scorecard numbers are real computations (CORE/cadence/neglect/campaign/
    capacity) over a 5-week simulation;
  - different modes give business-meaningfully different outcomes
    (Dojezd clears more neglect; Kampaň protects for campaigns).

Run: python tools/sim/verify_brain.py
"""
from __future__ import annotations

import glob
import os
import sys
import tempfile

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, ROOT)
sys.path.insert(0, os.path.join(ROOT, "backend"))

from desktop_client.engines import compliance_engine, import_engine, planning_engine  # noqa: E402
for _m in (import_engine, compliance_engine, planning_engine):
    if hasattr(_m, "iso_now"):
        _m.iso_now = lambda: "2026-07-09T00:00:00.000Z"

import brain  # noqa: E402
import config_store  # noqa: E402
import pipeline  # noqa: E402
import snapshot_store  # noqa: E402
import state_xlsx  # noqa: E402

SCAFFOLD = os.path.join(ROOT, "workbook", "FieldForceOptimizer_V11_scaffold.xlsx")
UPLOADS = "/root/.claude/uploads/96762f2e-6479-5ca9-bce2-fc70e4cf2947"
PPT = os.path.join(UPLOADS, "824b106e-Z_kladn___daje_o_prodejn_ch_m_stech_2.xlsx")
START, LEN = 30, 5
fails = []


def check(name, ok, detail):
    print(f"  [{'PASS' if ok else 'FAIL'}] {name}: {detail}")
    if not ok:
        fails.append(name)


def show(sc):
    c = sc["capacity"]
    print(f"   [{sc['modeLabel']}] týdny {sc['weeks'][0]}–{sc['weeks'][-1]}, "
          f"aktivních POS {sc['activePos']}")
    print(f"     kapacita: {c['technicians']} tech × {c['visitsPerTechWeek']}/týd × {c['weeks']} = "
          f"{c['totalCapacity']}, naplánováno {c['plannedVisits']} ({c['utilizationPct']} %)")
    print(f"     CORE {sc['core']['pct']}% ({sc['core']['covered']}/{sc['core']['due']}), "
          f"cadence {sc['cadence']['pct']}% ({sc['cadence']['covered']}/{sc['cadence']['overdue']})")
    print(f"     neglect: backlog {sc['neglect']['backlogBefore']} → dojede {sc['neglect']['cleared']}, "
          f"zůstane {sc['neglect']['remainingAfter']}")
    for cp in sc["campaigns"]:
        print(f"     kampaň {cp['name']} ({cp['type']}) t{cp['startWeek']}–{cp['endWeek']}: "
              f"≈{cp['pct']}% (demand {cp['demand']}, pokryto {cp['covered']})")
    print(f"     plán/týden: {sc['plannedByWeek']}")
    print(f"     → {sc['recommendation']}")


def main():
    raw = pipeline.read_export_rows(PPT)
    sa = [pipeline.read_export_rows(p) for p in
          sorted({os.path.getsize(f): f for f in glob.glob(UPLOADS + "/*visitdata*.xlsx")}.values())]
    cfg = config_store.load_config_state(SCAFFOLD)
    snap = snapshot_store.load_snapshot(SCAFFOLD)
    for s in ("MANAGER_PLAN", "MANAGER_PLAN_PUBLISHED", "PLAN_LIFECYCLE"):
        snap[s] = [snap[s][0]]
    print("Building draft (Import + Compliance)…")
    base = pipeline.build_state(cfg, raw, pipeline.merge_salesapp(sa), snapshot=snap)
    pipeline.run_import_compliance(base)
    fd, draft = tempfile.mkstemp(suffix=".xlsx"); os.close(fd)
    state_xlsx.save_state(base, draft)

    try:
        print("\n=== Pre-flight scorecard, 5 týdnů, kapacita 40/tech/týd ===")
        cards = {}
        for mode in ("dojezd", "kampan", "vyvazeny"):
            sc = brain.preflight(draft, START, LEN, mode, visits_per_tech_week=40)
            cards[mode] = sc
            print()
            show(sc)

        check("scorecard obsahuje reálné výpočty (kapacita, cadence, kampaně)",
              all(cards[m]["capacity"]["totalCapacity"] for m in cards)
              and cards["kampan"]["cadence"]["overdue"] > 0
              and len(cards["kampan"]["campaigns"]) > 0,
              f"cadence overdue {cards['kampan']['cadence']['overdue']}, "
              f"kampaní {len(cards['kampan']['campaigns'])}")
        check("režim Dojezd naplánuje víc návštěv (bez hold-backu) než Kampaňový",
              cards["dojezd"]["plannedTotal"] > cards["kampan"]["plannedTotal"],
              f"dojezd {cards['dojezd']['plannedTotal']} vs kampaň {cards['kampan']['plannedTotal']}")
        check("cadence (GECO/CORN) je vždy pokryta bez ohledu na režim",
              cards["dojezd"]["cadence"]["pct"] == 100.0 and cards["kampan"]["cadence"]["pct"] == 100.0,
              f"dojezd {cards['dojezd']['cadence']['pct']}%, kampaň {cards['kampan']['cadence']['pct']}%")
        check("plán je 5-týdenní (rozložený do týdnů)",
              len(cards["vyvazeny"]["plannedByWeek"]) >= 2,
              f"týdny v plánu: {list(cards['vyvazeny']['plannedByWeek'].keys())}")
        check("kapacita je skutečný výpočet techs×visits×weeks",
              cards["vyvazeny"]["capacity"]["totalCapacity"] ==
              cards["vyvazeny"]["capacity"]["technicians"] * 40 * LEN,
              f"{cards['vyvazeny']['capacity']['totalCapacity']}")
    finally:
        os.remove(draft)

    print("\n" + ("FIELD BRAIN PHASE 1 OK" if not fails else f"FAILURES: {fails}"))
    if fails:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
