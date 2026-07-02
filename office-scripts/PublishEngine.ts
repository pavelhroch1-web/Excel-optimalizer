// ============================================================================
// FIELD FORCE OPTIMIZER V11 - PUBLISH ENGINE
// ============================================================================
// Deployable Office Script. Run manually, as an explicit action, after
// reviewing/adjusting the Draft plan in MANAGER_PLAN and before sending it to
// technicians. This is the "Publish" step from docs/BUSINESS_RULES.md
// section 11 (Draft -> Review -> Published -> Active -> Closed).
//
// WHAT IT DOES:
//   - Finds the single EARLIEST week that is still Draft (has rows in
//     MANAGER_PLAN but no Published/Active/Closed row in PLAN_LIFECYCLE).
//     Only one week is published per run, matching the real weekly ritual
//     (you publish the upcoming week, not the whole multi-week horizon at
//     once) and the "only the nearest week becomes binding" rolling-horizon
//     design agreed earlier.
//   - Copies that week's MANAGER_PLAN rows verbatim into
//     MANAGER_PLAN_PUBLISHED (append-only, immutable snapshot).
//   - Sets that week's PLAN_LIFECYCLE status to Published, records
//     publishedAt.
//
// WHAT IT DELIBERATELY DOES NOT DO:
//   - It does not touch MANAGER_PLAN itself - Planning Engine already
//     refuses to regenerate a locked week once PLAN_LIFECYCLE says
//     Published, so simply flipping the status here is sufficient to lock it
//     going forward.
//   - It does not decide when Published becomes Active or Closed - that is
//     mechanical/derived and handled by ComplianceEngine.ts on every run,
//     never here.
//   - It does not pick a technician-by-technician subset - a week is
//     published as a whole (matches "tyden je zavazny", not per-technician).
// ============================================================================

function main(workbook: ExcelScript.Workbook) {
  function readTable(sheetName: string): (string | number | boolean)[][] {
    const ws = workbook.getWorksheet(sheetName);
    const range = ws.getUsedRange();
    return range ? range.getValues() : [];
  }

  const managerPlan = readTable("MANAGER_PLAN");
  const planLifecycle = readTable("PLAN_LIFECYCLE");
  const control = readTable("CONTROL");

  function setting(name: string, fallback: number): number {
    for (let i = 1; i < control.length; i++) {
      if (String(control[i][0]).toUpperCase().trim() == name.toUpperCase()) {
        const v = Number(control[i][1]);
        return isNaN(v) ? fallback : v;
      }
    }
    return fallback;
  }
  const YEAR = setting("YEAR", new Date().getFullYear());

  if (managerPlan.length < 2) {
    console.log("Publish Engine: MANAGER_PLAN is empty - run Planning Engine first.");
    return;
  }

  // Weeks already Published/Active/Closed - never republish.
  let lockedWeeks = new Set<number>();
  let plHeaders: string[] = [];
  let plIdx: (name: string) => number = () => -1;
  if (planLifecycle.length >= 2) {
    plHeaders = (planLifecycle[0] as string[]).map((h) => String(h));
    plIdx = (name: string) => plHeaders.indexOf(name);
    for (let i = 1; i < planLifecycle.length; i++) {
      const row = planLifecycle[i];
      if (Number(row[plIdx("year")]) != YEAR) {
        continue;
      }
      const status = String(row[plIdx("status")]);
      if (status == "Published" || status == "Active" || status == "Closed") {
        lockedWeeks.add(Number(row[plIdx("week")]));
      }
    }
  }

  // Earliest Draft week present in MANAGER_PLAN.
  let draftWeeks = new Set<number>();
  for (let i = 1; i < managerPlan.length; i++) {
    const week = Number(managerPlan[i][0]);
    if (week && !lockedWeeks.has(week)) {
      draftWeeks.add(week);
    }
  }
  if (draftWeeks.size == 0) {
    console.log("Publish Engine: no Draft week found to publish (everything is already locked, or MANAGER_PLAN is empty).");
    return;
  }
  const weekToPublish = Math.min(...draftWeeks);

  const rowsToPublish = managerPlan
    .slice(1)
    .filter((row) => Number(row[0]) == weekToPublish);

  const now = new Date().toISOString();
  const publishedRows = rowsToPublish.map((row) => [...row, now] as (string | number)[]);

  const publishedWs = workbook.getWorksheet("MANAGER_PLAN_PUBLISHED");
  const existingPublished = publishedWs.getUsedRange();
  const startRow = existingPublished ? existingPublished.getRowCount() : 1;
  publishedWs.getRangeByIndexes(startRow, 0, publishedRows.length, 18).setValues(publishedRows);

  // Update or insert the PLAN_LIFECYCLE row for this week.
  const plWs = workbook.getWorksheet("PLAN_LIFECYCLE");
  let existingRowIndex = -1;
  for (let i = 1; i < planLifecycle.length; i++) {
    if (Number(planLifecycle[i][plIdx("year")]) == YEAR && Number(planLifecycle[i][plIdx("week")]) == weekToPublish) {
      existingRowIndex = i;
      break;
    }
  }
  if (existingRowIndex >= 0) {
    plWs.getRangeByIndexes(existingRowIndex, 2, 1, 2).setValues([["Published", now]]);
  } else {
    const startPlRow = planLifecycle.length > 0 ? planLifecycle.length : 1;
    plWs.getRangeByIndexes(startPlRow, 0, 1, 5).setValues([[YEAR, weekToPublish, "Published", now, ""]]);
  }

  console.log(
    "Publish Engine: week " + weekToPublish + "/" + YEAR + " published (" +
      publishedRows.length + " visits snapshotted to MANAGER_PLAN_PUBLISHED). " +
      "This week is now locked - Planning Engine will not regenerate it."
  );
}
