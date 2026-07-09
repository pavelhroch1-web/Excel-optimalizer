"""Proves every planning parameter the manager sets (terminal type, partner/
market, category rule, campaign/Activity Plan) genuinely constrains the
Planning Engine's POS selection - not just the on-screen view.

Builds the draft ONCE (Import+Compliance are the slow part), then re-runs
only Planning under each rule change against a deep copy, and checks the
selected POS set actually changes the way the rule dictates.

Run: python tools/sim/verify_planning_filters.py
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

import config_store  # noqa: E402
import pipeline  # noqa: E402
import snapshot_store  # noqa: E402

SCAFFOLD = os.path.join(ROOT, "workbook", "FieldForceOptimizer_V11_scaffold.xlsx")
UPLOADS = "/root/.claude/uploads/96762f2e-6479-5ca9-bce2-fc70e4cf2947"
PPT = os.path.join(UPLOADS, "824b106e-Z_kladn___daje_o_prodejn_ch_m_stech_2.xlsx")
WEEK = 33
_fails: list[str] = []


def _salesapp():
    return sorted({os.path.getsize(f): f for f in glob.glob(UPLOADS + "/*visitdata*.xlsx")}.values())


def build_base_state() -> dict:
    raw = pipeline.read_export_rows(PPT)
    sa = [pipeline.read_export_rows(p) for p in _salesapp()]
    cfg = config_store.load_config_state(SCAFFOLD)
    snap = snapshot_store.load_snapshot(SCAFFOLD)
    for s in ("MANAGER_PLAN", "MANAGER_PLAN_PUBLISHED", "PLAN_LIFECYCLE"):
        snap[s] = [snap[s][0]]  # start with no published plan so WEEK plans fresh
    state = pipeline.build_state(cfg, raw, pipeline.merge_salesapp(sa), snapshot=snap)
    pipeline.run_import_compliance(state)
    return state


def pos_attr_maps(state: dict):
    h = state["POS_MASTER"][0]
    pi, mi, ci, ti = (h.index("posId"), h.index("market"),
                      h.index("category"), h.index("terminalType"))
    market, category, terminal = {}, {}, {}
    for r in state["POS_MASTER"][1:]:
        market[str(r[pi])] = r[mi]
        category[str(r[pi])] = r[ci]
        terminal[str(r[pi])] = r[ti]
    return market, category, terminal


def planned_pos(state: dict) -> set:
    mp = state["MANAGER_PLAN"]
    pc = mp[0].index("POS")
    return {str(r[pc]) for r in mp[1:] if r[pc] not in (None, "")}


def set_toggle(state, sheet, key_col, key_pred, active_val):
    tr = state[sheet]
    hdr = [str(x) for x in tr[0]]
    kc, ac = hdr.index(key_col), hdr.index("ACTIVE")
    changed = []
    for row in tr[1:]:
        if key_pred(str(row[kc])):
            row[ac] = active_val
            changed.append(row[kc])
    return changed


def set_category_rule(state, category_value, rule_val):
    cr = state["CATEGORY_RULES"]
    hdr = [str(x) for x in cr[0]]
    kc, rc = hdr.index("CATEGORY"), hdr.index("RULE")
    for row in cr[1:]:
        if str(row[kc]) == str(category_value):
            row[rc] = rule_val
            return True
    return False


def check(name, ok, detail):
    print(f"  [{'PASS' if ok else 'FAIL'}] {name}: {detail}")
    if not ok:
        _fails.append(name)


def main():
    print("Building draft (Import + Compliance, once)…")
    base = build_base_state()
    market, category, terminal = pos_attr_maps(base)

    b = copy.deepcopy(base)
    pipeline.run_planning(b, WEEK, 1)
    base_pos = planned_pos(b)
    print(f"Baseline week {WEEK}: {len(base_pos)} POS selected")
    print("  markets:", dict(Counter(market[p] for p in base_pos)))
    print("  categories:", dict(Counter(category[p] for p in base_pos)))
    print("  terminals:", dict(Counter(terminal[p] for p in base_pos)))
    if not base_pos:
        raise SystemExit("baseline empty - cannot test filters")

    # ---- 1) TERMINAL TYPE ----
    print("\n1) Terminal type")
    top_term = Counter(terminal[p] for p in base_pos).most_common(1)[0][0]
    s = copy.deepcopy(base)
    set_toggle(s, "TERMINAL_RULES", "TYP TERMINALU", lambda v: v == top_term, "NO")
    pipeline.run_planning(s, WEEK, 1)
    after = planned_pos(s)
    removed = {p for p in base_pos if terminal[p] == top_term}
    check(f"vypnutí terminálu '{top_term}' odebere jeho POS",
          all(p not in after for p in removed) and len(after) < len(base_pos),
          f"{len(base_pos)} -> {len(after)} POS (odebráno {len(removed)} typu '{top_term}')")

    # ---- 2) PARTNER / MARKET ----
    print("2) Partner / market")
    top_market = Counter(market[p] for p in base_pos).most_common(1)[0][0]
    s = copy.deepcopy(base)
    set_toggle(s, "MARKET_RULES", "MARKET", lambda v: v == top_market, "NO")
    pipeline.run_planning(s, WEEK, 1)
    after = planned_pos(s)
    still = [p for p in after if market[p] == top_market]
    check(f"vypnutí partnera '{top_market}' odebere jeho POS",
          not still and len(after) < len(base_pos),
          f"{len(base_pos)} -> {len(after)} POS (v plánu zbylo z '{top_market}': {len(still)})")

    # ---- 3) CATEGORY RULE ----
    # Category matching: an exact CATEGORY_RULES key wins; else STARTS_1
    # applies to every category beginning "1" (in this data it is CORE, which
    # is why the 1* categories dominate the plan); else the "*" default. So
    # STARTS_1 is the dominant category knob - flipping it to EXCLUDE must
    # drop those POS from the plan.
    print("3) Category rule (STARTS_1 -> EXCLUDE)")
    one_star_pos = {p for p in base_pos if str(category[p]).startswith("1")}
    s = copy.deepcopy(base)
    ok_set = set_category_rule(s, "STARTS_1", "EXCLUDE")
    pipeline.run_planning(s, WEEK, 1)
    after = planned_pos(s)
    still_1 = [p for p in after if p in one_star_pos]
    check("EXCLUDE 1* kategorií (STARTS_1) odebere jejich POS",
          ok_set and not still_1 and len(after) < len(base_pos),
          f"{len(base_pos)} -> {len(after)} POS (1* v plánu: {len(one_star_pos)} -> {len(still_1)})")

    # ---- 4) CAMPAIGNS / ACTIVITY PLAN (Smart Hold-back) ----
    # ACTIVITY_PLAN drives Smart Hold-back: when a campaign STARTS within the
    # look-ahead window, an A-class POS that still has slack to its deadline is
    # deferred (status "Odloženo") instead of visited now. So an imminent
    # campaign must change the engine's Odloženo/selected decisions.
    print("4) Campaigns (Activity Plan -> Smart Hold-back)")

    def statuses(state, campaign_start=None):
        st = copy.deepcopy(state)
        aps = st["ACTIVITY_PLAN"]
        h2 = [str(x) for x in aps[0]]
        sc, ec = h2.index("START_WEEK"), h2.index("END_WEEK")
        for row in aps[1:]:
            if campaign_start is None:
                row[sc], row[ec] = "", ""            # no campaigns at all
            else:
                row[sc], row[ec] = campaign_start, campaign_start + 1
        pipeline._set_control(st, "CAMPAIGN_START_WEEK", WEEK)
        pipeline._set_control(st, "CAMPAIGN_LENGTH", 1)
        from desktop_client.engines.mock_workbook import MockWorkbook
        cap = []
        planning_engine.run(MockWorkbook(st), candidates_out=cap)
        held = sum(1 for c in cap if str(c["status"]).startswith("Odloženo"))
        sel = {c["pos"] for c in cap if c["status"] == "Vybráno"}
        return held, sel

    held_none, sel_none = statuses(base, campaign_start=None)
    held_soon, sel_soon = statuses(base, campaign_start=WEEK + 1)
    print(f"   bez kampaní: Odloženo={held_none}, vybráno={len(sel_none)}")
    print(f"   kampaň od týdne {WEEK + 1}: Odloženo={held_soon}, vybráno={len(sel_soon)}")
    check("blížící se kampaň mění rozhodnutí enginu (Hold-back)",
          held_soon != held_none or sel_soon != sel_none,
          f"Odloženo {held_none} -> {held_soon}, vybraných {len(sel_none)} -> {len(sel_soon)}")

    print("\n" + ("ALL FILTERS CONSTRAIN SELECTION" if not _fails else f"FAILURES: {_fails}"))
    if _fails:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
