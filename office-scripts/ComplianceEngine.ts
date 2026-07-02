// ============================================================================
// FIELD FORCE OPTIMIZER V11 - COMPLIANCE ENGINE
// ============================================================================
// Deployable Office Script. Run AFTER importing a new SalesApp export into
// SALESAPP_IMPORT and AFTER Planning Engine has produced MANAGER_PLAN for the
// relevant week(s).
//
// SCOPE OF THIS VERSION:
//   - Parses SalesApp: UID, Date, State, Store UID, Executor (all unambiguous,
//     explicitly-named columns - see the file header note on what was
//     deliberately NOT attempted).
//   - Realized visit = State in {Completed, Finalized} (~99.7% of rows in the
//     real export; Suspended/InProgress excluded as not-yet-completed - a
//     stated assumption, not a guess about column meaning, flagged for
//     correction if wrong).
//   - Appends to VISIT_HISTORY_ACTUAL, deduplicated by SalesApp UID (safe to
//     re-import overlapping weekly exports).
//   - Matches actual visits to MANAGER_PLAN rows by POS + week (Store UID =
//     POS number, confirmed against real data). Does NOT attempt to match
//     SalesApp "Executor" to a POS_MASTER technician name - the two systems
//     use incompatible name formats ("Rek Lubomir" vs "302 Jan Kochman") and
//     guessing a fuzzy match was explicitly ruled out. Technician-level KPIs
//     use MANAGER_PLAN's own technician assignment instead, which sidesteps
//     the problem entirely for the compliance/KPI use case.
//   - Writes COMPLIANCE_LOG (Splneno_vcas / Splneno_pozde / Nesplneno /
//     Pending - see core.ts determineComplianceStatus for why "Pending"
//     exists as a bookkeeping state alongside the four states named in
//     docs/BUSINESS_RULES.md section 12).
//   - Updates POS_MASTER's lastRealVisitDate/Week and weeksSinceLastVisit -
//     this closes the real-world feedback loop that was completely missing
//     in V10.5.5 (see docs/ARCHITECTURE.md Phase 0 finding: VISIT_HISTORY
//     used to record the script's own planned output, not reality).
//
// DELIBERATELY NOT IN THIS VERSION (see docs/BUSINESS_RULES.md and the
// conversation record for why):
//   - Which LOS/LOT campaign/product a visit serviced. The SalesApp export
//     has no reliable structured column for this (checked all 37 columns -
//     campaign names only appear in inconsistent free-text notes). A
//     candidate design (derive it from ACTIVITY_PLAN's week-based schedule
//     crossed with the "Nabeh kampane" Ano/Ne signal) is proposed but NOT
//     implemented pending product-owner confirmation, since it is a business
//     interpretation of ambiguous data, not a technical detail.
//   - "Navic evidovano" (extra visit) attribution to a specific technician -
//     logged with the raw SalesApp Executor string as-is, not resolved to a
//     POS_MASTER technician identity.
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

  // ---- SHARED: core.ts (isoWeekNumber / weeksBetween / determineComplianceStatus) ----
  function isoWeekNumber(date: Date): { week: number; year: number } {
    const d = new Date(Date.UTC(date.getFullYear(), date.getMonth(), date.getDate()));
    const dayNum = (d.getUTCDay() + 6) % 7;
    d.setUTCDate(d.getUTCDate() - dayNum + 3);
    const isoYear = d.getUTCFullYear();
    const firstThursday = new Date(Date.UTC(isoYear, 0, 4));
    const firstDayNum = (firstThursday.getUTCDay() + 6) % 7;
    firstThursday.setUTCDate(firstThursday.getUTCDate() - firstDayNum + 3);
    const week = 1 + Math.round((d.getTime() - firstThursday.getTime()) / (7 * 24 * 3600 * 1000));
    return { week, year: isoYear };
  }
  function weeksBetween(week1: number, year1: number, week2: number, year2: number): number {
    return week2 - week1 + (year2 - year1) * 52;
  }
  function determineComplianceStatus(
    plannedWeek: number,
    plannedYear: number,
    actualWeeks: { week: number; year: number }[],
    lateCutoffWeeks: number,
    latestKnownWeek: number,
    latestKnownYear: number
  ): string {
    if (actualWeeks.length === 0) {
      const elapsed = weeksBetween(plannedWeek, plannedYear, latestKnownWeek, latestKnownYear);
      return elapsed > lateCutoffWeeks ? "Nesplneno" : "Pending";
    }
    const earliest = actualWeeks.reduce((min, w) =>
      weeksBetween(plannedWeek, plannedYear, w.week, w.year) <
      weeksBetween(plannedWeek, plannedYear, min.week, min.year)
        ? w
        : min
    );
    const delta = weeksBetween(plannedWeek, plannedYear, earliest.week, earliest.year);
    return delta <= 0 ? "Splneno_vcas" : "Splneno_pozde";
  }

  // ==========================================================================
  // LOAD SHEETS
  // ==========================================================================

  function readTable(sheetName: string): (string | number | boolean)[][] {
    const ws = workbook.getWorksheet(sheetName);
    const range = ws.getUsedRange();
    return range ? range.getValues() : [];
  }

  const salesApp = readTable("SALESAPP_IMPORT");
  const managerPlan = readTable("MANAGER_PLAN");
  const control = readTable("CONTROL");
  const visitHistoryActual = readTable("VISIT_HISTORY_ACTUAL");
  const posMaster = readTable("POS_MASTER");

  function setting(name: string, fallback: number): number {
    for (let i = 1; i < control.length; i++) {
      if (norm(String(control[i][0])) == norm(name)) {
        const v = Number(control[i][1]);
        return isNaN(v) ? fallback : v;
      }
    }
    return fallback;
  }
  const LATE_CUTOFF = setting("COMPLIANCE_LATE_CUTOFF_WEEKS", 1);

  if (salesApp.length < 2) {
    console.log("Compliance Engine: SALESAPP_IMPORT is empty, nothing to do.");
    return;
  }
  if (managerPlan.length < 2) {
    console.log("Compliance Engine: MANAGER_PLAN is empty - run Planning Engine first.");
    return;
  }

  // ==========================================================================
  // PARSE SALESAPP_IMPORT -> new realized visits (dedup by UID)
  // ==========================================================================

  const saHeaders = (salesApp[0] as string[]).map((h) => norm(String(h)));
  const saIdx = (name: string) => saHeaders.indexOf(norm(name));
  const cUID = saIdx("UID");
  const cDate = saIdx("DATE");
  const cState = saIdx("STATE");
  const cStoreUID = saIdx("STORE UID");
  const cExecutor = saIdx("EXECUTOR");

  const knownUids = new Set<string>();
  for (let i = 1; i < visitHistoryActual.length; i++) {
    knownUids.add(String(visitHistoryActual[i][6]));
  }

  interface ActualVisit {
    posId: string;
    date: Date;
    week: number;
    year: number;
    executor: string;
    state: string;
    uid: string;
  }
  let newVisits: ActualVisit[] = [];
  let latestWeek = 0;
  let latestYear = 0;

  for (let i = 1; i < salesApp.length; i++) {
    const row = salesApp[i];
    const uid = String(row[cUID]);
    if (!uid || knownUids.has(uid)) {
      continue; // already imported, or blank row
    }
    const state = norm(String(row[cState]));
    if (state != "COMPLETED" && state != "FINALIZED") {
      continue; // Suspended/InProgress - not a realized visit (stated assumption, see file header)
    }
    const dateVal = row[cDate];
    const date = dateVal instanceof Date ? dateVal : new Date(String(dateVal));
    if (isNaN(date.getTime())) {
      continue;
    }
    const { week, year } = isoWeekNumber(date);
    if (year > latestYear || (year == latestYear && week > latestWeek)) {
      latestWeek = week;
      latestYear = year;
    }
    newVisits.push({
      posId: String(row[cStoreUID]),
      date,
      week,
      year,
      executor: String(row[cExecutor]),
      state,
      uid,
    });
  }

  // If nothing new AND no prior history either, we have no reference "now" -
  // bail out rather than silently evaluating compliance against week 0.
  if (latestWeek == 0 && visitHistoryActual.length < 2) {
    console.log("Compliance Engine: no realized visits found in SALESAPP_IMPORT (all rows already imported, or none Completed/Finalized).");
    return;
  }
  // If this run added no new visits, fall back to the latest week already on
  // record so re-running Compliance Engine without a fresh import still
  // re-evaluates Pending rows correctly against previously known "now".
  if (latestWeek == 0) {
    for (let i = 1; i < visitHistoryActual.length; i++) {
      const w = Number(visitHistoryActual[i][2]);
      const y = Number(visitHistoryActual[i][3]);
      if (y > latestYear || (y == latestYear && w > latestWeek)) {
        latestWeek = w;
        latestYear = y;
      }
    }
  }

  // ==========================================================================
  // APPEND VISIT_HISTORY_ACTUAL
  // ==========================================================================

  const historyWs = workbook.getWorksheet("VISIT_HISTORY_ACTUAL");
  if (newVisits.length > 0) {
    const rows = newVisits.map((v) => [
      v.posId, v.date.toISOString().slice(0, 10), v.week, v.year, v.executor, v.state, v.uid,
    ]);
    const startRow = visitHistoryActual.length > 0 ? visitHistoryActual.length : 1;
    historyWs.getRangeByIndexes(startRow, 0, rows.length, 7).setValues(rows);
  }

  // Full actual-visit set (existing + new) grouped by POS, for matching below
  // and for updating POS_MASTER's last-visit fields.
  let actualByPos: { [pos: string]: { week: number; year: number; date: string }[] } = {};
  for (let i = 1; i < visitHistoryActual.length; i++) {
    const pos = String(visitHistoryActual[i][0]);
    if (!actualByPos[pos]) {
      actualByPos[pos] = [];
    }
    actualByPos[pos].push({
      week: Number(visitHistoryActual[i][2]),
      year: Number(visitHistoryActual[i][3]),
      date: String(visitHistoryActual[i][1]),
    });
  }
  for (const v of newVisits) {
    if (!actualByPos[v.posId]) {
      actualByPos[v.posId] = [];
    }
    actualByPos[v.posId].push({ week: v.week, year: v.year, date: v.date.toISOString().slice(0, 10) });
  }

  // ==========================================================================
  // MATCH MANAGER_PLAN -> COMPLIANCE_LOG
  // ==========================================================================

  const mpHeaders = (managerPlan[0] as string[]).map((h) => String(h));
  const mpIdx = (name: string) => mpHeaders.indexOf(name);
  const cWeek = mpIdx("WEEK");
  const cPos2 = mpIdx("POS");
  const cTech2 = mpIdx("TECHNICIAN");

  // One planned row per (posId, week) pair, even if MANAGER_PLAN has that POS
  // only once per week (it should - Planning Engine doesn't double-book a POS
  // in the same week - but de-duplicate defensively rather than assume).
  let plannedSet: { [key: string]: { posId: string; week: number; tech: string } } = {};
  for (let i = 1; i < managerPlan.length; i++) {
    const row = managerPlan[i];
    const posId = String(row[cPos2]);
    const week = Number(row[cWeek]);
    if (!posId || !week) {
      continue;
    }
    plannedSet[posId + "|" + week] = { posId, week, tech: String(row[cTech2]) };
  }
  // Assumes MANAGER_PLAN's YEAR is the same as latestYear from SalesApp - true
  // for the current single-year campaign scope of this project. Flagged as a
  // known simplification if the project ever spans a year boundary.
  const plannedYear = latestYear || new Date().getFullYear();

  let complianceRows: (string | number)[][] = [];
  const now = new Date().toISOString();
  const matchedPlannedKeys = new Set<string>();

  for (const key of Object.keys(plannedSet)) {
    const planned = plannedSet[key];
    matchedPlannedKeys.add(key);
    const actuals = (actualByPos[planned.posId] || []).map((a) => ({ week: a.week, year: a.year }));
    const status = determineComplianceStatus(planned.week, plannedYear, actuals, LATE_CUTOFF, latestWeek, latestYear);
    const matched = (actualByPos[planned.posId] || []).find((a) => a.week == planned.week && a.year == plannedYear);
    complianceRows.push([
      planned.posId, planned.tech, planned.week, plannedYear, status,
      matched ? matched.date : "", matched ? matched.week : "", now,
    ]);
  }

  // Extra visits: actual visits to a POS in a week where that POS was not
  // planned at all (per BUSINESS_RULES.md section 12: neutral, logged only).
  for (const posId of Object.keys(actualByPos)) {
    for (const a of actualByPos[posId]) {
      const key = posId + "|" + a.week;
      if (!plannedSet[key]) {
        complianceRows.push([posId, "", a.week, a.year, "Navic_evidovano", a.date, a.week, now]);
      }
    }
  }

  const complianceWs = workbook.getWorksheet("COMPLIANCE_LOG");
  const existingCompliance = complianceWs.getUsedRange();
  const complianceStartRow = existingCompliance ? existingCompliance.getRowCount() : 1;
  if (complianceRows.length > 0) {
    complianceWs
      .getRangeByIndexes(complianceStartRow, 0, complianceRows.length, 8)
      .setValues(complianceRows);
  }

  // ==========================================================================
  // UPDATE POS_MASTER last-visit fields (closes the real-world feedback loop
  // that V10.5.5 never had - see file header)
  // ==========================================================================

  const mHeaders = (posMaster[0] as string[]).map((h) => String(h));
  const midx = (name: string) => mHeaders.indexOf(name);
  let updated = 0;
  for (let i = 1; i < posMaster.length; i++) {
    const posId = String(posMaster[i][midx("posId")]);
    const actuals = actualByPos[posId];
    if (!actuals || actuals.length == 0) {
      continue;
    }
    const latest = actuals.reduce((max, a) =>
      weeksBetween(max.week, max.year, a.week, a.year) > 0 ? a : max
    );
    const weeksSince = weeksBetween(latest.week, latest.year, latestWeek, latestYear);
    const rowIndex = i; // 0-based within posMaster array == sheet row index (header at 0)
    workbook
      .getWorksheet("POS_MASTER")
      .getRangeByIndexes(rowIndex, midx("lastRealVisitDate"), 1, 1)
      .setValue(latest.date);
    workbook
      .getWorksheet("POS_MASTER")
      .getRangeByIndexes(rowIndex, midx("lastRealVisitWeek"), 1, 1)
      .setValue(latest.week);
    workbook
      .getWorksheet("POS_MASTER")
      .getRangeByIndexes(rowIndex, midx("weeksSinceLastVisit"), 1, 1)
      .setValue(weeksSince);
    updated++;
  }

  console.log(
    "Compliance Engine: " + newVisits.length + " new realized visits imported, " +
      complianceRows.length + " compliance rows written (" +
      complianceRows.filter((r) => r[4] == "Navic_evidovano").length + " extra), " +
      updated + " POS_MASTER rows updated with real last-visit data. Reference 'now' = week " +
      latestWeek + "/" + latestYear + "."
  );
}
