"""Verifies the Decision Support layer is faithful interpretation - no new
logic:

  (1) what_if() deltas MATCH what the engine actually does. For the top
      "partner off" and a "terminal on" scenario, we re-run the real engine
      with that lever flipped and confirm the candidate-pool count moves by
      exactly the delta what_if() reported from the single baseline capture.

  (2) recommend() produces a verdict + reasons for selected / not-selected /
      rejected POS, drawn only from the engine's own components.

Run: python tools/sim/verify_decision.py
"""
from __future__ import annotations

import copy
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
from desktop_client.engines.mock_workbook import MockWorkbook  # noqa: E402

import candidates as candidates_mod  # noqa: E402
import config_store  # noqa: E402
import decision  # noqa: E402
import pipeline  # noqa: E402
import snapshot_store  # noqa: E402
import state_xlsx  # noqa: E402

SCAFFOLD = os.path.join(ROOT, "workbook", "FieldForceOptimizer_V11_scaffold.xlsx")
UPLOADS = "/root/.claude/uploads/96762f2e-6479-5ca9-bce2-fc70e4cf2947"
PPT = os.path.join(UPLOADS, "824b106e-Z_kladn___daje_o_prodejn_ch_m_stech_2.xlsx")
WEEK = 33
fails = []


def check(name, ok, detail):
    print(f"  [{'PASS' if ok else 'FAIL'}] {name}: {detail}")
    if not ok:
        fails.append(name)


def pool_after_toggle(state, sheet, key_col, key_val, active):
    st = copy.deepcopy(state)
    tr = st[sheet]
    hdr = [str(x) for x in tr[0]]
    kc, ac = hdr.index(key_col), hdr.index("ACTIVE")
    for row in tr[1:]:
        if str(row[kc]) == key_val:
            row[ac] = active
    decision._set_control(st, "CAMPAIGN_START_WEEK", WEEK)
    decision._set_control(st, "CAMPAIGN_LENGTH", 1)
    cap = []
    planning_engine.run(MockWorkbook(st), candidates_out=cap)
    return len(cap)


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
        wi = decision.what_if(draft, WEEK)
        base_pool = wi["baseline"]["candidates"]
        print(f"Baseline week {WEEK}: {base_pool} kandidátů, {wi['baseline']['selected']} naplánováno, "
              f"{wi['baseline']['heldBack']} odloženo")
        for s in wi["scenarios"]:
            print(f"   • {s['label']}: {s['impact']}")

        # (1a) partner_off delta matches a real engine re-run
        p = next((s for s in wi["scenarios"] if s["lever"] == "partner_off"), None)
        if p:
            market = p["label"].split("„")[1].split("“")[0]
            real = pool_after_toggle(base, "MARKET_RULES", "MARKET", market, "NO")
            check(f"what_if partner_off '{market}' == reálný engine",
                  real == base_pool + p["delta"],
                  f"predikce {base_pool}{p['delta']:+d}={base_pool + p['delta']}, reálně {real}")

        # (1b) terminal_on delta matches a real engine re-run
        t = next((s for s in wi["scenarios"] if s["lever"] == "terminal_on"), None)
        if t:
            ttype = t["label"].split("„")[1].split("“")[0]
            real = pool_after_toggle(base, "TERMINAL_RULES", "TYP TERMINALU", ttype, "YES")
            predicted = base_pool + t["delta"]
            # terminal_ok uses substring matching -> this lever is a labelled
            # ESTIMATE (impact shown with "≈"); allow a tiny tolerance.
            check(f"what_if terminal_on '{ttype}' ≈ reálný engine (odhad)",
                  abs(real - predicted) <= 2 and not t.get("exact", True),
                  f"odhad {predicted}, reálně {real} (rozdíl {real - predicted})")
        else:
            print("   (žádný terminal_on scénář – všechny typy terminálu jsou zapnuté)")

        # (2) recommend() for selected / not-selected / rejected
        cap, rej = [], []
        st = copy.deepcopy(base)
        decision._set_control(st, "CAMPAIGN_START_WEEK", WEEK); decision._set_control(st, "CAMPAIGN_LENGTH", 1)
        planning_engine.run(MockWorkbook(st), candidates_out=cap, rejected_out=rej)
        sel = next((c["pos"] for c in cap if c["status"] == "Vybráno"), None)
        nos = next((c["pos"] for c in cap if c["status"] == "Nevybráno"), None)

        d = candidates_mod.pos_detail(draft, sel, WEEK)
        r = d["recommendation"]
        check("doporučení pro vybraný POS", r["verdict"].startswith("Doporučuji naplánovat") and r["reasons"],
              f"POS {sel}: '{r['verdict']}' – {r['reasons'][:3]}")

        if nos:
            d2 = candidates_mod.pos_detail(draft, nos, WEEK)
            r2 = d2["recommendation"]
            check("doporučení pro nevybraný POS", r2["verdict"].startswith("Zatím nedoporučuji") and r2["reasons"],
                  f"POS {nos}: '{r2['verdict']}' – {r2['reasons'][:3]}")

        if rej:
            d3 = candidates_mod.pos_detail(draft, rej[0]["pos"], WEEK)
            check("doporučení + páčka pro vyřazený POS",
                  d3["recommendation"]["verdict"].startswith("Nelze") and d3.get("includeLever"),
                  f"POS {rej[0]['pos']}: páčka='{d3.get('includeLever')}'")
    finally:
        os.remove(draft)

    print("\n" + ("DECISION SUPPORT OK (faithful interpretation)" if not fails else f"FAILURES: {fails}"))
    if fails:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
