// ============================================================================
// FIELD FORCE OPTIMIZER V11 - ACTIVATE POS ENGINE
// ============================================================================
// Deployable Office Script. Run manually, whenever the manager wants to pull
// specific POS back into planning that CATEGORY_RULES currently excludes
// wholesale (e.g. category "1CD"/"1POSTA" - see CATEGORY_RULES).
//
// Product owner (2026-07-11), after being asked whether this should
// reassign POS ownership: "já nikdy do jejich 'přiřazení'... měnit nechci,
// ja chci mít možnost je přidat jako máme třeba teď vyřazené 1CD, ale chci
// mít možnost určit buďto jaké, nebo prvních 500 nehledě kolik techniků to
// bude" - explicitly NOT a reassignment tool. This never touches
// assignedTechnician. It only sets POS_MASTER.managerOverrideType to
// FORCE_INCLUDE on selected POS - the exact mechanism PlanningEngine.ts
// already has for "manually include a POS a category rule would otherwise
// filter out" (see that file's "FORCE_INCLUDE bypasses Filters entirely"
// comment) - so each activated POS still gets planned for its OWN existing
// assignedTechnician (or managerOverrideTechnician if already set), via the
// normal capacity/priority/hold-back rules. However many technicians those
// POS happen to belong to is exactly how many technicians end up touched -
// no manual per-technician split needed, by design.
//
// TWO SELECTION MODES (mutually exclusive; POS_ACTIVATE_LIST wins if it has
// any rows, otherwise CONTROL.ACTIVATE_COUNT_BY_PPT is used):
//   1. Explicit list - POS IDs pasted into POS_ACTIVATE_LIST (same minimal
//      paste-list pattern as BLACKLIST, just the opposite direction).
//      Product owner: "podle mého seznamu" - the manager's own picks.
//   2. Count by PPT - CONTROL.ACTIVATE_COUNT_BY_PPT (default 0 = disabled).
//      Builds the pool of Active POS whose CATEGORY_RULES rule resolves to
//      EXCLUDE and that aren't already FORCE_INCLUDE/FORCE_EXCLUDE, sorts by
//      ppt descending, activates the top N. Product owner: "nebo ppt".
//
// Idempotent: re-running with the same inputs is safe - a POS already
// FORCE_INCLUDE is left alone (no duplicate note appended), and the count
// mode's pool always excludes anything already activated, so a second run
// with the same N naturally picks up the NEXT-highest-PPT still-excluded
// POS rather than re-doing the same ones.
//
// Does not touch managerOverridePriority or managerOverrideTechnician -
// only managerOverrideType, plus a short audit trail appended to
// plannerNotes (never overwritten, only appended to).
// ============================================================================

function main(workbook: ExcelScript.Workbook) {
  function readTable(sheetName: string): (string | number | boolean)[][] {
    const ws = workbook.getWorksheet(sheetName);
    const range = ws.getUsedRange();
    return range ? range.getValues() : [];
  }

  // SYNC-BLOCK-START: text.ts
  function norm(v: string): string {
    return v
      .toUpperCase()
      .normalize("NFD")
      .replace(/[\u0300-\u036f]/g, "")
      .trim();
  }
  // SYNC-BLOCK-END: text.ts

  // SYNC-BLOCK-START: core.ts (activate)
  function categoryRule(
    categoryRulesTable: { key: string; value: string }[], // key/value already normalized (upper, no diacritics)
    categoryNormalized: string
  ): string {
    let starPrefixRule: string | null = null;
    for (const row of categoryRulesTable) {
      if (row.key == categoryNormalized) {
        return row.value; // exact match always wins immediately
      }
      if (row.key == "STARTS_1" && categoryNormalized.startsWith("1")) {
        starPrefixRule = row.value;
      }
      if (row.key == "*") {
        starPrefixRule = starPrefixRule ?? row.value;
      }
    }
    return starPrefixRule ?? "NORMAL";
  }
  // SYNC-BLOCK-END: core.ts (activate)

  const posMaster = readTable("POS_MASTER");
  if (posMaster.length < 2) {
    console.log("Activate POS Engine: POS_MASTER is empty - nothing to do.");
    return;
  }
  const categoryRulesRaw = readTable("CATEGORY_RULES");
  const activateListRaw = readTable("POS_ACTIVATE_LIST");
  const control = readTable("CONTROL");

  function setting(name: string, fallback: number): number {
    for (let i = 1; i < control.length; i++) {
      if (norm(String(control[i][0])) == norm(name)) {
        const v = Number(control[i][1]);
        return isNaN(v) ? fallback : v;
      }
    }
    return fallback;
  }
  const activateCountByPpt = setting("ACTIVATE_COUNT_BY_PPT", 0);

  let categoryRulesTable: { key: string; value: string }[] = [];
  for (let i = 1; i < categoryRulesRaw.length; i++) {
    categoryRulesTable.push({
      key: norm(String(categoryRulesRaw[i][0])),
      value: norm(String(categoryRulesRaw[i][1])),
    });
  }

  const pmHeaders = (posMaster[0] as string[]).map((h) => String(h));
  const pmIdx = (name: string) => pmHeaders.indexOf(name);
  const posIdCol = pmIdx("posId");
  const statusCol = pmIdx("status");
  const categoryCol = pmIdx("category");
  const overrideTypeCol = pmIdx("managerOverrideType");
  const pptCol = pmIdx("ppt");
  const notesCol = pmIdx("plannerNotes");

  let explicitIds = new Set<string>();
  for (let i = 1; i < activateListRaw.length; i++) {
    const v = String(activateListRaw[i][0] ?? "").trim();
    if (v) {
      explicitIds.add(v);
    }
  }

  const posWs = workbook.getWorksheet("POS_MASTER");
  const today = new Date().toISOString().slice(0, 10);
  let activated: string[] = [];
  let alreadyActive = 0;
  let skippedForceExclude: string[] = [];

  function activateRow(rowIndex: number, row: (string | number | boolean)[], via: string): void {
    const currentOverride = norm(String(row[overrideTypeCol] ?? ""));
    if (currentOverride == "FORCE_INCLUDE") {
      alreadyActive++;
      return;
    }
    posWs.getRangeByIndexes(rowIndex, overrideTypeCol, 1, 1).setValue("FORCE_INCLUDE");
    const existingNotes = String(row[notesCol] ?? "");
    posWs
      .getRangeByIndexes(rowIndex, notesCol, 1, 1)
      .setValue((existingNotes ? existingNotes + " | " : "") + "Hromadně aktivováno" + via + " " + today);
  }

  let mode = "";
  if (explicitIds.size > 0) {
    mode = "seznam (POS_ACTIVATE_LIST)";
    for (let i = 1; i < posMaster.length; i++) {
      const row = posMaster[i];
      const posId = String(row[posIdCol]);
      if (!explicitIds.has(posId)) {
        continue;
      }
      if (String(row[statusCol]) != "Active") {
        continue; // not a candidate at all - Closed POS never enter planning
      }
      const currentOverride = norm(String(row[overrideTypeCol] ?? ""));
      if (currentOverride == "FORCE_EXCLUDE") {
        skippedForceExclude.push(posId); // explicit block always wins - never silently overridden
        continue;
      }
      activateRow(i, row, "");
      activated.push(posId);
    }
  } else if (activateCountByPpt > 0) {
    mode = "prvních " + activateCountByPpt + " podle PPT";
    let pool: { index: number; posId: string; ppt: number; row: (string | number | boolean)[] }[] = [];
    for (let i = 1; i < posMaster.length; i++) {
      const row = posMaster[i];
      if (String(row[statusCol]) != "Active") {
        continue;
      }
      const currentOverride = norm(String(row[overrideTypeCol] ?? ""));
      if (currentOverride == "FORCE_EXCLUDE" || currentOverride == "FORCE_INCLUDE") {
        continue; // already decided one way or the other - not part of the "still excluded" pool
      }
      const category = String(row[categoryCol]);
      const rule = categoryRule(categoryRulesTable, norm(category));
      if (rule != "EXCLUDE") {
        continue; // only pulling from the currently-excluded pool, per the product owner's "vyřazené 1CD" example
      }
      pool.push({ index: i, posId: String(row[posIdCol]), ppt: Number(row[pptCol]) || 0, row });
    }
    pool.sort((a, b) => b.ppt - a.ppt);
    const take = pool.slice(0, activateCountByPpt);
    for (const item of take) {
      activateRow(item.index, item.row, " (PPT)");
      activated.push(item.posId);
    }
  } else {
    console.log(
      "Activate POS Engine: POS_ACTIVATE_LIST is empty and CONTROL.ACTIVATE_COUNT_BY_PPT is 0 - nothing to activate."
    );
    return;
  }

  console.log(
    "Activate POS Engine: mode = " + mode + ". " +
      activated.length + " POS newly/already set to FORCE_INCLUDE (" + alreadyActive + " already were), " +
      skippedForceExclude.length + " skipped (explicit FORCE_EXCLUDE always wins)" +
      (skippedForceExclude.length > 0 ? ": " + skippedForceExclude.join(", ") : "") +
      ". Run Planning Engine next to see them enter this week's plan."
  );
}
