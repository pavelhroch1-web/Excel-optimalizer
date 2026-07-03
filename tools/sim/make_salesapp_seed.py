"""
One-off: builds a seed for tools/sim/run_e2e.ts to verify ComplianceEngine.ts's
new "MCHD - Nabeh kampane = Ano" campaign-visit filter against the real
uploaded SalesApp export, with a minimal MANAGER_PLAN_PUBLISHED/POS_MASTER/
CONTROL/PLAN_LIFECYCLE/VISIT_HISTORY_ACTUAL so the engine can run standalone.
"""
import json
import sys
import datetime
import openpyxl


def cell(v):
    if isinstance(v, (datetime.datetime, datetime.date)):
        return {"__date__": v.isoformat()}
    return "" if v is None else v


def main(xlsx_path, out_path):
    wb = openpyxl.load_workbook(xlsx_path, data_only=True)
    ws = wb["Visit Data"]
    rows = [[cell(v) for v in row] for row in ws.iter_rows(values_only=True)]

    state = {
        "SALESAPP_IMPORT": rows,
        "VISIT_HISTORY_ACTUAL": [
            ["posId", "date", "week", "year", "executor", "state", "uid", "matchedStatus"]
        ],
        "MANAGER_PLAN_PUBLISHED": [
            ["WEEK", "DATE", "DAY", "TECHNICIAN", "POS", "KATEGORIE", "NAZEV_PROVOZOVNY",
             "ULICE", "CISLO", "MESTO", "OBLAST", "POS_AREA", "PPT", "LOS_ACTIVITY",
             "LOT_ACTIVITY", "REASON", "GPS_GROUP", "publishedAt"],
            [22, "1. 6. 2026", "MON", "Dummy Tech", "73001577", "9PODNIK", "x", "y", "1", "z",
             "o", "pa", 1, "Gems", "Sportka", "", 1, "2026-06-01T00:00:00.000Z"],
        ],
        "POS_MASTER": [
            ["posId", "terminalId", "market", "category", "terminalType", "classification",
             "nazev", "area", "posArea", "street", "houseNumber", "city", "gpsX", "gpsY",
             "assignedTechnician", "ppt", "status", "closedSinceWeek", "closedSinceYear",
             "currentLosActivity", "currentLotActivity", "targetLosActivity", "targetLotActivity",
             "lastRealVisitDate", "lastRealVisitWeek", "lastPlannedVisitDate",
             "weeksSinceLastVisit", "visitCountThisCampaign", "businessScore",
             "plannerStatus", "assignedWeek", "assignedDay", "gpsGroup",
             "managerOverrideType", "managerOverridePriority", "managerOverrideTechnician",
             "plannerNotes", "importedAt", "updatedAt"],
            ["73001577", "1", "m", "9PODNIK", "t", "A", "x", "a", "pa", "s", "1", "c", 0, 0,
             "Dummy Tech", 1, "Active", "", "", "", "", "", "", "", "", "", "", "", "",
             "", "", "", "", "", "", "", "", "", ""],
        ],
        "CONTROL": [["key", "value"], ["YEAR", 2026], ["COMPLIANCE_LATE_CUTOFF_WEEKS", 1]],
        "PLAN_LIFECYCLE": [["year", "week", "status", "publishedAt", "closedAt"],
                           [2026, 22, "Published", "2026-06-01T00:00:00.000Z", ""]],
        "COMPLIANCE_LOG": [["posId", "technician", "week", "year", "status", "date", "actualWeek", "evaluatedAt"]],
        "ADVISOR_LOG": [["type", "severity", "subject", "message", "createdAt"]],
    }

    with open(out_path, "w", encoding="utf-8") as f:
        json.dump(state, f, ensure_ascii=False)
    print(f"Seed written to {out_path}: {len(rows) - 1} SalesApp visit rows")


if __name__ == "__main__":
    main(sys.argv[1], sys.argv[2])
