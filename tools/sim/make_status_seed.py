"""
One-off: seed to verify ImportEngine.ts's new "presence in RAW_DATA is the
sole source of Active/Closed status" rule across all four transitions:
  POS1: was Active, present again this week -> stays Active
  POS2: was Closed (closed long ago), present again this week -> reopens,
        closedSinceWeek/Year cleared
  POS3: was Active, ABSENT this week -> becomes Closed, closedSinceWeek/Year
        set to today's ISO week/year
  POS4: was already Closed since week 10/2026, ABSENT again this week ->
        stays Closed, closedSinceWeek/Year NOT overwritten (still 10/2026)
"""
import json

RAW_HEADER_ROW0 = ["instr"] * 16
RAW_HEADER_ROW1 = [
    "POS", "TECH", "TYP TERMINALU", "PTT", "KATEGORIE", "KATEGORIZACE", "MARKET",
    "NAZEV PROVOZOVNY", "ULICE", "CISLO POPISNE/ORIENTACNI", "MESTO", "OBLAST",
    "POS AREA", "X", "Y", "CISLO TERMINALU",
]


def raw_row(pos, tech="T1"):
    return [pos, tech, "Velky", 10, "9PODNIK", "A", "RSA", "Nazev", "Ulice", "1",
            "Mesto", "Oblast", "PA", 14.0, 50.0, "TERM" + pos]


MASTER_HEADER = [
    "posId", "terminalId", "market", "category", "terminalType", "classification",
    "nazev", "area", "posArea", "street", "houseNumber", "city", "gpsX", "gpsY",
    "assignedTechnician", "ppt", "status", "closedSinceWeek", "closedSinceYear",
    "currentLosActivity", "currentLotActivity", "targetLosActivity", "targetLotActivity",
    "lastRealVisitDate", "lastRealVisitWeek", "lastPlannedVisitDate",
    "weeksSinceLastVisit", "visitCountThisCampaign", "businessScore",
    "plannerStatus", "assignedWeek", "assignedDay", "gpsGroup",
    "managerOverrideType", "managerOverridePriority", "managerOverrideTechnician",
    "plannerNotes", "importedAt", "updatedAt",
]


def master_row(pos, status, closed_week="", closed_year=""):
    row = [pos, "OLDTERM", "RSA", "9PODNIK", "Velky", "A", "Nazev", "Oblast", "PA",
           "Ulice", "1", "Mesto", 14.0, 50.0, "T1", 10, status, closed_week, closed_year]
    row += [""] * (len(MASTER_HEADER) - len(row) - 2)
    row += ["2026-01-01T00:00:00.000Z", "2026-01-01T00:00:00.000Z"]
    assert len(row) == len(MASTER_HEADER), (len(row), len(MASTER_HEADER))
    return row


state = {
    "RAW_DATA": [RAW_HEADER_ROW0, RAW_HEADER_ROW1, raw_row("POS1"), raw_row("POS2")],
    "ACTIVITY_PLAN": [["activityType", "activity", "startWeek", "endWeek", "priority", "overrideGapWeeks"]],
    "POS_STATUS_IMPORT": [["POS", "ACTIVE"]],
    "POS_MASTER": [
        MASTER_HEADER,
        master_row("POS1", "Active"),
        master_row("POS2", "Closed", 5, 2025),
        master_row("POS3", "Active"),
        master_row("POS4", "Closed", 10, 2026),
    ],
}

with open("tools/sim/status_seed.json", "w", encoding="utf-8") as f:
    json.dump(state, f, ensure_ascii=False)
print("Seed written to tools/sim/status_seed.json")
