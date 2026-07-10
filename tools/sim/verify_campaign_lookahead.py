"""Feasibility check for the manager's case study, using ONLY the existing
engine mechanisms (config/ACTIVITY_PLAN variations - no engine change):

  A) "Dojezdový" week (week 29): with no campaign in the look-ahead horizon,
     the engine's selection is driven by neglect (weeksSinceLastVisit). Raising
     capacity lets it clean up MORE long-neglected POS.

  B) Look-ahead campaign protection (week 30 protecting week-31 campaign):
     adding a campaign that STARTS at week 31 makes the engine DEFER (Odloženo)
     non-mandatory POS in week 30 that it would otherwise visit - i.e. it saves
     them for the campaign - purely via Smart Hold-back over ACTIVITY_PLAN.
     Those same POS are then available in the campaign week.

  Generality: the behaviour is driven by the campaign START WEEK in
  ACTIVITY_PLAN + config, never by hard-coded week numbers - proven by running
  the SAME logic for two different (week, campaignStart) pairs.

Run: python tools/sim/verify_campaign_lookahead.py
"""
from __future__ import annotations

import copy
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
fails = []


def check(name, ok, detail):
    print(f"  [{'PASS' if ok else 'FAIL'}] {name}: {detail}")
    if not ok:
        fails.append(name)


def run_statuses(base, week, campaign_start=None, target_week=None):
    """Run the engine for `week` with ACTIVITY_PLAN carrying a single campaign
    window starting at campaign_start (or none), optional flat weekly capacity.
    Returns {pos: {status, weeksSince, mandatory}}."""
    st = copy.deepcopy(base)
    aps = st["ACTIVITY_PLAN"]
    h = [str(x) for x in aps[0]]
    sc, ec = h.index("START_WEEK"), h.index("END_WEEK")
    for row in aps[1:]:
        if campaign_start is None:
            row[sc], row[ec] = "", ""
        else:
            row[sc], row[ec] = campaign_start, campaign_start + 2
    pipeline._set_control(st, "CAMPAIGN_START_WEEK", week)
    pipeline._set_control(st, "CAMPAIGN_LENGTH", 1)
    if target_week is not None:
        pipeline._set_control(st, "TARGET_VISITS_WEEK", target_week)
    cap = []
    planning_engine.run(MockWorkbook(st), candidates_out=cap)
    return {c["pos"]: {"status": c["status"], "ws": c["weeksSinceLastVisit"],
                       "mand": c.get("mandatoryRuleId")} for c in cap}


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

    def selected_nonmand(m):
        return {p for p, v in m.items() if v["status"] == "Vybráno" and not v["mand"]}
    def held(m):
        return {p for p, v in m.items() if str(v["status"]).startswith("Odloženo")}

    # ---- A) cleanup week 29: no campaign, neglect-driven, capacity scales it
    print("\nA) Dojezdový týden 29 (bez kampaně)")
    w29 = run_statuses(base, 29, campaign_start=None)
    sel = [p for p, v in w29.items() if v["status"] == "Vybráno"]
    ws_vals = [w29[p]["ws"] for p in sel if isinstance(w29[p]["ws"], (int, float))]
    avg_ws = round(sum(ws_vals) / len(ws_vals), 1) if ws_vals else 0
    print(f"   vybráno {len(sel)} POS, průměr weeksSinceLastVisit = {avg_ws}")
    # default weekly capacity here is 8/day * 5 days = 40/tech; raise it well
    # above that to show cleanup coverage scales with capacity.
    w29_big = run_statuses(base, 29, campaign_start=None, target_week=80)
    sel_big = sum(1 for v in w29_big.values() if v["status"] == "Vybráno")
    check("vyšší kapacita dočistí víc POS (dojezd škáluje s kapacitou)", sel_big > len(sel),
          f"kapacita 40/týd → {len(sel)} vybráno; kapacita 80/týd → {sel_big} vybráno")

    # ---- B) week 30 protecting a week-31 campaign
    print("\nB) Týden 30 chrání kampaň v týdnu 31")
    w30_nocamp = run_statuses(base, 30, campaign_start=None)
    w30_camp = run_statuses(base, 30, campaign_start=31)
    protected = selected_nonmand(w30_nocamp) & held(w30_camp)
    print(f"   bez kampaně: {len(selected_nonmand(w30_nocamp))} nemandatorních vybráno, "
          f"{len(held(w30_nocamp))} odloženo")
    print(f"   kampaň od 31: {len(selected_nonmand(w30_camp))} nemandatorních vybráno, "
          f"{len(held(w30_camp))} odloženo")
    check("kampaň v t31 ochrání (odloží) POS, které by t30 jinak spotřeboval",
          len(protected) > 0,
          f"{len(protected)} POS přesunuto z „vybráno v t30“ na „odloženo“ (šetří se pro kampaň)")

    # those protected POS are available again in the campaign week 31
    if protected:
        w31 = run_statuses(base, 31, campaign_start=31)
        back = {p for p in protected if w31.get(p, {}).get("status") in ("Vybráno", "Nevybráno")}
        check("chráněné POS jsou v kampaňovém týdnu 31 opět dostupné",
              len(back) > 0, f"{len(back)}/{len(protected)} chráněných POS je v t31 znovu kandidátem")

    # ---- Generality: same mechanism, different weeks (no hard-coded 29-31)
    print("\nC) Obecnost – stejný mechanismus pro jiné týdny (kampaň od 45)")
    w44_nocamp = run_statuses(base, 44, campaign_start=None)
    w44_camp = run_statuses(base, 44, campaign_start=45)
    protected2 = selected_nonmand(w44_nocamp) & held(w44_camp)
    check("ochrana funguje i pro týden 44→45 (žádné napevno zadané týdny)",
          len(protected2) > 0,
          f"{len(protected2)} POS chráněno pro kampaň v t45")

    print("\n" + ("CASE STUDY FEASIBLE with existing mechanisms" if not fails else f"FAILURES: {fails}"))
    if fails:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
