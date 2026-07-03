// ============================================================================
// FIELD FORCE OPTIMIZER V11 - START TRACKING ENGINE
// ============================================================================
// Deployable Office Script. Run whenever the manager decides a published
// week's numbers should start counting on the manager dashboards
// (TECHNICIAN_SCORECARD/PERFORMANCE/WEEK_DASHBOARD/HOME).
//
// WHY THIS EXISTS AS ITS OWN EXPLICIT STEP: PublishEngine.ts already sends
// a week's plan to technicians (Draft -> Published), and ComplianceEngine.ts
// already evaluates realized visits against it as soon as SalesApp data
// comes in (Published -> Active -> Closed, fully automatic). Neither of
// those was ever gated on a separate manager decision. Product owner
// (2026-07-06): "abych ho začal sledovat až řeknu já" - publish and
// evaluate can keep happening in the background, but the manager wants to
// be the one who decides when a week's numbers start appearing in the
// dashboards (e.g. while still reviewing a freshly generated/published
// plan, before committing to track it).
//
// SCOPE: sets PLAN_LIFECYCLE.trackingStartedAt (a 6th column, appended -
// see tools/scaffold_workbook.py) to now, for every row whose status is
// Published or Active and whose trackingStartedAt is still blank. Never
// touches Draft (nothing to track yet) or Closed (should already have
// been started earlier in its life; if not, this still picks it up - a
// Closed week with no trackingStartedAt is exactly the "forgot to start
// tracking it" case this engine exists to fix).
//
// DOWNSTREAM EFFECT: PerformanceEngine.ts only includes a (technician,
// year, week) row in TECHNICIAN_PERFORMANCE_LOG/TECHNICIAN_PERFORMANCE_SUMMARY
// if that week's PLAN_LIFECYCLE row has a non-blank trackingStartedAt - see
// that file's header comment. COMPLIANCE_LOG itself is NOT gated by this;
// evaluation keeps happening regardless, only the manager-facing dashboard
// aggregation is held back.
// ============================================================================

function main(workbook: ExcelScript.Workbook) {
  function readTable(sheetName: string): (string | number | boolean)[][] {
    const ws = workbook.getWorksheet(sheetName);
    const range = ws.getUsedRange();
    return range ? range.getValues() : [];
  }

  const planLifecycle = readTable("PLAN_LIFECYCLE");
  if (planLifecycle.length < 2) {
    console.log("Start Tracking Engine: PLAN_LIFECYCLE is empty - nothing to start.");
    return;
  }

  const headers = (planLifecycle[0] as string[]).map((h) => String(h));
  const idx = (name: string) => headers.indexOf(name);
  const statusCol = idx("status");
  const trackingCol = idx("trackingStartedAt");
  if (trackingCol < 0) {
    console.log("Start Tracking Engine: PLAN_LIFECYCLE has no trackingStartedAt column - nothing to do.");
    return;
  }

  const now = new Date().toISOString();
  const plWs = workbook.getWorksheet("PLAN_LIFECYCLE");
  let started: string[] = [];
  for (let i = 1; i < planLifecycle.length; i++) {
    const row = planLifecycle[i];
    const status = String(row[statusCol]);
    const alreadyStarted = String(row[trackingCol] ?? "") !== "";
    if ((status == "Published" || status == "Active" || status == "Closed") && !alreadyStarted) {
      plWs.getRangeByIndexes(i, trackingCol, 1, 1).setValue(now);
      started.push(row[idx("year")] + "/W" + row[idx("week")]);
    }
  }

  console.log(
    started.length > 0
      ? "Start Tracking Engine: started tracking " + started.length + " week(s): " + started.join(", ") + "."
      : "Start Tracking Engine: no Published/Active/Closed week is waiting to start tracking - nothing to do."
  );
}
