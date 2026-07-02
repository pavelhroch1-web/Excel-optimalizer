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
  // SYNC-BLOCK-START: text.ts
  function norm(v: string): string {
    return v
      .toUpperCase()
      .normalize("NFD")
      .replace(/[\u0300-\u036f]/g, "")
      .trim();
  }
  // SYNC-BLOCK-END: text.ts

  // SYNC-BLOCK-START: dates.ts
  function isoMonday(year: number, week: number): Date {
    let d = new Date(year, 0, 4);
    let day = d.getDay();
    if (day == 0) {
      day = 7;
    }
    d.setDate(d.getDate() - day + 1 + (week - 1) * 7);
    return d;
  }

  function easter(y: number): Date {
    const f = Math.floor;
    let a = y % 19;
    let b = f(y / 100);
    let c = y % 100;
    let d = f(b / 4);
    let e = b % 4;
    let g = f((8 * b + 13) / 25);
    let h = (19 * a + b - d - g + 15) % 30;
    let i = f(c / 4);
    let k = c % 4;
    let l = (32 + 2 * e + 2 * i - h - k) % 7;
    let m = f((a + 11 * h + 22 * l) / 451);
    let month = f((h + l - 7 * m + 114) / 31);
    let day = ((h + l - 7 * m + 114) % 31) + 1;
    return new Date(y, month - 1, day);
  }

  // Czech public holidays: 11 fixed dates + Good Friday + Easter Monday.
  function isHoliday(date: Date, year: number): boolean {
    const fixed = [
      "1-1", "1-5", "8-5", "5-7", "6-7", "28-9",
      "28-10", "17-11", "24-12", "25-12", "26-12",
    ];
    const key = date.getDate() + "-" + (date.getMonth() + 1);
    if (fixed.includes(key)) {
      return true;
    }
    const e = easter(year);
    let friday = new Date(e);
    friday.setDate(e.getDate() - 2);
    let monday = new Date(e);
    monday.setDate(e.getDate() + 1);
    return (
      date.toDateString() == friday.toDateString() ||
      date.toDateString() == monday.toDateString()
    );
  }

  // Returns the working (non-holiday) Mon-Fri days for a given ISO week.
  // This is the automatic part of dynamic capacity - CAPACITY_OVERRIDE (a
  // new V11 config table) can still override the resulting day/visit count
  // manually; see docs/BUSINESS_RULES.md section 8.
  function workDays(year: number, week: number): { day: string; date: Date }[] {
    const names = ["MON", "TUE", "WED", "THU", "FRI"];
    let start = isoMonday(year, week);
    let result: { day: string; date: Date }[] = [];
    for (let i = 0; i < 5; i++) {
      let d = new Date(start);
      d.setDate(start.getDate() + i);
      if (!isHoliday(d, year)) {
        result.push({ day: names[i], date: d });
      }
    }
    return result;
  }
  // SYNC-BLOCK-END: dates.ts

  // SYNC-BLOCK-START: core.ts (compliance)
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
  function weeksBetween(week1: number, year1: number, week2: number, year2: number): number {
    return week2 - week1 + (year2 - year1) * 52;
  }

  type ComplianceStatus =
    | "Splneno_vcas"
    | "Splneno_pozde"
    | "Nesplneno"
    | "Pending";

  function determineComplianceStatus(
    plannedWeek: number,
    plannedYear: number,
    actualWeeks: { week: number; year: number }[],
    lateCutoffWeeks: number,
    latestKnownWeek: number,
    latestKnownYear: number
  ): ComplianceStatus {
    if (actualWeeks.length === 0) {
      const elapsed = weeksBetween(plannedWeek, plannedYear, latestKnownWeek, latestKnownYear);
      if (elapsed > lateCutoffWeeks) {
        return "Nesplneno";
      }
      return "Pending";
    }
    const earliest = actualWeeks.reduce((min, w) =>
      weeksBetween(plannedWeek, plannedYear, w.week, w.year) <
      weeksBetween(plannedWeek, plannedYear, min.week, min.year)
        ? w
        : min
    );
    const delta = weeksBetween(plannedWeek, plannedYear, earliest.week, earliest.year);
    if (delta <= 0) {
      return "Splneno_vcas";
    }
    return "Splneno_pozde"; // late is still late even beyond lateCutoffWeeks -
    // it happened, so it is not "Nesplneno" (which means it never happened)
  }

  type PlanStatus = "Draft" | "Published" | "Active" | "Closed";

  function advanceLifecycleStatus(
    current: PlanStatus,
    mondayHasPassed: boolean,
    hasPendingVisits: boolean
  ): PlanStatus {
    if (current == "Closed") {
      return "Closed"; // terminal - a closed week is never reopened
    }
    if (current == "Draft") {
      return "Draft"; // only PublishEngine.ts moves Draft -> Published
    }
    // current is Published or Active: closing (no visits still Pending) takes
    // priority over the Published/Active distinction, which is otherwise only
    // about whether the week has chronologically started yet.
    if (!hasPendingVisits) {
      return "Closed";
    }
    if (current == "Active") {
      return "Active"; // monotonic - a week that already reached Active can
      // never have mondayHasPassed become false again (time doesn't run
      // backward), so never regress it to Published even if called with an
      // inconsistent mondayHasPassed value.
    }
    return mondayHasPassed ? "Active" : "Published";
  }
  // SYNC-BLOCK-END: core.ts (compliance)

  // ==========================================================================
  // LOAD SHEETS
  // ==========================================================================

  function readTable(sheetName: string): (string | number | boolean)[][] {
    const ws = workbook.getWorksheet(sheetName);
    const range = ws.getUsedRange();
    return range ? range.getValues() : [];
  }

  const salesApp = readTable("SALESAPP_IMPORT");
  // Compliance always compares against the immutable Published snapshot,
  // never against the freely-regenerated MANAGER_PLAN Draft - see
  // docs/BUSINESS_RULES.md section 11 and PublishEngine.ts.
  const managerPlanPublished = readTable("MANAGER_PLAN_PUBLISHED");
  const control = readTable("CONTROL");
  const visitHistoryActual = readTable("VISIT_HISTORY_ACTUAL");
  const posMaster = readTable("POS_MASTER");
  const planLifecycle = readTable("PLAN_LIFECYCLE");

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
  // PlanningEngine.ts/PublishEngine.ts generate WEEK as a YEAR-anchored
  // offset (isoMonday(CONTROL.YEAR, week)) that can exceed 52/53 once a
  // campaign's week numbers roll into the next real year - PLAN_LIFECYCLE's
  // own (year, week) key is written using this same flat CONTROL.YEAR
  // convention (see PublishEngine.ts), not a true per-row ISO year. Needed
  // here ONLY to reconstruct that exact same raw key for matching against
  // PLAN_LIFECYCLE below - NOT used for compliance classification itself,
  // which uses the true per-row ISO week/year derived from DATE instead
  // (see "MATCH MANAGER_PLAN_PUBLISHED -> COMPLIANCE_LOG" below).
  const CONTROL_YEAR = setting("YEAR", new Date().getFullYear());

  if (salesApp.length < 2) {
    console.log("Compliance Engine: SALESAPP_IMPORT is empty, nothing to do.");
    return;
  }
  if (managerPlanPublished.length < 2) {
    console.log("Compliance Engine: MANAGER_PLAN_PUBLISHED is empty - run Planning Engine then Publish Engine first.");
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
  // MATCH MANAGER_PLAN_PUBLISHED -> COMPLIANCE_LOG
  // ==========================================================================

  const mpHeaders = (managerPlanPublished[0] as string[]).map((h) => String(h));
  const mpIdx = (name: string) => mpHeaders.indexOf(name);
  const cWeek = mpIdx("WEEK");
  const cDate2 = mpIdx("DATE");
  const cPos2 = mpIdx("POS");
  const cTech2 = mpIdx("TECHNICIAN");

  // One planned row per (posId, isoWeek, isoYear), even if the published
  // snapshot has that POS only once per week (it should - Planning Engine
  // doesn't double-book a POS in the same week - but de-duplicate
  // defensively rather than assume).
  //
  // week/year here are the TRUE ISO week/year derived from this row's own
  // DATE column via isoWeekNumber() - NOT the raw WEEK column value, which
  // is a YEAR-anchored offset from PlanningEngine.ts that can exceed 52/53
  // once a campaign's weeks roll into the next real year (e.g. week 54 of a
  // "2026" anchor is actually early January 2027). Using the true per-row
  // ISO pair here is what makes this comparable, apples-to-apples, against
  // actual visit weeks below (also derived via isoWeekNumber() from real
  // SalesApp dates) - previously this used a single flat "plannedYear" for
  // every row in the whole run (guessed from the newest SalesApp import),
  // which silently misclassified compliance for any published week that
  // actually falls in a different real year than that guess. rawWeek is
  // kept separately (see PLAN_LIFECYCLE matching below) since that table's
  // own key uses the raw, not the true-ISO, convention.
  let plannedSet: {
    [key: string]: { posId: string; week: number; year: number; rawWeek: number; tech: string };
  } = {};
  for (let i = 1; i < managerPlanPublished.length; i++) {
    const row = managerPlanPublished[i];
    const posId = String(row[cPos2]);
    const rawWeek = Number(row[cWeek]);
    const dateVal = row[cDate2];
    if (!posId || !rawWeek || !(dateVal instanceof Date)) {
      continue;
    }
    const { week, year } = isoWeekNumber(dateVal);
    plannedSet[posId + "|" + week + "|" + year] = { posId, week, year, rawWeek, tech: String(row[cTech2]) };
  }

  let complianceRows: (string | number)[][] = [];
  const now = new Date().toISOString();
  const matchedPlannedKeys = new Set<string>();
  // Keyed by PLAN_LIFECYCLE's own raw (CONTROL_YEAR, rawWeek) convention -
  // deliberately NOT the true-ISO (planned.year, planned.week) pair above,
  // since that is what PublishEngine.ts actually wrote as that row's
  // PLAN_LIFECYCLE key. Used below to advance PLAN_LIFECYCLE only.
  let pendingByRawWeek: { [key: string]: boolean } = {};

  for (const key of Object.keys(plannedSet)) {
    const planned = plannedSet[key];
    matchedPlannedKeys.add(key);
    const actuals = (actualByPos[planned.posId] || []).map((a) => ({ week: a.week, year: a.year }));
    const status = determineComplianceStatus(planned.week, planned.year, actuals, LATE_CUTOFF, latestWeek, latestYear);
    const matched = (actualByPos[planned.posId] || []).find((a) => a.week == planned.week && a.year == planned.year);
    complianceRows.push([
      planned.posId, planned.tech, planned.week, planned.year, status,
      matched ? matched.date : "", matched ? matched.week : "", now,
    ]);
    const rawKey = CONTROL_YEAR + "|" + planned.rawWeek;
    if (status == "Pending") {
      pendingByRawWeek[rawKey] = true;
    } else if (!(rawKey in pendingByRawWeek)) {
      pendingByRawWeek[rawKey] = false;
    }
  }

  // Extra visits: actual visits to a POS in a week where that POS was not
  // planned at all (per BUSINESS_RULES.md section 12: neutral, logged only).
  for (const posId of Object.keys(actualByPos)) {
    for (const a of actualByPos[posId]) {
      const key = posId + "|" + a.week + "|" + a.year;
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
  // ADVANCE PLAN LIFECYCLE (Published -> Active -> Closed, mechanical -
  // see docs/BUSINESS_RULES.md section 11. Draft -> Published only happens
  // in PublishEngine.ts, never here.)
  // ==========================================================================

  if (planLifecycle.length >= 2) {
    const plHeaders = (planLifecycle[0] as string[]).map((h) => String(h));
    const plIdx = (name: string) => plHeaders.indexOf(name);
    const today = new Date();

    for (let i = 1; i < planLifecycle.length; i++) {
      const row = planLifecycle[i];
      const year = Number(row[plIdx("year")]);
      const week = Number(row[plIdx("week")]);
      const current = String(row[plIdx("status")]) as PlanStatus;
      const key = year + "|" + week;
      if (!(key in pendingByRawWeek)) {
        continue; // no compliance data for this week yet - nothing to advance
      }
      const mondayHasPassed = isoMonday(year, week) <= today;
      const next = advanceLifecycleStatus(current, mondayHasPassed, pendingByRawWeek[key]);
      if (next != current) {
        workbook.getWorksheet("PLAN_LIFECYCLE").getRangeByIndexes(i, 2, 1, 1).setValue(next);
        if (next == "Closed") {
          workbook.getWorksheet("PLAN_LIFECYCLE").getRangeByIndexes(i, 4, 1, 1).setValue(now);
        }
      }
    }
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
