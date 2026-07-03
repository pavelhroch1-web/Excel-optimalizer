// ============================================================================
// FIELD FORCE OPTIMIZER V11 - IMPORT ENGINE
// ============================================================================
// Deployable Office Script (paste this whole file into Excel's Code Editor as
// one script). Shared helper sections below are duplicated from office-scripts/
// shared/*.ts (dev-source of truth) because Office Scripts cannot import across
// files - see office-scripts/README.md for the reasoning and the sync convention.
//
// SCOPE OF THIS SCRIPT (per product-owner instruction - bottom-up build order):
//   - Upserts RAW_DATA + ACTIVITY_PLAN into POS_MASTER.
//   - POS Active/Closed status is determined SOLELY by presence in this
//     week's RAW_DATA (product owner, 2026-07-03: the weekly PPT/RAW_DATA
//     export always contains the full universe of POS, so "missing this
//     week" reliably means "closed"). POS_STATUS_IMPORT is no longer read -
//     this replaced it, it does not sit alongside it.
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
  // SYNC-BLOCK-START: text.ts
  function norm(v: string): string {
    return v
      .toUpperCase()
      .normalize("NFD")
      .replace(/[\u0300-\u036f]/g, "")
      .trim();
  }
  // SYNC-BLOCK-END: text.ts

  // SYNC-BLOCK-START: columns.ts
  function buildHeaderIndex(headerRow: (string | number | boolean)[]): string[] {
    return headerRow.map((x) => norm(String(x)));
  }

  // Exact match after normalization (diacritics/case-insensitive). Use for fields
  // where the header text is stable and you want to fail loudly on a rename.
  function exactCol(headers: string[], name: string): number {
    const n = norm(name);
    for (let i = 0; i < headers.length; i++) {
      if (headers[i] == n) {
        return i;
      }
    }
    return -1;
  }

  // Substring match after normalization. Use only for fields that may have
  // slightly different header text across export versions (e.g. "TECH" inside
  // "TECHNIK"). Prefer exactCol wherever the header text is otherwise stable -
  // substring matching is intentionally used sparingly (V10.5.5 used it only for
  // TECH and PTT columns).
  function col(headers: string[], name: string): number {
    const n = norm(name);
    for (let i = 0; i < headers.length; i++) {
      if (headers[i].includes(n)) {
        return i;
      }
    }
    return -1;
  }
  // SYNC-BLOCK-END: columns.ts

  // SYNC-BLOCK-START: core.ts (import)
  // Verbatim from office-scripts/shared/core.ts - do not hand-edit here.
  function isoWeekNumber(date: Date): { week: number; year: number } {
    const d = new Date(Date.UTC(date.getFullYear(), date.getMonth(), date.getDate()));
    const dayNum = (d.getUTCDay() + 6) % 7; // Mon=0..Sun=6
    d.setUTCDate(d.getUTCDate() - dayNum + 3); // shift to nearest Thursday
    const isoYear = d.getUTCFullYear();
    const firstThursday = new Date(Date.UTC(isoYear, 0, 4));
    const firstDayNum = (firstThursday.getUTCDay() + 6) % 7;
    firstThursday.setUTCDate(firstThursday.getUTCDate() - firstDayNum + 3);
    const week = 1 + Math.round((d.getTime() - firstThursday.getTime()) / (7 * 24 * 3600 * 1000));
    return { week, year: isoYear };
  }
  // SYNC-BLOCK-END: core.ts (import)

  // ==========================================================================
  // LOAD SHEETS
  // ==========================================================================

  const rawWs = workbook.getWorksheet("RAW_DATA");
  const raw = rawWs.getUsedRange().getValues();

  const activityWs = workbook.getWorksheet("ACTIVITY_PLAN");
  const activity = activityWs.getUsedRange().getValues();

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
  // POS ACTIVE/CLOSED STATUS: presence in this week's RAW_DATA is now the
  // sole source of truth (product owner, 2026-07-03 - confirmed the weekly
  // PPT/RAW_DATA export always contains the FULL universe of POS, so
  // "missing = closed" is safe). This REPLACES the previous POS_STATUS_IMPORT
  // mechanism entirely - POS_STATUS_IMPORT is no longer read by this engine.
  // See "today" below for closedSinceWeek/Year bookkeeping when a POS
  // transitions to Closed.
  // ==========================================================================

  const today = new Date();
  const { week: todayWeek, year: todayYear } = isoWeekNumber(today);

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
  // UPSERT: RAW_DATA -> POS_MASTER
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

    // Status: present in this week's RAW_DATA -> Active, always (see the
    // "POS ACTIVE/CLOSED STATUS" comment above). Re-opening a previously
    // Closed POS clears its closed-since bookkeeping.
    const posStatus = "Active";
    const closedSinceWeek: string | number = "";
    const closedSinceYear: string | number = "";

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

  // POS present in POS_MASTER but absent from this week's RAW_DATA: now
  // treated as Closed (see "POS ACTIVE/CLOSED STATUS" comment above) - the
  // row is preserved (history, overrides, notes all kept, never dropped),
  // only status/closedSinceWeek/closedSinceYear are updated. If already
  // Closed from a previous run, closedSinceWeek/Year are left untouched
  // (records the ORIGINAL week it first went missing, not the latest one).
  for (const posId of Object.keys(existingByPos)) {
    if (!posIdsInRawData[posId]) {
      const existing = existingByPos[posId];
      const row = (masterExisting[
        masterExisting.findIndex((r: (string | number | boolean)[]) => String(r[mIdx("posId")]) === posId)
      ] as (string | number)[]).slice();
      row[mIdx("status")] = "Closed";
      if (existing.status !== "Closed") {
        row[mIdx("closedSinceWeek")] = todayWeek;
        row[mIdx("closedSinceYear")] = todayYear;
      }
      row[mIdx("updatedAt")] = now;
      outRows.push(row);
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
  // contents only - a full clear() also wipes cell formatting (header
  // style, column colors, banded rows) applied by tools/ux_style.py, which
  // would otherwise be erased on every single run.
  masterWs.getRange("A1:AM100000").clear(ExcelScript.ClearApplyTo.contents);
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
      " from RAW_DATA (Active), " +
      (outRows.length - Object.keys(posIdsInRawData).length) +
      " missing from RAW_DATA this run (set/kept Closed)."
  );
}
