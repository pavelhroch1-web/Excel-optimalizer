"""
One-off: builds an edge-case seed exercising override paths (FORCE_EXCLUDE,
FORCE_INCLUDE bypassing Filters, managerOverrideTechnician reassignment,
CAPACITY_OVERRIDE) that the real production data doesn't necessarily hit,
to stress-test the desktop_client/engines/ Python port against
office-scripts/PlanningEngine.ts on more than just the happy path.
Usage: python3 tools/sim/make_edge_seed.py tools/sim/post_import_ts.json tools/sim/edge_seed.json
"""
import json
import sys

IDX = {
    "posId": 0, "terminalType": 4, "assignedTechnician": 14, "status": 16,
    "managerOverrideType": 33, "managerOverrideTechnician": 35,
}


def main(in_path, out_path):
    with open(in_path, encoding="utf-8") as f:
        state = json.load(f)

    pm = state["POS_MASTER"]
    active_rows = [i for i in range(1, len(pm)) if pm[i][IDX["status"]] == "Active"]

    # Row A: FORCE_EXCLUDE - must vanish from candidate pool entirely.
    pm[active_rows[0]][IDX["managerOverrideType"]] = "FORCE_EXCLUDE"
    # Row B: FORCE_INCLUDE with a terminalType that fails TERMINAL_RULES -
    # must still appear (Filters bypassed for FORCE_INCLUDE).
    pm[active_rows[1]][IDX["terminalType"]] = "NEVER_ACTIVE_TERMINAL_TYPE_XYZ"
    pm[active_rows[1]][IDX["managerOverrideType"]] = "FORCE_INCLUDE"
    # Row C: managerOverrideTechnician reassignment - must be grouped/planned
    # under the override technician, not assignedTechnician.
    pm[active_rows[2]][IDX["managerOverrideTechnician"]] = "PREPSANY_TECHNIK_TEST"

    # CAPACITY_OVERRIDE: force a tiny capacity for one real technician/week
    # to exercise the capacity<=0 skip path and the mandatory-overflow-
    # beyond-capacity edge in selectWeekPOS.
    control = state["CONTROL"]
    year = next((int(r[1]) for r in control[1:] if str(r[0]).strip().upper() == "YEAR"), 2026)
    start_week = next((int(r[1]) for r in control[1:] if str(r[0]).strip().upper() == "CAMPAIGN_START_WEEK"), 30)
    some_tech = pm[active_rows[3]][IDX["assignedTechnician"]]
    state["CAPACITY_OVERRIDE"] = [
        ["technician", "year", "week", "capacity"],
        [some_tech, year, start_week, 1],
    ]

    with open(out_path, "w", encoding="utf-8") as f:
        json.dump(state, f, ensure_ascii=False)
    print(f"Edge seed written to {out_path}")
    print(f"  FORCE_EXCLUDE posId: {pm[active_rows[0]][0]}")
    print(f"  FORCE_INCLUDE (bad terminal) posId: {pm[active_rows[1]][0]}")
    print(f"  Reassigned posId: {pm[active_rows[2]][0]} -> PREPSANY_TECHNIK_TEST")
    print(f"  Capacity-override tech: {some_tech}, week {start_week}, capacity 1")


if __name__ == "__main__":
    main(sys.argv[1], sys.argv[2])
