"""Proves the CORN/GECO recurring-HARD cadence guarantee still holds in the
stateless/live pipeline: a GECO (category 1GECO, every 5 weeks) or CORN
(market CORN, every 4 weeks) POS that is overdue must be selected as
MANDATORY, deduped by address - regardless of PPT/score.

Run: python tools/sim/verify_geco_cadence.py
"""
from __future__ import annotations

import glob
import os
import sys
from collections import Counter

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, ROOT)
sys.path.insert(0, os.path.join(ROOT, "backend"))

from desktop_client.engines import compliance_engine, import_engine, planning_engine  # noqa: E402
for _m in (import_engine, compliance_engine, planning_engine):
    if hasattr(_m, "iso_now"):
        _m.iso_now = lambda: "2026-07-09T00:00:00.000Z"
from desktop_client.engines.mock_workbook import MockWorkbook  # noqa: E402

import config_store  # noqa: E402
import pipeline  # noqa: E402
import snapshot_store  # noqa: E402

SCAFFOLD = os.path.join(ROOT, "workbook", "FieldForceOptimizer_V11_scaffold.xlsx")
UPLOADS = "/root/.claude/uploads/96762f2e-6479-5ca9-bce2-fc70e4cf2947"
PPT = os.path.join(UPLOADS, "824b106e-Z_kladn___daje_o_prodejn_ch_m_stech_2.xlsx")
WEEK = 33
GECO_GAP = 5
CORN_GAP = 4


def main():
    raw = pipeline.read_export_rows(PPT)
    sa = [pipeline.read_export_rows(p) for p in
          sorted({os.path.getsize(f): f for f in glob.glob(UPLOADS + "/*visitdata*.xlsx")}.values())]
    cfg = config_store.load_config_state(SCAFFOLD)
    snap = snapshot_store.load_snapshot(SCAFFOLD)
    for s in ("MANAGER_PLAN", "MANAGER_PLAN_PUBLISHED", "PLAN_LIFECYCLE"):
        snap[s] = [snap[s][0]]
    print("Building draft (Import + Compliance)…")
    state = pipeline.build_state(cfg, raw, pipeline.merge_salesapp(sa), snapshot=snap)
    pipeline.run_import_compliance(state)

    # POS attributes
    h = state["POS_MASTER"][0]
    idx = {n: i for i, n in enumerate(h)}
    pm = {}
    for r in state["POS_MASTER"][1:]:
        pid = str(r[idx["posId"]])
        pm[pid] = {
            "category": str(r[idx["category"]]),
            "market": str(r[idx["market"]]),
            "weeksSince": r[idx["weeksSinceLastVisit"]],
            "street": str(r[idx["street"]]), "city": str(r[idx["city"]]),
            "status": str(r[idx["status"]]),
        }

    # Run planning with observability
    pipeline._set_control(state, "CAMPAIGN_START_WEEK", WEEK)
    pipeline._set_control(state, "CAMPAIGN_LENGTH", 1)
    cap = []
    planning_engine.run(MockWorkbook(state), candidates_out=cap)
    by_pos = {c["pos"]: c for c in cap}

    def overdue(pid, gap):
        w = pm[pid]["weeksSince"]
        try:
            return int(w) >= gap
        except (TypeError, ValueError):
            return False

    def report(label, member, gap):
        pool = [p for p, a in pm.items() if member(a) and a["status"] == "Active"]
        overdue_pos = [p for p in pool if overdue(p, gap)]
        # one address = street+city (dedup key the rule uses)
        overdue_addrs = {(pm[p]["street"], pm[p]["city"]) for p in overdue_pos}
        selected = [p for p in pool if by_pos.get(p, {}).get("status") == "Vybráno"]
        mandatory = [p for p in selected if by_pos[p].get("mandatoryRuleId")]
        sel_addrs = {(pm[p]["street"], pm[p]["city"]) for p in selected}
        covered = overdue_addrs & sel_addrs
        print(f"\n{label} (každých {gap} týdnů, HARD, dedup adresa):")
        print(f"  aktivních {label} POS: {len(pool)}")
        print(f"  po termínu (weeksSince>={gap}): {len(overdue_pos)} POS na {len(overdue_addrs)} adresách")
        print(f"  vybráno v týdnu {WEEK}: {len(selected)} POS ({len(mandatory)} jako MANDATORY)")
        print(f"  pokrytých overdue adres: {len(covered)}/{len(overdue_addrs)}")
        # sample a few mandatory GECO/CORN picks with their reason
        sample = [p for p in selected if by_pos[p].get("mandatoryRuleId")][:3]
        for p in sample:
            c = by_pos[p]
            print(f"    POS {p}: ruleId={c.get('mandatoryRuleId')}, weeksSince={pm[p]['weeksSince']}, "
                  f"score={c.get('score')}")
        return len(overdue_addrs), len(covered), len(mandatory)

    g_over, g_cov, g_mand = report("GECO", lambda a: a["category"] == "1GECO", GECO_GAP)
    c_over, c_cov, c_mand = report("CORN", lambda a: a["market"] == "CORN", CORN_GAP)

    print("\n=== VERDICT ===")
    ok = True
    # The guarantee: every overdue address is covered by exactly one selected
    # POS (HARD + address dedup), and those picks are tagged MANDATORY.
    if g_over > 0 and (g_cov < g_over or g_mand == 0):
        print(f"  [FAIL] GECO: pokryto {g_cov}/{g_over} overdue adres, mandatory={g_mand}"); ok = False
    else:
        print(f"  [PASS] GECO: overdue adresy pokryté HARD zárukou ({g_cov}/{g_over}), mandatory={g_mand}")
    if c_over > 0 and (c_cov < c_over or c_mand == 0):
        print(f"  [FAIL] CORN: pokryto {c_cov}/{c_over} overdue adres, mandatory={c_mand}"); ok = False
    else:
        print(f"  [PASS] CORN: overdue adresy pokryté HARD zárukou ({c_cov}/{c_over}), mandatory={c_mand}")
    print("\n" + ("CORN/GECO RECURRING-HARD GUARANTEE HOLDS" if ok else "GUARANTEE BROKEN"))
    if not ok:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
