"""Verifies the POS Detail read-out: (a) the new rejected_out hook does NOT
change the engine's MANAGER_PLAN output, (b) filtered-out POS are captured
with the correct reason, and (c) candidates.pos_detail returns the right
diagnostic for a selected, a not-selected, and a rejected POS.

Run: python tools/sim/verify_pos_detail.py
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

    # (a) byte-identical MANAGER_PLAN with rejected_out off vs on
    a = copy.deepcopy(base)
    pipeline._set_control(a, "CAMPAIGN_START_WEEK", WEEK); pipeline._set_control(a, "CAMPAIGN_LENGTH", 1)
    wb_a = MockWorkbook(a)
    planning_engine.run(wb_a)
    mp_none = wb_a.dump()["MANAGER_PLAN"]

    b = copy.deepcopy(base)
    pipeline._set_control(b, "CAMPAIGN_START_WEEK", WEEK); pipeline._set_control(b, "CAMPAIGN_LENGTH", 1)
    wb_b = MockWorkbook(b)
    cap, rej = [], []
    planning_engine.run(wb_b, candidates_out=cap, rejected_out=rej)
    mp_hooks = wb_b.dump()["MANAGER_PLAN"]
    check("MANAGER_PLAN nezměněn hooky (byte-identické, neprázdný plán)",
          mp_none == mp_hooks and len(mp_none) > 1,
          f"{len(mp_none) - 1} vs {len(mp_hooks) - 1} řádků")

    # (b) rejections captured with reasons
    from collections import Counter
    reasons = Counter(r["rejectReason"] for r in rej)
    check("zachyceny vyřazené POS s důvodem", len(rej) > 0,
          f"{len(rej)} vyřazených; top důvody: {dict(reasons.most_common(4))}")
    has_reason_kinds = any("terminál" in r or "partner" in r or "EXCLUDE" in r or "blacklist" in r.lower()
                           or "Neaktivní" in r for r in reasons)
    check("důvody odpovídají Excel filtrům", has_reason_kinds, f"{list(reasons)[:5]}")

    # (c) pos_detail for selected / not-selected / rejected
    fd, draft = tempfile.mkstemp(suffix=".xlsx"); os.close(fd)
    state_xlsx.save_state(base, draft)
    try:
        selected = next((c["pos"] for c in cap if c["status"] == "Vybráno"), None)
        not_sel = next((c["pos"] for c in cap if c["status"] == "Nevybráno"), None)
        rejected_pos = rej[0]["pos"] if rej else None

        d = candidates_mod.pos_detail(draft, selected, WEEK)
        ok = (d["found"] and d["isCandidate"] and d["score"] is not None
              and "baseScore" in d and d.get("lastCompliance") is not None or True)
        check("detail vybraného POS má skóre + rozpad + vysvětlení",
              d["found"] and d["isCandidate"] and d["score"] is not None and d.get("explanation"),
              f"POS {selected}: score={d.get('score')}, expl='{d.get('explanation','')[:40]}', "
              f"kampaní={len(d.get('activeCampaigns', []))}, historie={len(d.get('visitHistory', []))}")

        if not_sel:
            d2 = candidates_mod.pos_detail(draft, not_sel, WEEK)
            check("detail nevybraného POS má důvod",
                  d2["found"] and d2["isCandidate"] and d2.get("explanation"),
                  f"POS {not_sel}: '{d2.get('explanation','')[:60]}'")

        if rejected_pos:
            d3 = candidates_mod.pos_detail(draft, rejected_pos, WEEK)
            check("detail vyřazeného POS ukáže přesný důvod",
                  d3["found"] and not d3["isCandidate"] and "Není kandidát" in d3.get("explanation", ""),
                  f"POS {rejected_pos}: '{d3.get('explanation','')[:60]}'")
    finally:
        os.remove(draft)

    print("\n" + ("POS DETAIL OK" if not fails else f"FAILURES: {fails}"))
    if fails:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
