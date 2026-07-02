"""
Builds the V11 scaffold workbook.

Copies the production-data sheets from the reference V10.5.5 workbook unchanged
(RAW_DATA, CONTROL, ACTIVITY_PLAN, CATEGORY_RULES, TERMINAL_RULES, VISIT_HISTORY),
adds an explicit default row to CATEGORY_RULES, and adds the new V11 sheets
(POS_MASTER, config tables, import staging) as headers-only or seeded with values
that are already confirmed in docs/BUSINESS_RULES.md. Anything not yet confirmed
is seeded as an inactive TODO row, never silently activated.

Usage: python3 tools/scaffold_workbook.py <path-to-reference-workbook.xlsx> <output-path.xlsx>
"""
import sys
import openpyxl
from openpyxl.utils import get_column_letter

REFERENCE_SHEETS_KEEP_AS_IS = [
    "RAW_DATA", "CONTROL", "ACTIVITY_PLAN", "TERMINAL_RULES", "VISIT_HISTORY",
]


def copy_sheet(src_wb, dst_wb, name):
    src = src_wb[name]
    dst = dst_wb.create_sheet(name)
    for row in src.iter_rows(values_only=False):
        for cell in row:
            dst.cell(row=cell.row, column=cell.column, value=cell.value)
    return dst


def write_table(wb, name, headers, rows):
    ws = wb.create_sheet(name)
    ws.append(headers)
    for r in rows:
        ws.append(r)
    for i, h in enumerate(headers, start=1):
        ws.column_dimensions[get_column_letter(i)].width = max(12, len(str(h)) + 2)
    return ws


def main(ref_path, out_path):
    src_wb = openpyxl.load_workbook(ref_path, data_only=True)
    dst_wb = openpyxl.Workbook()
    dst_wb.remove(dst_wb.active)  # drop default empty sheet

    for name in REFERENCE_SHEETS_KEEP_AS_IS:
        copy_sheet(src_wb, dst_wb, name)

    # Add GPS bonus config keys to CONTROL (docs/BUSINESS_RULES.md 6a - corrected
    # spec: bounded, capacity-aware overflow, not a hard cap at capacity).
    control_ws = dst_wb["CONTROL"]
    control_ws.append(["GPS_EXTRA_ENABLED", 1, "1=on. Allows a small overflow beyond capacity for POS very close to an already-selected visit."])
    control_ws.append(["GPS_EXTRA_RADIUS_METERS", 300, "Radius for the GPS bonus overflow rule."])
    control_ws.append(["GPS_EXTRA_MAX_VISITS", 5, "Max extra visits per technician/week from the GPS bonus rule."])
    control_ws.append(["COMPLIANCE_LATE_CUTOFF_WEEKS", 1, "Weeks past the planned week before a still-unrealized visit becomes Nesplneno instead of Pending. Proposed default per docs/BUSINESS_RULES.md section 12, not yet formally reconfirmed."])
    control_ws.append(["ADVISOR_NEGLECT_WARNING_RATIO_PERCENT", 80, "Proposed default (not a confirmed business rule): WARNING neglect alert fires at this % of NEGLECTED_AFTER_WEEKS, CRITICAL fires at 100%. Tune on real data."])
    control_ws.append(["ADVISOR_TREND_WINDOW_WEEKS", 4, "Proposed default: how many recent weeks of COMPLIANCE_LOG feed the technician/region overload alerts. Tune on real data."])
    control_ws.append(["ADVISOR_OVERLOAD_WARNING_RATE_PERCENT", 20, "Proposed default (not a confirmed business rule): technician/region Nesplneno rate over the trend window that triggers a WARNING. Tune on real data."])
    control_ws.append(["ADVISOR_OVERLOAD_CRITICAL_RATE_PERCENT", 35, "Proposed default (not a confirmed business rule): technician/region Nesplneno rate over the trend window that triggers a CRITICAL alert. Tune on real data."])

    # CATEGORY_RULES: copy reference rows + add explicit confirmed default row
    cat_ws = src_wb["CATEGORY_RULES"]
    cat_rows = [list(r) for r in cat_ws.iter_rows(values_only=True)]
    header, body = cat_rows[0], cat_rows[1:]
    if not any(str(r[0]).strip() == "*" for r in body):
        body.append(["*", "NORMAL"])  # confirmed V10.5.5 default fallback, made explicit
    write_table(dst_wb, "CATEGORY_RULES", header, body)

    # MARKET_RULES (new filter layer)
    write_table(
        dst_wb, "MARKET_RULES",
        ["MARKET", "ACTIVE"],
        [
            ["IDT", "YES"],
            ["ČESKÁ POŠTA", "YES"],
            ["KA PARTNERS", "YES"],
            ["PETROL", "YES"],
            ["CORN", "YES"],
        ],
    )

    # CADENCE_RULES (unifies CORE / Mandatory / GECO / CORN)
    write_table(
        dst_wb, "CADENCE_RULES",
        ["ruleId", "scope", "matchValue", "minGapWeeks", "maxIntervalWeeks",
         "intervalType", "guaranteeType", "dedupBy", "campaignChangeOverride",
         "priority", "active", "validFrom", "validTo", "notes"],
        [
            ["CORE", "categoryPrefix", "1", 2, "", "RECURRING", "SOFT_HIGH_WEIGHT",
             "NONE", "YES", 90, "YES", "", "",
             "Evolution of V10.5.5 score constant (CORE+=1e8). minGapWeeks=2 preserves "
             "PREMIUM_GAP with campaign-change override."],
            ["MANDATORY_9PODNIK", "category", "9PODNIKC;9PODNIKFC", "", "",
             "ONCE_PER_CAMPAIGN", "HARD", "ADDRESS", "NO", 100, "YES", "", "",
             "Preserves V10.5.5 mandatoryPodnik(): one guaranteed slot per campaign run, "
             "best-PTT POS per street+city."],
            ["GECO", "category", "1GECO", "", 5, "RECURRING", "HARD", "NONE", "NO",
             80, "NO", "", "",
             "TODO: confirm scope (1GECO only vs whole KA PARTNERS market) and "
             "guaranteeType before activating. New for V11, no V10.5.5 precedent."],
            ["CORN", "market", "CORN", "", 4, "RECURRING", "HARD", "NONE", "NO",
             80, "NO", "", "",
             "TODO: confirm guaranteeType before activating (HARD proposed, "
             "16 POS = negligible capacity impact). New for V11, no V10.5.5 precedent."],
        ],
    )

    # PARETO_GROUPS
    write_table(
        dst_wb, "PARETO_GROUPS",
        ["tierId", "name", "scope", "boundaryType", "boundaryValue", "active", "notes"],
        [
            ["PREMIUM_TOP20", "Premium (top 20% per portfolio)", "PER_TECHNICIAN",
             "PERCENTILE", 20, "YES",
             "Preserves V10.5.5 behaviour exactly (relative ranking within each "
             "technician's own portfolio). Scope field kept switchable to GLOBAL / "
             "PER_REGION / PER_MARKET for a later decision, not activated now."],
            ["KA", "KA (business-significant outlets)", "", "", "", "NO",
             "TODO: scope + threshold not yet confirmed (BUSINESS_RULES.md)."],
            ["IDT_ABOVE_THRESHOLD", "IDT above PPT threshold", "", "", "", "NO",
             "TODO: boundaryType/scope not yet confirmed (BUSINESS_RULES.md)."],
        ],
    )

    # SCORE_PROFILES
    write_table(
        dst_wb, "SCORE_PROFILES",
        ["profileId", "component", "weight", "notes"],
        [
            ["DEFAULT", "CORE", 100000000, "Preserves V10.5.5 magnitude as-is; normalize later."],
            ["DEFAULT", "KATEGORIZACE_A", 10000000, "Preserves V10.5.5 magnitude as-is."],
            ["DEFAULT", "PPT", 1, "Raw PTT value, unweighted (as in V10.5.5)."],
            ["DEFAULT", "NEGLECTED_BONUS", 50000, "Applied when weeksSinceLastVisit >= NEGLECTED_AFTER_WEEKS."],
        ],
    )

    # ADVISOR_RULES (reserved for a future fully config-driven rule table -
    # AdvisorEngine.ts v1 reads its three alert types' thresholds directly
    # from CONTROL instead, see docs/BACKLOG.md for the planned generalization)
    write_table(
        dst_wb, "ADVISOR_RULES",
        ["ruleId", "type", "condition", "threshold", "severity", "messageTemplate", "active"],
        [],
    )

    # ADVISOR_LOG (append-only output of AdvisorEngine.ts)
    write_table(
        dst_wb, "ADVISOR_LOG",
        ["type", "severity", "subjectType", "subjectId", "message", "evaluatedAt"],
        [],
    )

    # CAPACITY_OVERRIDE
    write_table(
        dst_wb, "CAPACITY_OVERRIDE",
        ["technician", "year", "week", "capacity"],
        [],
    )

    # POS_STATUS_IMPORT (staging import sheet)
    write_table(
        dst_wb, "POS_STATUS_IMPORT",
        ["POS", "ACTIVE"],
        [],
    )

    # POS_MASTER (headers only — populated by Import Engine)
    write_table(
        dst_wb, "POS_MASTER",
        [
            "posId", "terminalId",
            "market", "category", "terminalType", "classification", "nazev", "area", "posArea",
            "street", "houseNumber", "city", "gpsX", "gpsY", "assignedTechnician", "ppt",
            "status", "closedSinceWeek", "closedSinceYear",
            "currentLosActivity", "currentLotActivity", "targetLosActivity", "targetLotActivity",
            "lastRealVisitDate", "lastRealVisitWeek", "lastPlannedVisitDate",
            "weeksSinceLastVisit", "visitCountThisCampaign",
            "businessScore",
            "plannerStatus", "assignedWeek", "assignedDay", "gpsGroup",
            "managerOverrideType", "managerOverridePriority", "managerOverrideTechnician",
            "plannerNotes",
            "importedAt", "updatedAt",
        ],
        [],
    )

    # MANAGER_PLAN (Planning Engine output - headers match legacy OUTPUT_PLAN
    # column order so the shape is familiar, populated by PlanningEngine.ts)
    write_table(
        dst_wb, "MANAGER_PLAN",
        ["WEEK", "DATE", "DAY", "TECHNICIAN", "POS", "KATEGORIE", "NAZEV_PROVOZOVNY",
         "ULICE", "CISLO", "MESTO", "OBLAST", "POS_AREA", "PPT", "LOS_ACTIVITY",
         "LOT_ACTIVITY", "REASON", "GPS_GROUP"],
        [],
    )

    # SALESAPP_IMPORT (staging - paste the weekly SalesApp export here; header
    # row matches the real export format so column-name lookup in
    # ComplianceEngine.ts works regardless of export column order/extras)
    write_table(
        dst_wb, "SALESAPP_IMPORT",
        ["UID", "Date", "State", "Started at", "Finished at", "Real duration (h)",
         "Chain UID", "Chain", "Store UID", "Store", "Store address",
         "Agency region", "Executor UID", "Executor"],
        [],
    )

    # VISIT_HISTORY_ACTUAL (real, append-only visit log from SalesApp -
    # distinct from the legacy VISIT_HISTORY sheet carried over from V10.5.5,
    # which recorded the script's own planned output, not reality; see
    # docs/BUSINESS_RULES.md 15c and ARCHITECTURE.md Compliance Engine entry)
    write_table(
        dst_wb, "VISIT_HISTORY_ACTUAL",
        ["posId", "date", "week", "year", "executor", "state", "salesAppUid"],
        [],
    )

    # COMPLIANCE_LOG (append-only, one row per planned-visit evaluation)
    write_table(
        dst_wb, "COMPLIANCE_LOG",
        ["posId", "technician", "plannedWeek", "plannedYear", "status",
         "matchedActualDate", "matchedActualWeek", "evaluatedAt"],
        [],
    )

    # DASHBOARD (Reporting Engine output - written fresh on every run)
    write_table(dst_wb, "DASHBOARD", ["", "", "", "", "", ""], [])

    dst_wb.save(out_path)
    print(f"Scaffold written to {out_path}")
    print("Sheets:", dst_wb.sheetnames)


if __name__ == "__main__":
    if len(sys.argv) != 3:
        print(__doc__)
        sys.exit(1)
    main(sys.argv[1], sys.argv[2])
