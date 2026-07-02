// ============================================================================
// FIELD FORCE OPTIMIZER V11 - IMPORT ENGINE
// ============================================================================
// Deployable Office Script (paste this whole file into Excel's Code Editor as
// one script). Shared helper sections below are duplicated from office-scripts/
// shared/*.ts (dev-source of truth) because Office Scripts cannot import across
// files - see office-scripts/README.md for the reasoning and the sync convention.
//
// SCOPE OF THIS SCRIPT (per product-owner instruction - bottom-up build order):
//   - Upserts RAW_DATA + POS_STATUS_IMPORT + ACTIVITY_PLAN into POS_MASTER.
//   - Never touches manager-override fields (managerOverrideType/Priority/
//     Technician, plannerNotes) on existing POS_MASTER rows.
//   - Does NOT run any scoring, filtering, cadence, or route logic - that is
//     Planning Engine, built later on top of this foundation.
//   - Does NOT import SalesApp / VISIT_HISTORY yet. Deliberately deferred:
//     the SalesApp -> LOS/LOT activity mapping is still an open business
//     question (docs/BUSINESS_RULES.md), and building it now risks encoding
//     a wrong assumption. TODO once that mapping is confirmed.
// ============================================================================

function main(workbook: ExcelScript.Workbook) {
  // ---- SHARED: text.ts ----
  function norm(v: string): string {
    return v
      .toUpperCase()
      .normalize("NFD")
      .replace(/[\u0300-\u036f]/g, "")
      .trim();
  }

  // ---- SHARED: columns.ts ----
  function buildHeaderIndex(headerRow: (string | number | boolean)[]): string[] {
    return headerRow.map((x) => norm(String(x)));
  }
  function exactCol(headers: string[], name: string): number {
    const n = norm(name);
    for (let i = 0; i < headers.length; i++) {
      if (headers[i] == n) {
        return i;
      }
    }
    return -1;
  }
  function col(headers: string[], name: string): number {
    const n = norm(name);
    for (let i = 0; i < headers.length; i++) {
      if (headers[i].includes(n)) {
        return i;
      }
    }
    return -1;
  }

  // ==========================================================================
  // LOAD SHEETS
  // ==========================================================================

  const rawWs = workbook.getWorksheet("RAW_DATA");
  const raw = rawWs.getUsedRange().getValues();

  const activityWs = workbook.getWorksheet("ACTIVITY_PLAN");
  const activity = activityWs.getUsedRange().getValues();

  const statusWs = workbook.getWorksheet("POS_STATUS_IMPORT");
  const statusRange = statusWs.getUsedRange();
  const status = statusRange ? statusRange.getValues() : [["POS", "ACTIVE"]];

  const masterWs = workbook.getWorksheet("POS_MASTER");
  const masterRange = masterWs.getUsedRange();
  const masterExisting = masterRange ? masterRange.getValues() : [];

  // ==========================================================================
  // RAW_DATA COLUMN MAPPING (dynamic - header row is raw[1], matching V10.5.5's
  // layout where raw[0] is an instruction row and raw[1] is the real header)
  // ==========================================================================

  const headers = buildHeaderIndex(raw[1] as string[]);

  const cPOS = exactCol(headers, "POS");
  const cTECH = col(headers, "TECH");
  const cTERM = exactCol(headers, "TYP TERMINALU"); // norm() strips diacritics
  const cPTT = col(headers, "PTT");
  const cKATEG = exactCol(headers, "KATEGORIE");
  const cKATEGORIZACE = exactCol(headers, "KATEGORIZACE"); // exact name, not
    // positional (`katCols[1]`) - V10.5.5's fragile lookup, replaced per
    // product-owner decision (docs/BUSINESS_RULES.md 15b item 2).
  const cMARKET = exactCol(headers, "MARKET");
  const cNAZEV = exactCol(headers, "NAZEV PROVOZOVNY");
  const cULICE = exactCol(headers, "ULICE");
  const cCISLO = exactCol(headers, "CISLO POPISNE/ORIENTACNI");
  const cMESTO = exactCol(headers, "MESTO");
  const cOBLAST = exactCol(headers, "OBLAST");
  const cAREA = exactCol(headers, "POS AREA");
  const cX = exactCol(headers, "X");
  const cY = exactCol(headers, "Y");
  const cTERMID = exactCol(headers, "CISLO TERMINALU");

  // ==========================================================================
  // POS_STATUS_IMPORT -> lookup map (source of truth for Active/Closed)
  // ==========================================================================

  let statusByPos: { [pos: string]: boolean } = {};
  for (let i = 1; i < status.length; i++) {
    const pos = String(status[i][0]);
    if (pos) {
      statusByPos[pos] = Number(status[i][1]) === 1;
    }
  }

  // ==========================================================================
  // ACTIVITY_PLAN -> stored, not used by any engine yet (see file header)
  // ==========================================================================

  interface ActivityPlanEntry {
    activityType: string;
    activity: string;
    startWeek: number;
    endWeek: number;
    priority: number | null;
    overrideGapWeeks: number | null;
  }
  let activityEntries: ActivityPlanEntry[] = [];
  for (let i = 1; i < activity.length; i++) {
    const row = activity[i];
    if (!row[0]) {
      continue;
    }
    activityEntries.push({
      activityType: norm(String(row[0])),
      activity: String(row[1]),
      startWeek: Number(row[2]),
      endWeek: Number(row[3]),
      priority: row[4] === undefined || row[4] === "" ? null : Number(row[4]),
      overrideGapWeeks: row[5] === undefined || row[5] === "" ? null : Number(row[5]),
    });
  }
  // activityEntries is intentionally not written anywhere yet - Planning Engine
  // will read ACTIVITY_PLAN directly when it is built. This block exists so the
  // parsing logic (including priority/overrideGapWeeks passthrough) is proven
  // here first, per the "build data model before engines" instruction.

  // ==========================================================================
  // EXISTING POS_MASTER -> index by posId, so manual fields can be preserved
  // ==========================================================================

  const masterHeaders: string[] =
    masterExisting.length > 0
      ? (masterExisting[0] as string[]).map((h) => String(h))
      : [];
  const mIdx = (name: string) => masterHeaders.indexOf(name);

  interface ExistingManualFields {
    managerOverrideType: string;
    managerOverridePriority: string;
    managerOverrideTechnician: string;
    plannerNotes: string;
    closedSinceWeek: string | number;
    closedSinceYear: string | number;
    status: string;
  }
  let existingByPos: { [pos: string]: ExistingManualFields } = {};
  for (let i = 1; i < masterExisting.length; i++) {
    const row = masterExisting[i];
    const posId = String(row[mIdx("posId")]);
    if (!posId) {
      continue;
    }
    existingByPos[posId] = {
      managerOverrideType: String(row[mIdx("managerOverrideType")] ?? ""),
      managerOverridePriority: String(row[mIdx("managerOverridePriority")] ?? ""),
      managerOverrideTechnician: String(row[mIdx("managerOverrideTechnician")] ?? ""),
      plannerNotes: String(row[mIdx("plannerNotes")] ?? ""),
      closedSinceWeek: row[mIdx("closedSinceWeek")] ?? "",
      closedSinceYear: row[mIdx("closedSinceYear")] ?? "",
      status: String(row[mIdx("status")] ?? "Active"),
    };
  }

  // ==========================================================================
  // UPSERT: RAW_DATA (+ POS_STATUS_IMPORT) -> POS_MASTER
  // ==========================================================================

  const now = new Date().toISOString();
  const posIdsInRawData: { [pos: string]: boolean } = {};
  let outRows: (string | number)[][] = [];

  for (let i = 2; i < raw.length; i++) {
    const r = raw[i];
    const posId = String(r[cPOS]);
    if (!posId) {
      continue;
    }
    posIdsInRawData[posId] = true;

    const existing = existingByPos[posId];

    // Status: POS_STATUS_IMPORT is the only source of truth. A POS missing
    // from POS_STATUS_IMPORT keeps its previous status (default Active for a
    // brand-new POS) - RAW_DATA presence/absence never closes a POS, per
    // docs/BUSINESS_RULES.md section 2 ("Closed POS").
    let posStatus = existing ? existing.status : "Active";
    let closedSinceWeek: string | number = existing ? existing.closedSinceWeek : "";
    let closedSinceYear: string | number = existing ? existing.closedSinceYear : "";
    if (statusByPos.hasOwnProperty(posId)) {
      const isActive = statusByPos[posId];
      if (isActive && posStatus === "Closed") {
        // Re-opened: clear closed-since bookkeeping.
        posStatus = "Active";
        closedSinceWeek = "";
        closedSinceYear = "";
      } else if (!isActive && posStatus !== "Closed") {
        posStatus = "Closed";
        // Week/year of closure left for the caller to fill via CONTROL-driven
        // "current week" once Planning Engine exists; blank for now rather
        // than guessing.
      }
    }

    outRows.push([
      posId, // posId
      String(r[cTERMID]), // terminalId
      String(r[cMARKET]), // market
      String(r[cKATEG]), // category
      String(r[cTERM]), // terminalType
      String(r[cKATEGORIZACE]), // classification
      String(r[cNAZEV]), // nazev
      String(r[cOBLAST]), // area (OBLAST column - distinct from POS AREA)
      String(r[cAREA]), // posArea (POS AREA column)
      String(r[cULICE]), // street
      String(r[cCISLO]), // houseNumber
      String(r[cMESTO]), // city
      Number(r[cX]) || 0, // gpsX
      Number(r[cY]) || 0, // gpsY
      String(r[cTECH]), // assignedTechnician
      Number(r[cPTT]) || 0, // ppt
      posStatus, // status
      closedSinceWeek, // closedSinceWeek
      closedSinceYear, // closedSinceYear
      "", "", "", "", // currentLos/currentLot/targetLos/targetLot - Planning Engine fills these
      "", "", "", "", 0, // visit facts - Compliance Engine fills these
      "", // businessScore - Business Engine fills this
      "", "", "", "", // plannerStatus/assignedWeek/assignedDay/gpsGroup - Decision/Route Engine
      existing ? existing.managerOverrideType : "", // NEVER overwritten
      existing ? existing.managerOverridePriority : "", // NEVER overwritten
      existing ? existing.managerOverrideTechnician : "", // NEVER overwritten
      existing ? existing.plannerNotes : "", // NEVER overwritten
      existing ? "" : now, // importedAt (set once, kept from original import)
      now, // updatedAt
    ]);
  }

  // POS present in POS_MASTER but absent from this RAW_DATA batch: keep them
  // untouched (append unchanged) rather than dropping the row. RAW_DATA
  // disappearing a POS is never treated as closure - only POS_STATUS_IMPORT
  // is authoritative for that.
  for (const posId of Object.keys(existingByPos)) {
    if (!posIdsInRawData[posId]) {
      const row = masterExisting[
        masterExisting.findIndex((r) => String(r[mIdx("posId")]) === posId)
      ];
      outRows.push(row as (string | number)[]);
    }
  }

  // ==========================================================================
  // WRITE POS_MASTER
  // ==========================================================================

  const masterHeaderRow = [
    "posId", "terminalId", "market", "category", "terminalType", "classification",
    "nazev", "area", "posArea", "street", "houseNumber", "city", "gpsX", "gpsY",
    "assignedTechnician", "ppt", "status", "closedSinceWeek", "closedSinceYear",
    "currentLosActivity", "currentLotActivity", "targetLosActivity", "targetLotActivity",
    "lastRealVisitDate", "lastRealVisitWeek", "lastPlannedVisitDate",
    "weeksSinceLastVisit", "visitCountThisCampaign", "businessScore",
    "plannerStatus", "assignedWeek", "assignedDay", "gpsGroup",
    "managerOverrideType", "managerOverridePriority", "managerOverrideTechnician",
    "plannerNotes", "importedAt", "updatedAt",
  ];

  // 39 columns -> column AM. Recompute if masterHeaderRow.length changes.
  masterWs.getRange("A1:AM100000").clear();
  masterWs.getRangeByIndexes(0, 0, 1, masterHeaderRow.length).setValues([masterHeaderRow]);
  if (outRows.length > 0) {
    masterWs
      .getRangeByIndexes(1, 0, outRows.length, masterHeaderRow.length)
      .setValues(outRows);
  }

  console.log(
    "Import Engine: " +
      outRows.length +
      " POS_MASTER rows upserted (" +
      Object.keys(posIdsInRawData).length +
      " from RAW_DATA, " +
      (outRows.length - Object.keys(posIdsInRawData).length) +
      " retained unchanged)."
  );
}
