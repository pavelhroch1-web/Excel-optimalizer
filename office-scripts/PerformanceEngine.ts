// ============================================================================
// FIELD FORCE OPTIMIZER V11 - PERFORMANCE ENGINE
// ============================================================================
// Deployable Office Script. Run any time after Compliance Engine (needs
// COMPLIANCE_LOG populated; works with an empty COMPLIANCE_LOG too - just
// produces zero rows, not an error).
//
// COMPUTES NOTHING NEW - same principle as ReportingEngine.ts (see its file
// header): pure aggregation of numbers already decided by PlanningEngine.ts/
// ComplianceEngine.ts. This engine's only job is turning the append-only,
// per-visit COMPLIANCE_LOG and MANAGER_PLAN_PUBLISHED into ONE compact
// summary row per (technician, ISO year, ISO week) - the data foundation for
// the manager UX layer (docs/MANAGER_UX_ARCHITECTURE.md section 1). No new
// business rule, no compliance classification, no capacity decision - if a
// number here looks wrong, the bug is upstream.
//
// WHY A SEPARATE ENGINE FROM ReportingEngine.ts: ReportingEngine.ts already
// builds DASHBOARD's network-wide summary; this is a different grain
// (per-technician-per-week, not "latest snapshot") feeding a different set
// of screens (docs/MANAGER_UX_ARCHITECTURE.md), and keeping it separate
// avoids growing ReportingEngine.ts (already ~700 lines) into an unrelated
// second responsibility.
//
// SCOPE:
//   - plannedVisits: count of MANAGER_PLAN_PUBLISHED rows per (technician,
//     ISO week/year derived from DATE - NOT the raw WEEK column, which is
//     PlanningEngine's campaign-relative offset and can exceed 52/53 near a
//     year boundary; using true ISO week/year here keeps this consistent
//     with COMPLIANCE_LOG's own plannedWeek/plannedYear convention, fixed
//     for the same reason in ComplianceEngine.ts - see ARCHITECTURE.md
//     section 21).
//   - realizedVisits / splnenoVcas / splnenoPozde / nesplneno / navicEvidovano:
//     counts from COMPLIANCE_LOG, DEDUPED to the latest evaluation per
//     (posId, plannedWeek, plannedYear) first - COMPLIANCE_LOG is append-only
//     and re-evaluates every still-open visit on every Compliance Engine run,
//     so counting raw rows would double/triple-count the same visit (the
//     exact bug already found and fixed once in AdvisorEngine.ts - see
//     ARCHITECTURE.md Phase 6 notes; latestByKey() is the same fix reused
//     here).
//   - Navic_evidovano rows have NO technician in COMPLIANCE_LOG (that visit
//     was never planned for anyone, so ComplianceEngine.ts has nothing to
//     attribute it to) - resolved here via POS_MASTER's assignedTechnician/
//     managerOverrideTechnician for that POS, purely for this aggregation.
//     Informational only; does not change COMPLIANCE_LOG or any compliance
//     classification.
//   - compliancePercent: realizedVisits / plannedVisits (0 when plannedVisits
//     is 0, not a divide-by-zero error).
//   - visitsMon..visitsFri: weekday of COMPLIANCE_LOG's matchedActualDate for
//     realized (Splneno_vcas/Splneno_pozde) rows only - the only place a
//     resolved technician identity is reliably tied to a real visit date
//     (VISIT_HISTORY_ACTUAL's own "executor" field is the raw, incompatible
//     SalesApp name - see ComplianceEngine.ts's file header for why that's
//     never used for technician identity).
//   - region: the most common POS_MASTER.area among that technician's
//     planned POS that week (informational tie-break: first area seen wins
//     ties, not a business rule).
//
// SECOND OUTPUT - TECHNICIAN_PERFORMANCE_SUMMARY: one row per technician -
// their most recent week's numbers, a long-run average compliance, and a
// trend delta vs. the previous week on record (technician, region,
// latestYear, latestWeek, plannedVisits, realizedVisits, splnenoVcas,
// splnenoPozde, nesplneno, navicEvidovano, compliancePercent,
// longRunAvgCompliance, trendDelta), bounded technicians x 1 row, full
// rebuild every run. Feeds the PERFORMANCE comparison screen - a real
// native Excel Table/AutoFilter (product owner, 2026-07-05: prefer native
// Excel Table+AutoFilter over a custom filter UI - see
// tools/ux_style.py's build_performance_sheet()). Derived from the SAME
// buckets already used above, not a second read of anything.
//
// THIRD OUTPUT - TECHNICIAN_TOP_ISSUES: top 5 all-time Nesplneno POS per
// technician (technician, rank, posId, posName, region, nesplnenoCount),
// bounded technicians x 5 rows, full rebuild every run. Feeds
// TECHNICIAN_SCORECARD's "TOP problematic POS" tile. Deliberately computed
// here from the already-deduped compliance rows (same latestByKey() pass
// used above), NOT as an Excel formula over raw COMPLIANCE_LOG as originally
// sketched in docs/MANAGER_UX_ARCHITECTURE.md section 4 - a formula-side
// COUNTIFS over the raw append-only log would hit the exact double-counting
// bug this file exists to avoid (see above), so this stays inside the
// already-tested engine instead of being re-derived in the presentation
// layer.
//
// FOURTH THING THIS ENGINE NOW DOES - TRACKING GATE: a (technician, year,
// week) bucket is only included in ANY output (TECHNICIAN_PERFORMANCE_LOG,
// TECHNICIAN_PERFORMANCE_SUMMARY, TECHNICIAN_TOP_ISSUES) if that week's
// PLAN_LIFECYCLE row has a non-blank trackingStartedAt - see
// StartTrackingEngine.ts's file header. Publish and Compliance evaluation
// both keep working exactly as before regardless of this gate; only the
// manager-dashboard aggregation is held back until the manager explicitly
// starts tracking that week (product owner, 2026-07-06: "abych ho začal
// sledovat až řeknu já"). PLAN_LIFECYCLE keys weeks by (CONTROL_YEAR,
// rawWeek) - the same YEAR-anchored offset convention already reconciled
// against true-ISO year/week in ComplianceEngine.ts (see that file's own
// CONTROL_YEAR comment) - so the same reconciliation is repeated here,
// built once per (technician,true-ISO-week) while walking
// MANAGER_PLAN_PUBLISHED below.
//
// FIFTH OUTPUT ADDITION - route efficiency (kmMon..kmFri on
// TECHNICIAN_PERFORMANCE_LOG): total straight-line driving distance between
// consecutive REALIZED visits for each weekday, using POS_MASTER's GPS
// coordinates and the same distanceKm() flat-earth approximation
// PlanningEngine.ts already uses for GPS clustering (product owner,
// 2026-07-06: "kolik najel km" + "semafor"). This is an ESTIMATE, not a
// real recorded route: there is no visit-time-of-day data anywhere in this
// system, so the visiting ORDER within a day is assumed to match
// PlanningEngine.ts's own planned sequence for that technician/date (the
// order its GPS-clustering already decided) - any realized visit to a POS
// that wasn't in that day's plan (Navic_evidovano) is appended at the end,
// sorted by posId for determinism. The severity coloring ("semafor")
// thresholds live in CONTROL (ROUTE_KM_WARNING_KM/ROUTE_KM_CRITICAL_KM,
// proposed defaults - see docs/BACKLOG.md) and are applied in
// tools/ux_style.py, not here - this engine only computes the raw km.
//
// SIXTH OUTPUT ADDITION - otherVisits: count of Completed/Finalized SalesApp
// visits whose purpose was NOT the campaign ("MCHD - Nabeh kampane") signal
// - real visits (restocking, lottery ticket downloads, etc.) that never
// count toward compliance (see ComplianceEngine.ts's file header), logged to
// OTHER_VISIT_LOG and aggregated here purely as manager context, alongside
// the campaign-visit numbers. Product owner (2026-07-06), after reviewing
// the real SalesApp export with the assistant: "Merch" and "Visibility" are
// the SAME single MCHD-Nabeh-kampane signal already used for compliance, not
// two separate breakdowns - this otherVisits count is the actual remaining
// ask ("dalsi navstevy jsou typu ostatni"). Technician attribution uses the
// same (posId, true-ISO week/year) -> planned technician lookup as the
// tracking-gate/route-km logic above, falling back to POS_MASTER's current
// assignment (same pattern as Navic_evidovano) when a POS wasn't planned
// that week; gated by the same tracking-started check as everything else.
//
// SEVENTH OUTPUT ADDITION - posListMon..posListFri: the actual list of that
// day's realized POS ("id - name", comma-separated), in the same order
// orderedPosForDay() computed the km estimate from. Product owner
// (2026-07-06), after seeing the daily km/visit-count breakdown: "na tady mě
// to zajímá až na dny, zda jezdil efektivně, kolik jich udělal a pos" -
// wanted the concrete POS list per day, not just a count, so a manager can
// see WHAT a technician actually did that day, not only how many.
// Informational only, same tracking gate as everything else.
//
// EIGHTH OUTPUT ADDITION - badWeeksInWindow / flakaRiziko on
// TECHNICIAN_PERFORMANCE_SUMMARY: a persistent-underperformance flag.
// Product owner (2026-07-06), reviewing the manager screens: "chci aby mi
// to ukazalo ktery z nich flaka a ktery ne" - explicitly scoped to the
// TECHNICIAN only (not a POS-level systemic-vs-personal split, which was
// considered and declined - "me zajima technik ne POS"). Counts how many of
// a technician's last CONTROL.FLAKANI_WINDOW_WEEKS tracked weeks had
// compliancePercent below CONTROL.FLAKANI_BAD_WEEK_THRESHOLD_PERCENT;
// flakaRiziko = "Ano" only once at least CONTROL.FLAKANI_BAD_WEEKS_COUNT of
// those were bad - deliberately requires a repeated pattern (confirmed
// definition: 2+ of the last 4 weeks), not a single bad week, so one rough
// week (illness, an unusually hard week) doesn't mislabel someone. Displayed
// on PERFORMANCE (the network-wide comparison screen - the natural place to
// scan "who's slacking" across the whole team) and as a status badge on
// TECHNICIAN_SCORECARD.
//
// NINTH OUTPUT ADDITION - monthKey (YYYYMM) on TECHNICIAN_PERFORMANCE_LOG:
// the calendar month of each (technician, ISO week) row, computed via
// isoMonday() + JS Date arithmetic rather than an Excel formula approximating
// week-to-month conversion (this project already treats that class of
// boundary math as an engine responsibility, not a spreadsheet one). Feeds
// TECHNICIAN_SCORECARD's long-term monthly trend chart - product owner
// (2026-07-06), after the weekly/4-week views above: "je pro mě i důležitý
// dlouhodobý pohled" - wants compliance trend across months/campaigns, not
// just the last 6 weeks.
//
// TENTH OUTPUT ADDITION - maxKmDay on TECHNICIAN_PERFORMANCE_SUMMARY: the
// worst single day's route-km (see routeKmForDay above) in a technician's
// most recent tracked week. Found missing during a final full test pass
// (2026-07-06): route efficiency (km + semafor) only ever existed
// per-technician on TECHNICIAN_SCORECARD - there was no way to scan the
// whole team at once for who had a bad day, unlike compliance/flaka-riziko
// which are both visible on the network-wide PERFORMANCE screen. Same
// tracking-gate as everything else.
//
// NOT IN THIS VERSION: WHICH LOS/LOT campaign a visit serviced (still
// blocked on ambiguous free-text data - see BUSINESS_RULES.md section 12).
// GPS-based map data remains deferred too.
// ============================================================================

function main(workbook: ExcelScript.Workbook) {
  // SYNC-BLOCK-START: core.ts (performance)
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

  interface TimestampedRow {
    key: string;
    timestamp: string; // ISO string, lexicographically comparable
  }
  function latestByKey<T extends TimestampedRow>(rows: T[]): T[] {
    let latest: { [key: string]: T } = {};
    for (const row of rows) {
      if (!latest[row.key] || row.timestamp > latest[row.key].timestamp) {
        latest[row.key] = row;
      }
    }
    return Object.values(latest);
  }

  interface GeoPoint {
    x: number;
    y: number;
  }

  // Fallback for computeOptimalRouteKm() when there are too many points for
  // the exact Held-Karp DP to stay cheap (see that function's comment) - tries
  // a nearest-neighbor construction from every possible starting point and
  // keeps the shortest one found. Deterministic, not guaranteed optimal, but a
  // reasonable approximation rather than refusing to compute anything.
  function nearestNeighborRouteKm(points: GeoPoint[]): number {
    const n = points.length;
    let best = Infinity;
    for (let start = 0; start < n; start++) {
      const visited: boolean[] = new Array(n).fill(false);
      visited[start] = true;
      let total = 0;
      let current = start;
      for (let step = 1; step < n; step++) {
        let nearest = -1;
        let nearestDist = Infinity;
        for (let k = 0; k < n; k++) {
          if (visited[k]) {
            continue;
          }
          const d = distanceKm(points[current].x, points[current].y, points[k].x, points[k].y);
          if (d < nearestDist) {
            nearestDist = d;
            nearest = k;
          }
        }
        total += nearestDist;
        visited[nearest] = true;
        current = nearest;
      }
      if (total < best) {
        best = total;
      }
    }
    return Math.round(best * 10) / 10;
  }

  // The "matematicke minimum" (product owner, 2026-07-09) a technician's
  // realized route for one day is compared against: the shortest possible
  // OPEN path (free start, free end - there is no known depot, see
  // geoDays()'s own comment on why a fixed start point is deliberately not
  // assumed) visiting every one of that day's GPS-resolvable stops exactly
  // once. Exact Held-Karp dynamic program, multi-source (dp[mask][j] =
  // shortest path visiting exactly the stops in `mask`, ending at stop j,
  // having started anywhere within `mask` - base case dp[{i}][i] = 0 for every
  // i, since any single stop could be the start) - O(2^n * n^2), exact and
  // cheap for n up to ~13 (a realistic daily visit count given
  // TARGET_VISITS_DAY plus GPS bonus overflow); falls back to
  // nearestNeighborRouteKm() beyond that rather than growing exponentially
  // unbounded.
  function computeOptimalRouteKm(points: GeoPoint[]): number {
    const n = points.length;
    if (n < 2) {
      return 0;
    }
    if (n > 13) {
      return nearestNeighborRouteKm(points);
    }
    const dist: number[][] = [];
    for (let i = 0; i < n; i++) {
      dist.push([]);
      for (let j = 0; j < n; j++) {
        dist[i].push(distanceKm(points[i].x, points[i].y, points[j].x, points[j].y));
      }
    }
    const full = 1 << n;
    const INF = Infinity;
    let dp: number[][] = [];
    for (let mask = 0; mask < full; mask++) {
      dp.push(new Array(n).fill(INF));
    }
    for (let i = 0; i < n; i++) {
      dp[1 << i][i] = 0;
    }
    for (let mask = 1; mask < full; mask++) {
      for (let j = 0; j < n; j++) {
        if (!(mask & (1 << j)) || dp[mask][j] == INF) {
          continue;
        }
        for (let k = 0; k < n; k++) {
          if (mask & (1 << k)) {
            continue;
          }
          const nextMask = mask | (1 << k);
          const candidate = dp[mask][j] + dist[j][k];
          if (candidate < dp[nextMask][k]) {
            dp[nextMask][k] = candidate;
          }
        }
      }
    }
    let best = INF;
    for (let j = 0; j < n; j++) {
      if (dp[full - 1][j] < best) {
        best = dp[full - 1][j];
      }
    }
    return Math.round(best * 10) / 10;
  }
  // SYNC-BLOCK-END: core.ts (performance)

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

  // SYNC-BLOCK-START: geo.ts
  function distanceKm(ax: number, ay: number, bx: number, by: number): number {
    const dx = (ax - bx) * 111;
    const dy = (ay - by) * 72;
    return Math.sqrt(dx * dx + dy * dy);
  }
  // SYNC-BLOCK-END: geo.ts

  function readTable(sheetName: string): (string | number | boolean)[][] {
    const ws = workbook.getWorksheet(sheetName);
    const range = ws.getUsedRange();
    return range ? range.getValues() : [];
  }

  const posMaster = readTable("POS_MASTER");
  const managerPlanPublished = readTable("MANAGER_PLAN_PUBLISHED");
  const complianceLog = readTable("COMPLIANCE_LOG");
  const otherVisitLog = readTable("OTHER_VISIT_LOG");
  const control = readTable("CONTROL");
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
  // Same YEAR-anchored raw-week convention as ComplianceEngine.ts's own
  // CONTROL_YEAR (see that file's header comment) - needed here only to
  // reconstruct PLAN_LIFECYCLE's own (year, rawWeek) key for the tracking
  // gate, not for compliance classification.
  const CONTROL_YEAR = setting("YEAR", new Date().getFullYear());
  // "Flaka riziko" flag settings (see EIGHTH OUTPUT ADDITION below).
  const FLAKANI_WINDOW_WEEKS = setting("FLAKANI_WINDOW_WEEKS", 4);
  const FLAKANI_BAD_WEEK_THRESHOLD_PERCENT = setting("FLAKANI_BAD_WEEK_THRESHOLD_PERCENT", 70);
  const FLAKANI_BAD_WEEKS_COUNT = setting("FLAKANI_BAD_WEEKS_COUNT", 2);
  // Monitoring efektivity (product owner, 2026-07-09): actual-vs-optimal
  // route km ratio thresholds - "o 50 %+ vyšší než optimum" was given
  // explicitly as the CRITICAL bar (>=150%), WARNING set at a milder 125%
  // (product owner's own "cik-cak" framing implies WARNING should trigger
  // meaningfully before the hard 50%-over line, not right at it).
  const ROUTE_EFFICIENCY_WARNING_PERCENT = setting("ROUTE_EFFICIENCY_WARNING_PERCENT", 125);
  const ROUTE_EFFICIENCY_CRITICAL_PERCENT = setting("ROUTE_EFFICIENCY_CRITICAL_PERCENT", 150);
  // "Manažerské" triggers (product owner, 2026-07-09, speaking explicitly as
  // vedoucí Field Force týmu): below-peer volume/value/duration signals,
  // each expressed as "% of the network/own average" - below
  // *_WARNING_PERCENT is a soft flag, below *_CRITICAL_PERCENT is a hard
  // one, same convention as ROUTE_EFFICIENCY above but inverted (LOW is bad
  // here, not high).
  const VOLUME_WARNING_PERCENT = setting("VOLUME_WARNING_PERCENT", 70);
  const VOLUME_CRITICAL_PERCENT = setting("VOLUME_CRITICAL_PERCENT", 50);
  const PPT_DENSITY_WARNING_PERCENT = setting("PPT_DENSITY_WARNING_PERCENT", 70);
  const PPT_DENSITY_CRITICAL_PERCENT = setting("PPT_DENSITY_CRITICAL_PERCENT", 50);
  const DURATION_WARNING_PERCENT = setting("DURATION_WARNING_PERCENT", 70);
  const DURATION_CRITICAL_PERCENT = setting("DURATION_CRITICAL_PERCENT", 50);
  // How many independently corroborating signals are required before the
  // automatic "problémový technik" callouts (HOME/EFFICIENCY) surface a
  // name - product owner: efficiency ratio alone should never be enough
  // ("GPS je odhad, takže to ani nemusí být na vinu"), a real conversation
  // needs at least this many flags pointing the same direction at once.
  const PROBLEM_SIGNAL_MIN_COUNT = setting("PROBLEM_SIGNAL_MIN_COUNT", 2);

  // ==========================================================================
  // PLAN_LIFECYCLE -> which (CONTROL_YEAR, rawWeek) weeks has the manager
  // explicitly started tracking (StartTrackingEngine.ts sets this - see
  // file header above).
  // ==========================================================================

  let trackingStartedRawWeeks: { [key: string]: boolean } = {};
  // True-ISO equivalent of trackingStartedRawWeeks, computed directly from
  // PLAN_LIFECYCLE's own raw (year, rawWeek) key - independent of whether
  // any MANAGER_PLAN_PUBLISHED row for that week happens to exist. BUG FIX
  // (found 2026-07-06 during a full test pass): this used to be built
  // opportunistically inside the MANAGER_PLAN_PUBLISHED loop below, so a week
  // with a manager-started PLAN_LIFECYCLE row but ZERO planned visits (e.g.
  // a week made up entirely of Navic_evidovano/OTHER_VISIT_LOG activity)
  // would never be marked as tracking-started, silently hiding that week's
  // Nesplneno/otherVisits from every manager dashboard even after the
  // manager explicitly started tracking it.
  let trueIsoTrackingStarted: { [key: string]: boolean } = {};
  if (planLifecycle.length >= 2) {
    const plHeaders = (planLifecycle[0] as string[]).map((h) => String(h));
    const plIdx = (name: string) => plHeaders.indexOf(name);
    const trackingCol = plIdx("trackingStartedAt");
    if (trackingCol >= 0) {
      for (let i = 1; i < planLifecycle.length; i++) {
        const row = planLifecycle[i];
        if (String(row[trackingCol] ?? "") !== "") {
          const rawYear = Number(row[plIdx("year")]);
          const rawWeek = Number(row[plIdx("week")]);
          trackingStartedRawWeeks[rawYear + "|" + rawWeek] = true;
          const { week, year } = isoWeekNumber(isoMonday(rawYear, rawWeek));
          trueIsoTrackingStarted[year + "|" + week] = true;
        }
      }
    }
  }

  // ==========================================================================
  // POS_MASTER -> posId -> {area, technician} lookup (region info + fallback
  // technician attribution for Navic_evidovano rows)
  // ==========================================================================

  const pmHeaders = posMaster.length > 0 ? (posMaster[0] as string[]).map((h) => String(h)) : [];
  const pmIdx = (name: string) => pmHeaders.indexOf(name);
  let posArea: { [posId: string]: string } = {};
  let posTechnician: { [posId: string]: string } = {};
  let posName: { [posId: string]: string } = {};
  let posGps: { [posId: string]: { x: number; y: number } } = {};
  // PPT lookup (product owner, 2026-07-09, "Monitoring efektivity" - hodnotová
  // hustota) - a technician with great route-km efficiency but visiting only
  // low-value POS is a different problem than a bad route shape, and route
  // efficiency alone cannot see it.
  let posPpt: { [posId: string]: number } = {};
  for (let i = 1; i < posMaster.length; i++) {
    const row = posMaster[i];
    const posId = String(row[pmIdx("posId")]);
    if (!posId) {
      continue;
    }
    posArea[posId] = String(row[pmIdx("area")] ?? "");
    posName[posId] = String(row[pmIdx("nazev")] ?? "");
    const override = String(row[pmIdx("managerOverrideTechnician")] ?? "");
    posTechnician[posId] = override || String(row[pmIdx("assignedTechnician")] ?? "");
    posPpt[posId] = Number(row[pmIdx("ppt")]) || 0;
    const gpsX = Number(row[pmIdx("gpsX")]);
    const gpsY = Number(row[pmIdx("gpsY")]);
    if (!isNaN(gpsX) && !isNaN(gpsY) && (gpsX != 0 || gpsY != 0)) {
      posGps[posId] = { x: gpsX, y: gpsY };
    }
  }

  // ==========================================================================
  // Aggregation bucket, one per (technician, year, week)
  // ==========================================================================

  interface Bucket {
    technician: string;
    year: number;
    week: number;
    areaCounts: { [area: string]: number };
    plannedVisits: number;
    realizedVisits: number;
    splnenoVcas: number;
    splnenoPozde: number;
    nesplneno: number;
    navicEvidovano: number;
    otherVisits: number;
    visitsByDay: number[]; // [Mon, Tue, Wed, Thu, Fri] - planned/campaign visits
    otherVisitsByDay: number[]; // [Mon, Tue, Wed, Thu, Fri] - ad-hoc (OTHER_VISIT_LOG) visits, product owner 2026-07-09: "denní statistiky - plnění vs. neplnění"
    possByDay: string[][]; // realized posIds per weekday, unsorted until output time
    realizedPptSum: number; // sum of posPpt over realized (Splneno_vcas/pozde) POS this week - hodnotová hustota
    durationHoursSum: number; // sum of matchedActualDurationHours over realized visits with a known duration
    durationKnownCount: number; // how many of those realized visits actually carried a duration value
    // Real work-day span/idle time (product owner, 2026-07-11: "není ten
    // salesapp pořádně vytěžený") - combines BOTH campaign (COMPLIANCE_LOG)
    // and ad-hoc (OTHER_VISIT_LOG) visits per day, since idle time should
    // reflect the whole day in the field, not just campaign-purpose stops.
    dayFirstStart: (Date | null)[]; // [Mon..Fri] earliest startedAt that day
    dayLastFinish: (Date | null)[]; // [Mon..Fri] latest finishedAt that day
    dayActiveHoursSum: number[]; // [Mon..Fri] sum of durationHours that day (visits with a known duration)
  }
  let buckets: { [key: string]: Bucket } = {};

  function bucketFor(technician: string, year: number, week: number): Bucket {
    const key = technician + "|" + year + "|" + week;
    if (!buckets[key]) {
      buckets[key] = {
        technician,
        year,
        week,
        areaCounts: {},
        plannedVisits: 0,
        realizedVisits: 0,
        splnenoVcas: 0,
        splnenoPozde: 0,
        nesplneno: 0,
        navicEvidovano: 0,
        otherVisits: 0,
        visitsByDay: [0, 0, 0, 0, 0],
        otherVisitsByDay: [0, 0, 0, 0, 0],
        possByDay: [[], [], [], [], []],
        realizedPptSum: 0,
        durationHoursSum: 0,
        durationKnownCount: 0,
        dayFirstStart: [null, null, null, null, null],
        dayLastFinish: [null, null, null, null, null],
        dayActiveHoursSum: [0, 0, 0, 0, 0],
      };
    }
    return buckets[key];
  }

  // Records one visit's real clock timing into a bucket-day's running
  // first-start/last-finish/active-time trackers (product owner, 2026-07-11)
  // - shared by both the campaign-visit (COMPLIANCE_LOG) and ad-hoc
  // (OTHER_VISIT_LOG) loops below, since a technician's real work day
  // includes both kinds of stop.
  function recordDayTiming(
    bucket: Bucket,
    dayIdx: number,
    startedAt: Date | null,
    finishedAt: Date | null,
    durationHours: number | null
  ): void {
    if (startedAt && (!bucket.dayFirstStart[dayIdx] || startedAt < (bucket.dayFirstStart[dayIdx] as Date))) {
      bucket.dayFirstStart[dayIdx] = startedAt;
    }
    if (finishedAt && (!bucket.dayLastFinish[dayIdx] || finishedAt > (bucket.dayLastFinish[dayIdx] as Date))) {
      bucket.dayLastFinish[dayIdx] = finishedAt;
    }
    if (durationHours !== null) {
      bucket.dayActiveHoursSum[dayIdx] += durationHours;
    }
  }

  // ==========================================================================
  // MANAGER_PLAN_PUBLISHED -> plannedVisits + region tally (tracking-gated),
  // plus the per-(technician,date) planned visiting order used later for the
  // route-efficiency estimate (see file header).
  // ==========================================================================

  const mpHeaders = managerPlanPublished.length > 0 ? (managerPlanPublished[0] as string[]).map((h) => String(h)) : [];
  const mpIdx = (name: string) => mpHeaders.indexOf(name);
  let plannedOrderByTechDate: { [key: string]: string[] } = {};
  // (posId, true-ISO week, true-ISO year) -> the technician that POS was
  // planned for that week - used to attribute OTHER_VISIT_LOG rows (which
  // carry no technician of their own) to a technician below, same idea as
  // ComplianceEngine.ts's plannedSet.
  let plannedTechByPosWeek: { [key: string]: string } = {};
  for (let i = 1; i < managerPlanPublished.length; i++) {
    const row = managerPlanPublished[i];
    const tech = String(row[mpIdx("TECHNICIAN")] ?? "");
    const posId = String(row[mpIdx("POS")] ?? "");
    const dateVal = row[mpIdx("DATE")];
    if (!tech || !(dateVal instanceof Date)) {
      continue;
    }
    const dateKey = dateVal.toISOString().slice(0, 10);
    const orderKey = tech + "|" + dateKey;
    if (!plannedOrderByTechDate[orderKey]) {
      plannedOrderByTechDate[orderKey] = [];
    }
    plannedOrderByTechDate[orderKey].push(posId);

    const { week, year } = isoWeekNumber(dateVal);
    plannedTechByPosWeek[posId + "|" + week + "|" + year] = tech;
    const rawWeek = Number(row[mpIdx("WEEK")]);
    const trackingStarted = trackingStartedRawWeeks[CONTROL_YEAR + "|" + rawWeek] === true;
    if (!trackingStarted) {
      continue; // week not yet started tracking - see file header ("TRACKING GATE")
    }
    const bucket = bucketFor(tech, year, week);
    bucket.plannedVisits++;
    const area = posArea[posId] || "";
    if (area) {
      bucket.areaCounts[area] = (bucket.areaCounts[area] || 0) + 1;
    }
  }

  // ==========================================================================
  // COMPLIANCE_LOG -> dedupe to latest evaluation per (posId, week, year),
  // then aggregate realized/status counts + day-of-week distribution.
  // ==========================================================================

  const clHeaders = complianceLog.length > 0 ? (complianceLog[0] as string[]).map((h) => String(h)) : [];
  const clIdx = (name: string) => clHeaders.indexOf(name);

  interface ComplianceRow extends TimestampedRow {
    posId: string;
    technician: string;
    week: number;
    year: number;
    status: string;
    matchedActualDate: Date | null;
    matchedActualDurationHours: number | null;
    matchedActualStartedAt: Date | null;
    matchedActualFinishedAt: Date | null;
  }
  // Excel auto-detects simple date-formatted text as a real Date cell value
  // on read-back, but a full ISO datetime string (Started at/Finished at,
  // written via toISOString()) is not guaranteed to be recognized the same
  // way - parse defensively from either a real Date or a string.
  function parseCellDate(v: unknown): Date | null {
    if (v instanceof Date) {
      return v;
    }
    if (typeof v == "string" && v) {
      const d = new Date(v);
      return isNaN(d.getTime()) ? null : d;
    }
    return null;
  }
  let rawRows: ComplianceRow[] = [];
  for (let i = 1; i < complianceLog.length; i++) {
    const row = complianceLog[i];
    const posId = String(row[clIdx("posId")]);
    const week = Number(row[clIdx("plannedWeek")]);
    const year = Number(row[clIdx("plannedYear")]);
    if (!posId || !week || !year) {
      continue;
    }
    const dateVal = row[clIdx("matchedActualDate")];
    const durationRaw = Number(row[clIdx("matchedActualDurationHours")]);
    rawRows.push({
      key: posId + "|" + week + "|" + year,
      timestamp: String(row[clIdx("evaluatedAt")]),
      posId,
      technician: String(row[clIdx("technician")] ?? ""),
      week,
      year,
      status: String(row[clIdx("status")]),
      matchedActualDate: parseCellDate(dateVal),
      matchedActualDurationHours: !isNaN(durationRaw) && durationRaw > 0 ? durationRaw : null,
      matchedActualStartedAt: parseCellDate(row[clIdx("matchedActualStartedAt")]),
      matchedActualFinishedAt: parseCellDate(row[clIdx("matchedActualFinishedAt")]),
    });
  }
  const dedupedRows = latestByKey(rawRows);

  const dayIndex: { [jsDay: number]: number } = { 1: 0, 2: 1, 3: 2, 4: 3, 5: 4 }; // Mon..Fri, Sat/Sun (0,6) excluded

  // Cumulative, all-time Nesplneno tally per (technician, posId) - the data
  // behind TECHNICIAN_SCORECARD's "TOP problematic POS" tile. Built from the
  // SAME dedupedRows pass above (not a second read of raw COMPLIANCE_LOG),
  // deliberately - counting raw append-only rows here would hit the exact
  // double-counting bug already fixed once via latestByKey() (see file
  // header): a POS can sit as Nesplneno across several re-evaluated runs
  // before finally being visited, and counting every one of those rows would
  // overstate how often it was actually missed.
  let nesplnenoByTechPos: { [key: string]: { technician: string; posId: string; count: number } } = {};

  for (const r of dedupedRows) {
    if (!trueIsoTrackingStarted[r.year + "|" + r.week]) {
      continue; // week not yet started tracking - see file header ("TRACKING GATE")
    }
    // Navic_evidovano rows carry no technician in COMPLIANCE_LOG (never
    // planned for anyone) - fall back to POS_MASTER's current assignment,
    // purely for this aggregation (see file header).
    const tech = r.technician || posTechnician[r.posId] || "";
    if (!tech) {
      continue; // genuinely unattributable (POS not in POS_MASTER either) - skip rather than guess
    }
    const bucket = bucketFor(tech, r.year, r.week);
    if (r.status == "Splneno_vcas") {
      bucket.splnenoVcas++;
      bucket.realizedVisits++;
      bucket.realizedPptSum += posPpt[r.posId] || 0;
    } else if (r.status == "Splneno_pozde") {
      bucket.splnenoPozde++;
      bucket.realizedVisits++;
      bucket.realizedPptSum += posPpt[r.posId] || 0;
    } else if (r.status == "Nesplneno") {
      bucket.nesplneno++;
      const key = tech + "|" + r.posId;
      if (!nesplnenoByTechPos[key]) {
        nesplnenoByTechPos[key] = { technician: tech, posId: r.posId, count: 0 };
      }
      nesplnenoByTechPos[key].count++;
    } else if (r.status == "Navic_evidovano") {
      bucket.navicEvidovano++;
    }
    if (r.matchedActualDate && (r.status == "Splneno_vcas" || r.status == "Splneno_pozde")) {
      const jsDay = r.matchedActualDate.getDay();
      if (jsDay in dayIndex) {
        bucket.visitsByDay[dayIndex[jsDay]]++;
        bucket.possByDay[dayIndex[jsDay]].push(r.posId);
        recordDayTiming(bucket, dayIndex[jsDay], r.matchedActualStartedAt, r.matchedActualFinishedAt, r.matchedActualDurationHours);
      }
      if (r.matchedActualDurationHours !== null) {
        bucket.durationHoursSum += r.matchedActualDurationHours;
        bucket.durationKnownCount++;
      }
    }
  }

  // ==========================================================================
  // OTHER_VISIT_LOG -> otherVisits per (technician, year, week), tracking-
  // gated the same as everything else above. No dedup needed here (each
  // OTHER_VISIT_LOG row is already a unique SalesApp UID, appended once by
  // ComplianceEngine.ts - see that file's header).
  // ==========================================================================

  const ovHeaders = otherVisitLog.length > 0 ? (otherVisitLog[0] as string[]).map((h) => String(h)) : [];
  const ovIdx = (name: string) => ovHeaders.indexOf(name);
  for (let i = 1; i < otherVisitLog.length; i++) {
    const row = otherVisitLog[i];
    const posId = String(row[ovIdx("posId")]);
    const week = Number(row[ovIdx("week")]);
    const year = Number(row[ovIdx("year")]);
    if (!posId || !week || !year) {
      continue;
    }
    if (!trueIsoTrackingStarted[year + "|" + week]) {
      continue; // week not yet started tracking - see file header ("TRACKING GATE")
    }
    const tech = plannedTechByPosWeek[posId + "|" + week + "|" + year] || posTechnician[posId] || "";
    if (!tech) {
      continue; // genuinely unattributable - skip rather than guess
    }
    const bucket = bucketFor(tech, year, week);
    bucket.otherVisits++;
    // Daily breakdown (product owner, 2026-07-09: "denní statistiky techniků
    // - plnění vs. neplnění") - same day-of-week bucketing as
    // COMPLIANCE_LOG's visitsByDay above, so planned-vs-ad-hoc can be
    // compared day for day, not just as weekly totals.
    const ovDateVal = row[ovIdx("date")];
    const ovDate = typeof ovDateVal === "string" ? new Date(ovDateVal) : ovDateVal instanceof Date ? ovDateVal : null;
    if (ovDate) {
      const jsDay = ovDate.getDay();
      if (jsDay in dayIndex) {
        bucket.otherVisitsByDay[dayIndex[jsDay]]++;
        const ovDurationRaw = Number(row[ovIdx("durationHours")]);
        const ovDuration = !isNaN(ovDurationRaw) && ovDurationRaw > 0 ? ovDurationRaw : null;
        recordDayTiming(
          bucket, dayIndex[jsDay],
          parseCellDate(row[ovIdx("startedAt")]), parseCellDate(row[ovIdx("finishedAt")]), ovDuration
        );
      }
    }
  }

  // ==========================================================================
  // WRITE TECHNICIAN_PERFORMANCE_LOG (full rebuild every run - bounded row
  // count, technicians x weeks, so this stays fast regardless of how large
  // the underlying append-only logs grow - see
  // docs/MANAGER_UX_ARCHITECTURE.md section 1b).
  // ==========================================================================

  // Route-efficiency estimate for one bucket-day: order that day's realized
  // posIds by their position in the technician's PLANNED sequence for that
  // exact calendar date (PlanningEngine.ts's own GPS-clustered order), then
  // sum consecutive distanceKm() calls. A POS realized that day but absent
  // from the plan (Navic_evidovano) has no planned position - appended at
  // the end, sorted by posId, so the result is still deterministic. Returns
  // 0 when fewer than 2 GPS-resolvable stops are visited that day (no
  // "route" to measure) rather than a misleading number.
  // Orders one bucket-day's (deduplicated) realized posIds by their position
  // in the technician's PLANNED sequence for that exact calendar date - the
  // same "assume the visiting order matched the plan" approximation used by
  // routeKmForDay below, extracted here so the POS-list display (see file
  // header, product owner 2026-07-06: "kolik jich udelal a pos" - wants the
  // actual POS list per day, not just a count) shows POS in the same order
  // the km figure was computed from.
  function orderedPosForDay(technician: string, year: number, week: number, dayIndex: number, posIds: string[]): string[] {
    const monday = isoMonday(year, week);
    const visitDate = new Date(monday);
    visitDate.setDate(monday.getDate() + dayIndex);
    const dateKey = visitDate.toISOString().slice(0, 10);
    const plannedOrder = plannedOrderByTechDate[technician + "|" + dateKey] || [];
    const unique = [...new Set(posIds)];
    return unique.sort((a, b) => {
      const ai = plannedOrder.indexOf(a);
      const bi = plannedOrder.indexOf(b);
      if (ai >= 0 && bi >= 0) {
        return ai - bi;
      }
      if (ai >= 0) {
        return -1;
      }
      if (bi >= 0) {
        return 1;
      }
      return a < b ? -1 : a > b ? 1 : 0;
    });
  }

  function routeKmForDay(technician: string, year: number, week: number, dayIndex: number, posIds: string[]): number {
    if (posIds.length < 2) {
      return 0;
    }
    const sorted = orderedPosForDay(technician, year, week, dayIndex, posIds);
    let totalKm = 0;
    let resolvedStops = 0;
    let prev: { x: number; y: number } | null = null;
    for (const posId of sorted) {
      const gps = posGps[posId];
      if (!gps) {
        continue; // POS with no GPS coordinates on record - skip, don't guess a distance
      }
      resolvedStops++;
      if (prev) {
        totalKm += distanceKm(prev.x, prev.y, gps.x, gps.y);
      }
      prev = gps;
    }
    return resolvedStops >= 2 ? Math.round(totalKm * 10) / 10 : 0;
  }

  // Monitoring efektivity (product owner, 2026-07-09: "chci vidět, kdo jezdí
  // cik-cak"): the same day's GPS-resolvable stops as routeKmForDay above,
  // but run through computeOptimalRouteKm() instead of the planned-order
  // assumption - "matematicke minimum" a manager can compare the realized
  // route against. Order of posIds does not matter here (computeOptimalRouteKm
  // finds its own optimal order), unlike routeKmForDay.
  function optimalRouteKmForDay(posIds: string[]): number {
    const points: { x: number; y: number }[] = [];
    for (const posId of [...new Set(posIds)]) {
      const gps = posGps[posId];
      if (gps) {
        points.push(gps);
      }
    }
    return computeOptimalRouteKm(points);
  }

  // ==========================================================================
  // NETWORK PEER AVERAGES, one pre-pass over every bucket BEFORE either
  // output loop (product owner, 2026-07-09, speaking as vedoucí Field Force
  // týmu: "výrazně méně návštěvnosti než ostatní" - a signal that requires
  // comparing a technician against everyone else the SAME week, not just
  // against their own plan or their own history). Plain averages across
  // whichever technicians have a bucket that week - deliberately simple
  // (not a median), and with ~27 technicians one outlier's own pull on the
  // average it's being compared against is small.
  // ==========================================================================
  interface WeekPeerStats {
    visitsSum: number;
    visitsCount: number;
    pptPerVisitSum: number;
    pptPerVisitCount: number;
    durationSum: number;
    durationCount: number;
  }
  let peerStatsByWeek: { [weekKey: string]: WeekPeerStats } = {};
  for (const key of Object.keys(buckets)) {
    const b = buckets[key];
    const weekKey = b.year + "|" + b.week;
    if (!peerStatsByWeek[weekKey]) {
      peerStatsByWeek[weekKey] = { visitsSum: 0, visitsCount: 0, pptPerVisitSum: 0, pptPerVisitCount: 0, durationSum: 0, durationCount: 0 };
    }
    const stats = peerStatsByWeek[weekKey];
    stats.visitsSum += b.realizedVisits;
    stats.visitsCount++;
    if (b.realizedVisits > 0) {
      stats.pptPerVisitSum += b.realizedPptSum / b.realizedVisits;
      stats.pptPerVisitCount++;
    }
    if (b.durationKnownCount > 0) {
      stats.durationSum += b.durationHoursSum / b.durationKnownCount;
      stats.durationCount++;
    }
  }
  function networkAvgVisits(year: number, week: number): number | null {
    const s = peerStatsByWeek[year + "|" + week];
    return s && s.visitsCount > 0 ? s.visitsSum / s.visitsCount : null;
  }
  function networkAvgPptPerVisit(year: number, week: number): number | null {
    const s = peerStatsByWeek[year + "|" + week];
    return s && s.pptPerVisitCount > 0 ? s.pptPerVisitSum / s.pptPerVisitCount : null;
  }
  function networkAvgDuration(year: number, week: number): number | null {
    const s = peerStatsByWeek[year + "|" + week];
    return s && s.durationCount > 0 ? s.durationSum / s.durationCount : null;
  }
  // vs-peer % helper: null when either side is unmeasurable (never 0 -
  // "no data" must never look like "0% of peer average").
  function vsPeerPercent(value: number | null, peerAvg: number | null): number | null {
    return value !== null && peerAvg !== null && peerAvg > 0 ? Math.round((value / peerAvg) * 100) : null;
  }
  function lowFlag(percent: number | null, warningPercent: number, criticalPercent: number): string {
    if (percent === null) {
      return "";
    }
    if (percent < criticalPercent) {
      return "KRITICKÉ";
    }
    if (percent < warningPercent) {
      return "POZOR";
    }
    return "OK";
  }

  const now = new Date().toISOString();
  let outRows: (string | number)[][] = [];
  for (const key of Object.keys(buckets)) {
    const b = buckets[key];
    let topArea = "";
    let topAreaCount = 0;
    for (const area of Object.keys(b.areaCounts)) {
      if (b.areaCounts[area] > topAreaCount) {
        topArea = area;
        topAreaCount = b.areaCounts[area];
      }
    }
    const compliancePercent = b.plannedVisits > 0 ? Math.round((b.realizedVisits / b.plannedVisits) * 1000) / 10 : 0;
    const kmByDay = b.possByDay.map((posIds, dayIdx) => routeKmForDay(b.technician, b.year, b.week, dayIdx, posIds));
    // MONITORING EFEKTIVITY (product owner, 2026-07-09, "vedoucí Field Force
    // týmu, který mě má krýt záda"): weekly actual-vs-optimal route km and
    // km-per-visit, the two headline "je to cik-cak, nebo pracuje" signals.
    // Both totals only count days with >=2 GPS-resolvable stops on EITHER
    // side (a single-stop day has no "route" to be efficient or inefficient
    // about) - summed across the week rather than averaged day by day, so a
    // handful of single-stop/no-GPS days don't dilute or distort the ratio.
    const optimalKmByDay = b.possByDay.map((posIds) => optimalRouteKmForDay(posIds));
    let totalActualKm = 0;
    let totalOptimalKm = 0;
    for (let d = 0; d < 5; d++) {
      if (kmByDay[d] > 0 && optimalKmByDay[d] > 0) {
        totalActualKm += kmByDay[d];
        totalOptimalKm += optimalKmByDay[d];
      }
    }
    totalActualKm = Math.round(totalActualKm * 10) / 10;
    totalOptimalKm = Math.round(totalOptimalKm * 10) / 10;
    // null (blank in the sheet), not 0, when there is nothing measurable yet
    // this week - a real 0% "perfect" ratio must never be indistinguishable
    // from "no data".
    const efficiencyRatioPercent = totalOptimalKm > 0 ? Math.round((totalActualKm / totalOptimalKm) * 100) : null;
    const kmPerVisit = b.realizedVisits > 0 ? Math.round((totalActualKm / b.realizedVisits) * 10) / 10 : null;
    const efficiencyFlag =
      efficiencyRatioPercent === null
        ? ""
        : efficiencyRatioPercent >= ROUTE_EFFICIENCY_CRITICAL_PERCENT
        ? "KRITICKÉ"
        : efficiencyRatioPercent >= ROUTE_EFFICIENCY_WARNING_PERCENT
        ? "POZOR"
        : "OK";
    // Skutečný pracovní den (product owner, 2026-07-11: "chybí mi tam
    // zobrazení kolik udělal za den, není ten salesapp pořádně vytěžený") -
    // real clock span (první start -> poslední konec, ze SalesApp Started
    // at/Finished at) a "mrtvý čas" uvnitř něj (span minus reálně strávený
    // čas na návštěvách). Blank (not 0) for a day with fewer than the two
    // timestamps needed to know a span at all.
    const workSpanHoursByDay = b.dayFirstStart.map((start, d) => {
      const finish = b.dayLastFinish[d];
      if (!start || !finish || finish <= start) {
        return null;
      }
      return Math.round(((finish.getTime() - start.getTime()) / 3600000) * 100) / 100;
    });
    const idleHoursByDay = workSpanHoursByDay.map((span, d) =>
      span === null ? null : Math.max(0, Math.round((span - b.dayActiveHoursSum[d]) * 100) / 100)
    );
    // Display list of that day's realized POS, in the same order the km
    // figure above was computed from (see orderedPosForDay's comment) - "id
    // - name" per stop, comma-separated, so a manager can see WHICH POS a
    // technician actually visited each day, not just a count.
    const posListByDay = b.possByDay.map((posIds, dayIdx) =>
      orderedPosForDay(b.technician, b.year, b.week, dayIdx, posIds)
        .map((id) => id + (posName[id] ? " - " + posName[id] : ""))
        .join(", ")
    );
    // monthKey (YYYYMM, e.g. 202607) - the calendar month of this ISO week's
    // Monday, computed once here via JS Date arithmetic rather than
    // approximated with fragile Excel date formulas over raw ISO week
    // numbers (this project already treats week/month/year boundary math as
    // something to get right in engine code, not in spreadsheet formulas -
    // see isoWeekNumber/isoMonday's own header comments). Feeds
    // TECHNICIAN_SCORECARD's long-term monthly trend chart (product owner,
    // 2026-07-06: "je pro mě i důležitý dlouhodobý pohled" - vývoj
    // compliance za měsíce/kampaně, not just the last 6 weeks).
    const monthDate = isoMonday(b.year, b.week);
    const monthKey = monthDate.getFullYear() * 100 + (monthDate.getMonth() + 1);

    // "MANAŽERSKÉ" TRIGGERY (product owner, 2026-07-09):
    //  - podprůměrná návštěvnost oproti kolegům TENTO týden.
    //  - podprůměrná hodnotová hustota (PPT/návštěva) - "hodně návštěv, ale
    //    jednoúčelové" - dobrá km efektivita neznamená, že ty návštěvy mají
    //    hodnotu.
    //  - podprůměrná délka návštěvy (real duration ze SalesApp) - přímo
    //    naměřený signál, ne GPS odhad.
    const pptPerVisit = b.realizedVisits > 0 ? Math.round((b.realizedPptSum / b.realizedVisits) * 100) / 100 : null;
    const avgVisitDurationHours = b.durationKnownCount > 0 ? Math.round((b.durationHoursSum / b.durationKnownCount) * 100) / 100 : null;
    const volumeVsPeerPercent = vsPeerPercent(b.realizedVisits, networkAvgVisits(b.year, b.week));
    const pptDensityVsPeerPercent = vsPeerPercent(pptPerVisit, networkAvgPptPerVisit(b.year, b.week));
    const durationVsPeerPercent = vsPeerPercent(avgVisitDurationHours, networkAvgDuration(b.year, b.week));
    const volumeFlag = lowFlag(volumeVsPeerPercent, VOLUME_WARNING_PERCENT, VOLUME_CRITICAL_PERCENT);
    const pptDensityFlag = lowFlag(pptDensityVsPeerPercent, PPT_DENSITY_WARNING_PERCENT, PPT_DENSITY_CRITICAL_PERCENT);
    const durationFlag = lowFlag(durationVsPeerPercent, DURATION_WARNING_PERCENT, DURATION_CRITICAL_PERCENT);
    // Kombinovaný signál (Trigger C, product owner: "GPS je odhad, takže to
    // ani nemusí být na vinu") - žádný jednotlivý flag sám o sobě nespustí
    // "problémový technik", potřebuje se potkat aspoň PROBLEM_SIGNAL_MIN_COUNT
    // signálů najednou. compliancePercent below FLAKANI_BAD_WEEK_THRESHOLD_PERCENT
    // reuses the SAME "bad week" bar flaká riziko already uses, so this stays
    // consistent with the existing metric rather than inventing another cutoff.
    const activeSignals = [
      b.plannedVisits > 0 && compliancePercent < FLAKANI_BAD_WEEK_THRESHOLD_PERCENT,
      volumeFlag == "POZOR" || volumeFlag == "KRITICKÉ",
      pptDensityFlag == "POZOR" || pptDensityFlag == "KRITICKÉ",
      durationFlag == "POZOR" || durationFlag == "KRITICKÉ",
      efficiencyFlag == "POZOR" || efficiencyFlag == "KRITICKÉ",
    ].filter(Boolean).length;
    const combinedRiskFlag = activeSignals >= PROBLEM_SIGNAL_MIN_COUNT ? "Ano" : "Ne";

    outRows.push([
      b.technician, b.year, b.week, topArea,
      b.plannedVisits, b.realizedVisits,
      b.splnenoVcas, b.splnenoPozde, b.nesplneno, b.navicEvidovano,
      compliancePercent,
      b.visitsByDay[0], b.visitsByDay[1], b.visitsByDay[2], b.visitsByDay[3], b.visitsByDay[4],
      now,
      kmByDay[0], kmByDay[1], kmByDay[2], kmByDay[3], kmByDay[4],
      b.otherVisits,
      posListByDay[0], posListByDay[1], posListByDay[2], posListByDay[3], posListByDay[4],
      monthKey,
      b.otherVisitsByDay[0], b.otherVisitsByDay[1], b.otherVisitsByDay[2], b.otherVisitsByDay[3], b.otherVisitsByDay[4],
      totalActualKm, totalOptimalKm, efficiencyRatioPercent ?? "", kmPerVisit ?? "", efficiencyFlag,
      pptPerVisit ?? "", avgVisitDurationHours ?? "",
      volumeVsPeerPercent ?? "", pptDensityVsPeerPercent ?? "", durationVsPeerPercent ?? "",
      volumeFlag, pptDensityFlag, durationFlag, activeSignals, combinedRiskFlag,
      workSpanHoursByDay[0] ?? "", workSpanHoursByDay[1] ?? "", workSpanHoursByDay[2] ?? "", workSpanHoursByDay[3] ?? "", workSpanHoursByDay[4] ?? "",
      idleHoursByDay[0] ?? "", idleHoursByDay[1] ?? "", idleHoursByDay[2] ?? "", idleHoursByDay[3] ?? "", idleHoursByDay[4] ?? "",
    ]);
  }

  const headerRow = [
    "technician", "year", "week", "region",
    "plannedVisits", "realizedVisits",
    "splnenoVcas", "splnenoPozde", "nesplneno", "navicEvidovano",
    "compliancePercent",
    "visitsMon", "visitsTue", "visitsWed", "visitsThu", "visitsFri",
    "updatedAt",
    "kmMon", "kmTue", "kmWed", "kmThu", "kmFri",
    "otherVisits",
    "posListMon", "posListTue", "posListWed", "posListThu", "posListFri",
    "monthKey",
    // Daily planned-vs-ad-hoc stats (product owner, 2026-07-09) - appended at
    // the end so existing column-index-based readers (TECHNICIAN_SCORECARD/
    // PERFORMANCE) are unaffected.
    "otherVisitsMon", "otherVisitsTue", "otherVisitsWed", "otherVisitsThu", "otherVisitsFri",
    // Monitoring efektivity (product owner, 2026-07-09) - actual vs. optimal
    // ("matematicke minimum") route km this week, the resulting ratio, km
    // per realized visit, and a written flag - see docs/BUSINESS_RULES.md.
    "totalActualKmWeek", "totalOptimalKmWeek", "efficiencyRatioPercent", "kmPerVisit", "efficiencyFlag",
    // "Manažerské" triggery (product owner, 2026-07-09) - viz
    // docs/BUSINESS_RULES.md: podprůměrná návštěvnost/hodnotová
    // hustota/délka návštěvy oproti síti tento týden, a kombinovaný signál
    // (>= PROBLEM_SIGNAL_MIN_COUNT flagů najednou).
    "pptPerVisit", "avgVisitDurationHours",
    "volumeVsPeerPercent", "pptDensityVsPeerPercent", "durationVsPeerPercent",
    "volumeFlag", "pptDensityFlag", "durationFlag", "activeSignalCount", "combinedRiskFlag",
    // Skutečný pracovní den (product owner, 2026-07-11) - real clock work
    // span and idle time per day, from SalesApp Started at/Finished at.
    "workSpanHoursMon", "workSpanHoursTue", "workSpanHoursWed", "workSpanHoursThu", "workSpanHoursFri",
    "idleHoursMon", "idleHoursTue", "idleHoursWed", "idleHoursThu", "idleHoursFri",
  ];
  const outWs = workbook.getWorksheet("TECHNICIAN_PERFORMANCE_LOG");
  outWs.getRange("A2:BG100000").clear(ExcelScript.ClearApplyTo.contents);
  outWs.getRangeByIndexes(0, 0, 1, headerRow.length).setValues([headerRow]);
  if (outRows.length > 0) {
    outWs.getRangeByIndexes(1, 0, outRows.length, headerRow.length).setValues(outRows);
  }

  // ==========================================================================
  // WRITE TECHNICIAN_PERFORMANCE_SUMMARY: one row per technician - their
  // most recent week's numbers, plus a long-run average and trend vs the
  // previous week on record. A bounded (technicians-count) snapshot, same
  // full-rebuild-every-run approach as TECHNICIAN_PERFORMANCE_LOG above.
  // Feeds the PERFORMANCE comparison screen - built as a real, sortable/
  // filterable native Excel Table over this sheet (product owner,
  // 2026-07-05: prefer native Excel features over custom substitutes -
  // see tools/ux_style.py's build_performance_sheet()). Derived from the
  // SAME buckets already computed above, not a second read of anything -
  // no new correctness risk.
  // ==========================================================================

  interface TechWeekEntry {
    year: number; week: number; region: string;
    plannedVisits: number; realizedVisits: number;
    splnenoVcas: number; splnenoPozde: number; nesplneno: number; navicEvidovano: number;
    compliancePercent: number;
    maxKmDay: number;
    efficiencyRatioPercent: number | null;
    kmPerVisit: number | null;
    volumeVsPeerPercent: number | null;
    pptDensityVsPeerPercent: number | null;
    durationVsPeerPercent: number | null;
  }
  let byTechWeeks: { [tech: string]: TechWeekEntry[] } = {};
  for (const key of Object.keys(buckets)) {
    const b = buckets[key];
    let topArea = "";
    let topAreaCount = 0;
    for (const area of Object.keys(b.areaCounts)) {
      if (b.areaCounts[area] > topAreaCount) {
        topArea = area;
        topAreaCount = b.areaCounts[area];
      }
    }
    const compliancePercent = b.plannedVisits > 0 ? Math.round((b.realizedVisits / b.plannedVisits) * 1000) / 10 : 0;
    // Worst single day's route-km that week (see routeKmForDay above) - the
    // network-wide PERFORMANCE comparison had no route-efficiency signal at
    // all before this (found during a final full test pass, 2026-07-06):
    // km/semafor only existed per-technician on TECHNICIAN_SCORECARD, with
    // no way to scan the whole team for who had a bad day.
    const kmByDayForSummary = b.possByDay.map((posIds, dayIdx) => routeKmForDay(b.technician, b.year, b.week, dayIdx, posIds));
    const maxKmDay = Math.max(...kmByDayForSummary);
    // Same actual-vs-optimal weekly aggregation as the TECHNICIAN_PERFORMANCE_LOG
    // pass above (not reused directly - buckets are iterated in a separate
    // pass here - but the same >=2-GPS-resolvable-stops-on-both-sides rule).
    const optimalKmByDayForSummary = b.possByDay.map((posIds) => optimalRouteKmForDay(posIds));
    let summaryActualKm = 0;
    let summaryOptimalKm = 0;
    for (let d = 0; d < 5; d++) {
      if (kmByDayForSummary[d] > 0 && optimalKmByDayForSummary[d] > 0) {
        summaryActualKm += kmByDayForSummary[d];
        summaryOptimalKm += optimalKmByDayForSummary[d];
      }
    }
    const efficiencyRatioPercentForSummary = summaryOptimalKm > 0 ? Math.round((summaryActualKm / summaryOptimalKm) * 100) : null;
    const kmPerVisitForSummary = b.realizedVisits > 0 ? Math.round((summaryActualKm / b.realizedVisits) * 10) / 10 : null;
    // "Manažerské" triggery, per-week values for later long-run averaging -
    // see the identical TECHNICIAN_PERFORMANCE_LOG computation above for the
    // full rationale.
    const pptPerVisitForSummary = b.realizedVisits > 0 ? b.realizedPptSum / b.realizedVisits : null;
    const avgVisitDurationHoursForSummary = b.durationKnownCount > 0 ? b.durationHoursSum / b.durationKnownCount : null;
    const volumeVsPeerPercentForSummary = vsPeerPercent(b.realizedVisits, networkAvgVisits(b.year, b.week));
    const pptDensityVsPeerPercentForSummary = vsPeerPercent(pptPerVisitForSummary, networkAvgPptPerVisit(b.year, b.week));
    const durationVsPeerPercentForSummary = vsPeerPercent(avgVisitDurationHoursForSummary, networkAvgDuration(b.year, b.week));
    if (!byTechWeeks[b.technician]) {
      byTechWeeks[b.technician] = [];
    }
    byTechWeeks[b.technician].push({
      year: b.year, week: b.week, region: topArea,
      plannedVisits: b.plannedVisits, realizedVisits: b.realizedVisits,
      splnenoVcas: b.splnenoVcas, splnenoPozde: b.splnenoPozde,
      nesplneno: b.nesplneno, navicEvidovano: b.navicEvidovano,
      compliancePercent, maxKmDay,
      efficiencyRatioPercent: efficiencyRatioPercentForSummary, kmPerVisit: kmPerVisitForSummary,
      volumeVsPeerPercent: volumeVsPeerPercentForSummary,
      pptDensityVsPeerPercent: pptDensityVsPeerPercentForSummary,
      durationVsPeerPercent: durationVsPeerPercentForSummary,
    });
  }

  let summaryRows: (string | number)[][] = [];
  for (const tech of Object.keys(byTechWeeks)) {
    const weeks = byTechWeeks[tech].sort((a, b) => b.year * 100 + b.week - (a.year * 100 + a.week));
    const latest = weeks[0];
    const prev = weeks.length > 1 ? weeks[1] : null;
    // BUG FIX (found 2026-07-06 during a full test pass): a week with ZERO
    // plannedVisits forces compliancePercent to 0 (see the divide-by-zero
    // guard above), which would otherwise drag this average down for a week
    // where there was nothing planned to fail at (e.g. a week with only
    // unplanned/Navic_evidovano activity) - same root cause as the
    // flaka-riziko fix just below, excluded here too.
    const weeksWithPlan = weeks.filter((w) => w.plannedVisits > 0);
    const longRunAvgCompliance = weeksWithPlan.length > 0
      ? Math.round((weeksWithPlan.reduce((s, w) => s + w.compliancePercent, 0) / weeksWithPlan.length) * 10) / 10
      : 0;
    const trendDelta = prev ? Math.round((latest.compliancePercent - prev.compliancePercent) * 10) / 10 : "";
    // "Flaka riziko" (persistent-underperformance flag) - product owner,
    // 2026-07-06: "chci aby mi to ukazalo ktery z nich flaka a ktery ne" -
    // wants a repeated pattern over several weeks, not a single bad week
    // (which could be a fluke: illness, a one-off hard week, a temporarily
    // overloaded territory). Counts "bad" weeks (compliancePercent below
    // FLAKANI_BAD_WEEK_THRESHOLD_PERCENT) within the last FLAKANI_WINDOW_WEEKS
    // tracked weeks on record for this technician; flagged only once at
    // least FLAKANI_BAD_WEEKS_COUNT of those are bad. With fewer than
    // FLAKANI_BAD_WEEKS_COUNT weeks of history at all, this can never fire -
    // correctly waits for enough data rather than guessing from one week.
    // weeksWithPlan (computed above for longRunAvgCompliance) is reused here
    // too - a week with ZERO plannedVisits would otherwise unfairly count as
    // a "bad" week (compliancePercent forced to 0) for a week where there
    // was nothing planned to fail at.
    const recentWeeks = weeksWithPlan.slice(0, FLAKANI_WINDOW_WEEKS);
    const badWeeksInWindow = recentWeeks.filter((w) => w.compliancePercent < FLAKANI_BAD_WEEK_THRESHOLD_PERCENT).length;
    const flakaRiziko = badWeeksInWindow >= FLAKANI_BAD_WEEKS_COUNT ? "Ano" : "Ne";
    // Monitoring efektivity, long-run view (product owner, 2026-07-09): a
    // sustained pattern over FLAKANI_WINDOW_WEEKS, same window as flaka
    // riziko above, not just the latest single week - one bad-route week can
    // be a fluke (a diverted GPS track, an unplanned detour); a sustained
    // ratio is a real signal. Only weeks with a measurable ratio count
    // (a week with no multi-stop route days has nothing to average in).
    const weeksWithRatio = weeks.filter((w) => w.efficiencyRatioPercent !== null).slice(0, FLAKANI_WINDOW_WEEKS);
    const longRunAvgEfficiencyRatio = weeksWithRatio.length > 0
      ? Math.round(weeksWithRatio.reduce((s, w) => s + (w.efficiencyRatioPercent as number), 0) / weeksWithRatio.length)
      : null;
    const efficiencyFlagForSummary =
      longRunAvgEfficiencyRatio === null
        ? ""
        : longRunAvgEfficiencyRatio >= ROUTE_EFFICIENCY_CRITICAL_PERCENT
        ? "KRITICKÉ"
        : longRunAvgEfficiencyRatio >= ROUTE_EFFICIENCY_WARNING_PERCENT
        ? "POZOR"
        : "OK";

    // "MANAŽERSKÉ" TRIGGERY - sustained (long-run) view, same
    // FLAKANI_WINDOW_WEEKS window as flaká riziko/efficiencyFlag above, so a
    // single fluke week never triggers a flag on its own.
    //
    // Volume: BOTH comparisons requested by the product owner ("obojí
    // najednou") - vs. network peer average that week, AND vs. this
    // technician's own recent average (excluding the latest week, so a
    // technician can't be flagged for merely returning to their own normal
    // after one unusually busy week). The flag uses whichever of the two is
    // more severe.
    const weeksWithVolumeRatio = weeks.filter((w) => w.volumeVsPeerPercent !== null).slice(0, FLAKANI_WINDOW_WEEKS);
    const longRunAvgVolumeVsPeerPercent = weeksWithVolumeRatio.length > 0
      ? Math.round(weeksWithVolumeRatio.reduce((s, w) => s + (w.volumeVsPeerPercent as number), 0) / weeksWithVolumeRatio.length)
      : null;
    const priorWeeksForOwnAvg = weeks.slice(1, 1 + FLAKANI_WINDOW_WEEKS);
    const ownAvgVisits = priorWeeksForOwnAvg.length > 0
      ? priorWeeksForOwnAvg.reduce((s, w) => s + w.realizedVisits, 0) / priorWeeksForOwnAvg.length
      : null;
    const volumeVsOwnAvgPercent = vsPeerPercent(latest.realizedVisits, ownAvgVisits);
    const volumeFlagPercentForFlag =
      longRunAvgVolumeVsPeerPercent !== null && volumeVsOwnAvgPercent !== null
        ? Math.min(longRunAvgVolumeVsPeerPercent, volumeVsOwnAvgPercent)
        : longRunAvgVolumeVsPeerPercent ?? volumeVsOwnAvgPercent;
    const volumeFlagForSummary = lowFlag(volumeFlagPercentForFlag, VOLUME_WARNING_PERCENT, VOLUME_CRITICAL_PERCENT);

    const weeksWithPptDensityRatio = weeks.filter((w) => w.pptDensityVsPeerPercent !== null).slice(0, FLAKANI_WINDOW_WEEKS);
    const longRunAvgPptDensityVsPeerPercent = weeksWithPptDensityRatio.length > 0
      ? Math.round(weeksWithPptDensityRatio.reduce((s, w) => s + (w.pptDensityVsPeerPercent as number), 0) / weeksWithPptDensityRatio.length)
      : null;
    const pptDensityFlagForSummary = lowFlag(longRunAvgPptDensityVsPeerPercent, PPT_DENSITY_WARNING_PERCENT, PPT_DENSITY_CRITICAL_PERCENT);

    const weeksWithDurationRatio = weeks.filter((w) => w.durationVsPeerPercent !== null).slice(0, FLAKANI_WINDOW_WEEKS);
    const longRunAvgDurationVsPeerPercent = weeksWithDurationRatio.length > 0
      ? Math.round(weeksWithDurationRatio.reduce((s, w) => s + (w.durationVsPeerPercent as number), 0) / weeksWithDurationRatio.length)
      : null;
    const durationFlagForSummary = lowFlag(longRunAvgDurationVsPeerPercent, DURATION_WARNING_PERCENT, DURATION_CRITICAL_PERCENT);

    // Kombinovaný signál (Trigger C) - sustained version, drives the
    // automatic "problémový technik" callouts on EFFICIENCY/HOME. A lone
    // KRITICKÉ efficiencyFlag never surfaces a name by itself anymore - GPS
    // je odhad, needs corroboration.
    const activeSignalsForSummary = [
      flakaRiziko == "Ano",
      volumeFlagForSummary == "POZOR" || volumeFlagForSummary == "KRITICKÉ",
      pptDensityFlagForSummary == "POZOR" || pptDensityFlagForSummary == "KRITICKÉ",
      durationFlagForSummary == "POZOR" || durationFlagForSummary == "KRITICKÉ",
      efficiencyFlagForSummary == "POZOR" || efficiencyFlagForSummary == "KRITICKÉ",
    ].filter(Boolean).length;
    const combinedRiskFlagForSummary = activeSignalsForSummary >= PROBLEM_SIGNAL_MIN_COUNT ? "Ano" : "Ne";

    summaryRows.push([
      tech, latest.region, latest.year, latest.week,
      latest.plannedVisits, latest.realizedVisits,
      latest.splnenoVcas, latest.splnenoPozde, latest.nesplneno, latest.navicEvidovano,
      latest.compliancePercent, longRunAvgCompliance, trendDelta,
      badWeeksInWindow, flakaRiziko, latest.maxKmDay,
      latest.efficiencyRatioPercent ?? "", latest.kmPerVisit ?? "",
      longRunAvgEfficiencyRatio ?? "", efficiencyFlagForSummary,
      volumeVsOwnAvgPercent ?? "", longRunAvgVolumeVsPeerPercent ?? "", volumeFlagForSummary,
      longRunAvgPptDensityVsPeerPercent ?? "", pptDensityFlagForSummary,
      longRunAvgDurationVsPeerPercent ?? "", durationFlagForSummary,
      activeSignalsForSummary, combinedRiskFlagForSummary,
    ]);
  }
  const summaryHeaderRow = [
    "technician", "region", "latestYear", "latestWeek",
    "plannedVisits", "realizedVisits", "splnenoVcas", "splnenoPozde", "nesplneno", "navicEvidovano",
    "compliancePercent", "longRunAvgCompliance", "trendDelta",
    "badWeeksInWindow", "flakaRiziko", "maxKmDay",
    // Monitoring efektivity (product owner, 2026-07-09) - appended at the
    // end, same rationale as TECHNICIAN_PERFORMANCE_LOG's new columns above.
    "efficiencyRatioPercent", "kmPerVisit", "longRunAvgEfficiencyRatio", "efficiencyFlag",
    // "Manažerské" triggery (product owner, 2026-07-09) - viz
    // docs/BUSINESS_RULES.md.
    "volumeVsOwnAvgPercent", "longRunAvgVolumeVsPeerPercent", "volumeFlag",
    "longRunAvgPptDensityVsPeerPercent", "pptDensityFlag",
    "longRunAvgDurationVsPeerPercent", "durationFlag",
    "activeSignalCount", "combinedRiskFlag",
  ];
  const summaryWs = workbook.getWorksheet("TECHNICIAN_PERFORMANCE_SUMMARY");
  summaryWs.getRange("A2:AC100000").clear(ExcelScript.ClearApplyTo.contents);
  summaryWs.getRangeByIndexes(0, 0, 1, summaryHeaderRow.length).setValues([summaryHeaderRow]);
  if (summaryRows.length > 0) {
    summaryWs.getRangeByIndexes(1, 0, summaryRows.length, summaryHeaderRow.length).setValues(summaryRows);
  }

  // ==========================================================================
  // WRITE TECHNICIAN_TOP_ISSUES: top 5 all-time-Nesplneno POS per technician
  // (bounded technicians x 5 rows, same full-rebuild-every-run approach as
  // TECHNICIAN_PERFORMANCE_LOG above). Feeds TECHNICIAN_SCORECARD's "TOP
  // problematic POS" tile.
  // ==========================================================================

  let byTech: { [tech: string]: { posId: string; count: number }[] } = {};
  for (const entry of Object.values(nesplnenoByTechPos)) {
    if (!byTech[entry.technician]) {
      byTech[entry.technician] = [];
    }
    byTech[entry.technician].push({ posId: entry.posId, count: entry.count });
  }
  let issueRows: (string | number)[][] = [];
  for (const tech of Object.keys(byTech)) {
    const sorted = byTech[tech].sort((a, b) => b.count - a.count || (a.posId < b.posId ? -1 : 1));
    const top5 = sorted.slice(0, 5);
    top5.forEach((entry, i) => {
      issueRows.push([tech, i + 1, entry.posId, posName[entry.posId] || "", posArea[entry.posId] || "", entry.count]);
    });
  }
  const issueHeaderRow = ["technician", "rank", "posId", "posName", "region", "nesplnenoCount"];
  const issueWs = workbook.getWorksheet("TECHNICIAN_TOP_ISSUES");
  issueWs.getRange("A2:F100000").clear(ExcelScript.ClearApplyTo.contents);
  issueWs.getRangeByIndexes(0, 0, 1, issueHeaderRow.length).setValues([issueHeaderRow]);
  if (issueRows.length > 0) {
    issueWs.getRangeByIndexes(1, 0, issueRows.length, issueHeaderRow.length).setValues(issueRows);
  }

  console.log(
    "Performance Engine: " + outRows.length +
      " technician/week rows written to TECHNICIAN_PERFORMANCE_LOG (from " +
      dedupedRows.length + " deduped compliance evaluations, " +
      (complianceLog.length - 1) + " raw rows before dedup, " +
      Object.keys(trackingStartedRawWeeks).length + " week(s) with tracking started), " +
      summaryRows.length + " rows written to TECHNICIAN_PERFORMANCE_SUMMARY, " +
      issueRows.length + " rows written to TECHNICIAN_TOP_ISSUES, " +
      (otherVisitLog.length - 1) + " other-purpose visits aggregated from OTHER_VISIT_LOG."
  );
}
