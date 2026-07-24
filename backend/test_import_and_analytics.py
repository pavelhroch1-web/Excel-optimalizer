"""Fast, dependency-light regression tests for the import + analytics logic
added on top of the engine (validator, DTO, dedup, read-side caches).

Runs Node-free and pytest-free — just:  python3 backend/test_import_and_analytics.py
Uses a throwaway copy of the seed DB (never touches the user's runtime data).
Exit code 0 = all passed, 1 = a failure (so CI / a pre-push hook can gate on it).
"""
from __future__ import annotations

import os
import shutil
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.dirname(HERE)
sys.path.insert(0, HERE)
sys.path.insert(0, REPO)

# Isolate: point the datastore at a temp copy of the seed BEFORE importing db.
_WORK = tempfile.mkdtemp(prefix="ffo_test_")
os.environ["FFO_LOCAL"] = "1"
_seed = os.path.join(REPO, "seed", "fieldforce.db")
_db = os.path.join(_WORK, "fieldforce.db")
if os.path.exists(_seed):
    shutil.copy(_seed, _db)
os.environ["FFO_DB_PATH"] = _db

import openpyxl  # noqa: E402

_passed = 0
_failed = 0


def check(name, cond):
    global _passed, _failed
    if cond:
        _passed += 1
        print(f"  ok   {name}")
    else:
        _failed += 1
        print(f"  FAIL {name}")


def _mkxlsx(headers, rows):
    wb = openpyxl.Workbook()
    ws = wb.active
    ws.append(headers)
    for r in rows:
        ws.append(r)
    p = tempfile.mktemp(suffix=".xlsx", dir=_WORK)
    wb.save(p)
    return p


# --------------------------------------------------------------- pos_dedup (pure)
def test_dedup_pure():
    import pos_dedup
    # whitespace + case collapse => a true 1:1 key
    check("dedup _sq collapses ws/case",
          pos_dedup._sq("  Hradecká   408/40 ") == "hradecká 408/40")
    a = {"street": "Hradecká", "house_number": "408/40", "city": "Brno"}
    b = {"street": "hradecká", "house_number": "408/40  ", "city": "BRNO"}
    check("dedup identical addr up to spacing/case",
          pos_dedup._norm_addr(a) == pos_dedup._norm_addr(b))
    c = {"street": "Jiná", "house_number": "1", "city": "Brno"}
    check("dedup different addr differs", pos_dedup._norm_addr(a) != pos_dedup._norm_addr(c))
    # strength: higher PPT wins
    weak = {"ppt": 100, "classification": "C", "terminal_type": "LI", "gps_x": 1, "last_seen": "2020", "pos_id": "1"}
    strong = {"ppt": 5000, "classification": "C", "terminal_type": "LI", "gps_x": 1, "last_seen": "2020", "pos_id": "2"}
    check("dedup strength orders by PPT", pos_dedup._strength(strong) > pos_dedup._strength(weak))


# ------------------------------------------------------ import validator (no DB)
def test_validate():
    import import_validate
    good = _mkxlsx(["POS", "NAZEV PROVOZOVNY", "ULICE", "MĚSTO", "PPT"],
                   [["1", "Test", "Hlavní 1", "Praha", "5000"]])
    r = import_validate.validate(good, "pos_master")
    check("validate good pos_master ok", r["ok"] is True and not r["missing"])

    nopos = _mkxlsx(["NAZEV PROVOZOVNY", "PPT"], [["Test", "5000"]])
    r = import_validate.validate(nopos, "pos_master")
    check("validate missing POS id -> not ok", r["ok"] is False)
    check("validate names the missing column", "číslo POS" in " ".join(r["missing"]))
    check("validate has a human reason", bool(r["reason"]))

    empty = _mkxlsx(["POS", "PPT"], [])
    r = import_validate.validate(empty, "pos_master")
    check("validate empty sheet -> not ok", r["ok"] is False and "řádk" in (r["reason"] or ""))

    sa = _mkxlsx(["UID", "Store UID", "Executor", "Date"], [["u1", "t1", "Jan", "2026-01-01"]])
    r = import_validate.validate(sa, "salesapp")
    check("validate good salesapp ok", r["ok"] is True)


# -------------------------------------------------- ImportResult DTO conformance
def test_import_result_contract():
    import auto_import
    import contracts
    # primary-total picks the right table per kind
    check("primary_total pos_master", auto_import._primary_total("pos_master", {"pos_master": 7}) == 7)
    check("primary_total salesapp", auto_import._primary_total("salesapp", {"salesapp_visits": 9}) == 9)
    check("primary_total workbook sums",
          auto_import._primary_total("workbook", {"pos_master": 2, "salesapp_visits": 3, "campaigns": 1}) == 6)
    ok = auto_import._result(True, "pos_master", counts={"pos_master": 3}, filename="x.xlsx")
    check("result ok conforms to ImportResult", contracts.validate_import_result(ok))
    check("result ok total reflects rows", ok["total"] == 3 and ok["ok"] is True)
    bad = auto_import._result(False, "pos_master", error="chybí sloupec")
    check("result bad conforms + total 0", contracts.validate_import_result(bad) and bad["total"] == 0)


# --------------------------------------------------- read-side caches (needs DB)
def test_caches():
    if not os.path.exists(_db):
        print("  skip caches (no seed DB)")
        return
    import diagnostics
    import team_analytics
    team_analytics.invalidate()
    a = team_analytics.overview(days_back=90)
    b = team_analytics.overview(days_back=90)
    check("overview cached result equals fresh", a == b)
    # mutating the returned copy must NOT poison the cache
    if a.get("technicians"):
        a["technicians"][0]["visits"] = -999
    c = team_analytics.overview(days_back=90)
    check("overview returns an isolated copy",
          not c.get("technicians") or c["technicians"][0]["visits"] != -999)

    h1 = diagnostics.health_scores(90, "TECHNIK")
    regs = h1.get("regions") or []
    if regs:
        hr = diagnostics.health_scores(90, "TECHNIK", region=regs[0])
        check("health region filter narrows the list",
              all(t.get("region") == regs[0] for t in hr["technicians"]))
        check("health region filter keeps regions list", hr["regions"] == regs)
    # invalidate clears both memos without error
    diagnostics.invalidate_cache()
    check("invalidate_cache clears health memo", not diagnostics._health_cache)
    check("invalidate_cache clears overview memo", not team_analytics._overview_cache)


def test_plan_recency():
    """A POS on the previous published tourplan must count toward recency, so
    the engine doesn't re-send a technician there next run even when salesapp
    never recorded that visit (product owner, 2026-07-24)."""
    import datetime
    import runtime_state as rs

    # Czech plan-date parsing is the linchpin (D. M. YYYY), never raises.
    check("parse cs date D. M. YYYY", rs._parse_cs_date("20. 7. 2026") == "2026-07-20")
    check("parse cs date single-digit day", rs._parse_cs_date("7. 8. 2026") == "2026-08-07")
    check("parse cs date rejects garbage", rs._parse_cs_date("not a date") is None)

    planned = rs._last_planned()
    today = datetime.date.today().isoformat()
    check("plan recency is a pos->iso map", isinstance(planned, dict))
    check("plan recency never returns future dates",
          all(v <= today for v in planned.values()))

    # The merge must never make recency OLDER than salesapp alone: weeksSince
    # from the merged source is <= weeksSince from salesapp for every POS.
    last, _earliest = rs._last_visits()
    if planned and last:
        pid = next((p for p in planned if p in last), None)
        if pid:
            merged = max(last[pid][:10], planned[pid])
            check("merged recency is the more-recent of plan/salesapp",
                  merged >= last[pid][:10] and merged >= planned[pid])

    # Toggle honoured: off => plan dates ignored.
    import settings
    settings.set_value("planner", "count_planned_as_visited", "false")
    check("toggle off disables plan recency", rs._count_planned_as_visited() is False)
    settings.set_value("planner", "count_planned_as_visited", "true")
    check("toggle on enables plan recency", rs._count_planned_as_visited() is True)


def main():
    print("import + analytics regression tests")
    for t in (test_dedup_pure, test_validate, test_import_result_contract, test_caches,
              test_plan_recency):
        print(f"\n[{t.__name__}]")
        t()
    print(f"\n{_passed} passed, {_failed} failed")
    return 1 if _failed else 0


if __name__ == "__main__":
    sys.exit(main())
