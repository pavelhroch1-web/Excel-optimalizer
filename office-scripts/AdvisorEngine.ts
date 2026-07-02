// ============================================================================
// FIELD FORCE OPTIMIZER V11 - ADVISOR ENGINE
// ============================================================================
// Deployable Office Script. Run at Refresh time (after Import Engine, and
// after Compliance Engine if a new SalesApp export was just processed).
//
// DIAGNOSTIC ONLY - this engine never writes to MANAGER_PLAN or to any
// POS_MASTER decision field. It only reads already-known facts and
// classifies them into ADVISOR_LOG rows for the manager to review and act on
// manually (docs/BUSINESS_RULES.md section 13 - "Advisor tedy nebude
// planovat. Bude cist data a prubezne mi vysvetlovat stav site.")
//
// SCOPE OF THIS VERSION (three alert types, all buildable from data Compliance
// Engine already produces - no new business decision required):
//   - NEGLECT_RISK: POS approaching/past NEGLECTED_AFTER_WEEKS since last real
//     visit. Two tiers (WARNING at a configurable ratio of the threshold,
//     CRITICAL at the threshold itself) - the "two-tier or one" question was
//     left open in docs/BUSINESS_RULES.md section 13; this ships both tiers
//     with a proposed WARNING ratio (80%), clearly flagged as tunable, not
//     silently decided.
//   - TECHNICIAN_OVERLOAD: share of a technician's planned visits that ended
//     Nesplneno over the last ADVISOR_TREND_WINDOW_WEEKS, compared against
//     proposed WARNING/CRITICAL rate thresholds.
//   - REGIONAL_UNDERPERFORMANCE: same metric, grouped by POS_MASTER.area
//     (OBLAST) instead of technician.
//   - VOLUME_TREND_SIGNAL (Planning Cycle Advisor v1 - docs/ARCHITECTURE.md
//     section 19): compares the average weekly realized-visit count over
//     the trailing ADVISOR_VOLUME_TRAILING_WEEKS weeks of VISIT_HISTORY_ACTUAL
//     against the ADVISOR_VOLUME_BASELINE_WEEKS weeks before that. A
//     deterministic moving-average heuristic, NOT a predictive model -
//     informational only (severity "INFO"), never suggests a specific
//     action, just flags that recent volume has moved meaningfully. Stays
//     silent (produces zero alerts of this type) until there is enough
//     history - correct behavior for a new workbook, not a bug.
//   - CLOSED_POS_IN_PLAN / TECHNICIAN_REASSIGNED (Published Plan Drift -
//     docs/ARCHITECTURE.md section 21): a Published/Active week is frozen
//     by design (PlanningEngine.ts never touches it again) - these two
//     alerts flag when POS_MASTER has moved on since publish (a POS closed,
//     or its technician reassigned) while the frozen commitment hasn't.
//     Never regenerates or edits the plan - purely "here's what's gone
//     stale, you decide."
//   - UNPLANNED_ACTIVE_POS: an Active POS that has never appeared in any
//     published plan at all - typically a POS that's new since the last
//     publish. A different signal from NEGLECT_RISK (which needs weeks of
//     history to fire) - this one can fire on the very first run after a
//     new POS is imported.
//   All threshold VALUES below are proposed defaults, not confirmed business
//   rules - see the CONTROL rows added by scaffold_workbook.py, each with an
//   explicit "proposed default, tune on real data" note. The MECHANISM
//   (config-driven, no hardcoded alert types beyond these) is the
//   confirmed, tested part.
//
// NOT IN THIS VERSION (see docs/BACKLOG.md):
//   - Campaign-completion risk (no active HARD cadence rule with a recurring
//     deadline exists yet - GECO/CORN are still inactive by config).
//   - Combine-visit (LOS+LOT) opportunity alerts (blocked on the same open
//     campaign-attribution question as Compliance Engine's per-visit
//     breakdown).
//   - Override-consequence notes (mechanical, deferred only for time, not
//     blocked on anything - good next candidate).
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

  // SYNC-BLOCK-START: core.ts (advisor)
  interface NeglectCandidate {
    posId: string;
    weeksSinceLastVisit: number | null;
  }

  function findNeglected(items: NeglectCandidate[], thresholdWeeks: number): string[] {
    return items
      .filter((i) => i.weeksSinceLastVisit !== null && i.weeksSinceLastVisit >= thresholdWeeks)
      .map((i) => i.posId);
  }

  interface ComplianceOutcome {
    group: string; // technician name or region name - caller decides the grouping
    status: string;
  }

  interface GroupFailureRate {
    group: string;
    total: number;
    failed: number;
    rate: number; // failed / total, in [0, 1]
  }

  function computeFailureRateByGroup(
    rows: ComplianceOutcome[],
    failureStatuses: string[]
  ): GroupFailureRate[] {
    let byGroup: { [group: string]: { total: number; failed: number } } = {};
    for (const row of rows) {
      if (!row.group) {
        continue;
      }
      if (!byGroup[row.group]) {
        byGroup[row.group] = { total: 0, failed: 0 };
      }
      byGroup[row.group].total++;
      if (failureStatuses.includes(row.status)) {
        byGroup[row.group].failed++;
      }
    }
    return Object.keys(byGroup).map((group) => ({
      group,
      total: byGroup[group].total,
      failed: byGroup[group].failed,
      rate: byGroup[group].failed / byGroup[group].total,
    }));
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

  interface WeeklyVolume {
    week: number;
    year: number;
    count: number;
  }

  interface VolumeTrendSignal {
    trailingAvg: number;
    baselineAvg: number;
    ratioPercent: number; // trailingAvg / baselineAvg * 100, rounded to 1 decimal
    significant: boolean;
  }

  function computeVolumeTrend(
    weeklyVolumes: WeeklyVolume[],
    trailingWindow: number,
    baselineWindow: number,
    thresholdPercent: number
  ): VolumeTrendSignal | null {
    const sorted = [...weeklyVolumes].sort((a, b) =>
      a.year != b.year ? a.year - b.year : a.week - b.week
    );
    if (sorted.length < trailingWindow + baselineWindow) {
      return null;
    }
    const trailing = sorted.slice(sorted.length - trailingWindow);
    const baseline = sorted.slice(
      sorted.length - trailingWindow - baselineWindow,
      sorted.length - trailingWindow
    );
    const avg = (rows: WeeklyVolume[]) => rows.reduce((sum, r) => sum + r.count, 0) / rows.length;
    const trailingAvg = avg(trailing);
    const baselineAvg = avg(baseline);
    if (baselineAvg === 0) {
      return null;
    }
    const ratioPercent = Math.round((trailingAvg / baselineAvg) * 1000) / 10;
    const significant = Math.abs(ratioPercent - 100) >= thresholdPercent;
    return { trailingAvg, baselineAvg, ratioPercent, significant };
  }

  interface OpenPlanRow {
    posId: string;
    plannedTechnician: string;
  }

  interface POSCurrentState {
    status: string; // "Active" | "Closed"
    assignedTechnician: string;
  }

  interface DriftAlert {
    posId: string;
    type: "CLOSED_POS_IN_PLAN" | "TECHNICIAN_REASSIGNED";
    plannedTechnician: string;
    currentTechnician: string;
  }

  function findPublishedPlanDrift(
    openPlanRows: OpenPlanRow[],
    posState: { [posId: string]: POSCurrentState }
  ): DriftAlert[] {
    let seen = new Set<string>(); // one alert per (posId, type), even if the POS appears in several still-open weeks
    let alerts: DriftAlert[] = [];
    for (const row of openPlanRows) {
      const current = posState[row.posId];
      if (!current) {
        continue;
      }
      if (current.status == "Closed") {
        const key = row.posId + "|CLOSED_POS_IN_PLAN";
        if (!seen.has(key)) {
          seen.add(key);
          alerts.push({
            posId: row.posId,
            type: "CLOSED_POS_IN_PLAN",
            plannedTechnician: row.plannedTechnician,
            currentTechnician: current.assignedTechnician,
          });
        }
      }
      if (current.assignedTechnician && current.assignedTechnician != row.plannedTechnician) {
        const key = row.posId + "|TECHNICIAN_REASSIGNED";
        if (!seen.has(key)) {
          seen.add(key);
          alerts.push({
            posId: row.posId,
            type: "TECHNICIAN_REASSIGNED",
            plannedTechnician: row.plannedTechnician,
            currentTechnician: current.assignedTechnician,
          });
        }
      }
    }
    return alerts;
  }

  function findUnplannedActivePOS(activePosIds: string[], everPlannedPosIds: Set<string>): string[] {
    return activePosIds.filter((posId) => !everPlannedPosIds.has(posId));
  }
  // SYNC-BLOCK-END: core.ts (advisor)

  // ==========================================================================
  // LOAD SHEETS
  // ==========================================================================

  function readTable(sheetName: string): (string | number | boolean)[][] {
    const ws = workbook.getWorksheet(sheetName);
    const range = ws.getUsedRange();
    return range ? range.getValues() : [];
  }

  const posMaster = readTable("POS_MASTER");
  const complianceLog = readTable("COMPLIANCE_LOG");
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

  const NEGLECTED_AFTER = setting("NEGLECTED_AFTER_WEEKS", 26);
  const NEGLECT_WARNING_RATIO = setting("ADVISOR_NEGLECT_WARNING_RATIO_PERCENT", 80) / 100;
  const TREND_WINDOW = setting("ADVISOR_TREND_WINDOW_WEEKS", 4);
  const OVERLOAD_WARNING_RATE = setting("ADVISOR_OVERLOAD_WARNING_RATE_PERCENT", 20) / 100;
  const OVERLOAD_CRITICAL_RATE = setting("ADVISOR_OVERLOAD_CRITICAL_RATE_PERCENT", 35) / 100;
  const VOLUME_TRAILING_WEEKS = setting("ADVISOR_VOLUME_TRAILING_WEEKS", 8);
  const VOLUME_BASELINE_WEEKS = setting("ADVISOR_VOLUME_BASELINE_WEEKS", 8);
  const VOLUME_THRESHOLD_PERCENT = setting("ADVISOR_VOLUME_THRESHOLD_PERCENT", 25);

  if (posMaster.length < 2) {
    console.log("Advisor Engine: POS_MASTER is empty - run Import Engine first.");
    return;
  }

  // ==========================================================================
  // NEGLECT_RISK
  // ==========================================================================

  const mHeaders = (posMaster[0] as string[]).map((h) => String(h));
  const midx = (name: string) => mHeaders.indexOf(name);

  let neglectCandidates: NeglectCandidate[] = [];
  let posArea: { [posId: string]: string } = {};
  for (let i = 1; i < posMaster.length; i++) {
    const r = posMaster[i];
    const posId = String(r[midx("posId")]);
    if (!posId || String(r[midx("status")]) != "Active") {
      continue;
    }
    const weeksSince =
      r[midx("weeksSinceLastVisit")] === "" || r[midx("weeksSinceLastVisit")] === undefined
        ? null
        : Number(r[midx("weeksSinceLastVisit")]);
    neglectCandidates.push({ posId, weeksSinceLastVisit: weeksSince });
    posArea[posId] = String(r[midx("area")]);
  }

  const criticalNeglect = new Set(findNeglected(neglectCandidates, NEGLECTED_AFTER));
  const warningNeglect = new Set(
    findNeglected(neglectCandidates, Math.round(NEGLECTED_AFTER * NEGLECT_WARNING_RATIO))
  );

  const now = new Date().toISOString();
  let alertRows: (string | number)[][] = [];

  for (const posId of criticalNeglect) {
    alertRows.push(["NEGLECT_RISK", "CRITICAL", "POS", posId,
      "POS " + posId + " nebylo navstiveno " + NEGLECTED_AFTER + "+ tydnu.", now]);
  }
  for (const posId of warningNeglect) {
    if (!criticalNeglect.has(posId)) {
      alertRows.push(["NEGLECT_RISK", "WARNING", "POS", posId,
        "POS " + posId + " se blizi hranici " + NEGLECTED_AFTER + " tydnu bez navstevy.", now]);
    }
  }

  // ==========================================================================
  // TECHNICIAN_OVERLOAD / REGIONAL_UNDERPERFORMANCE
  // (share only "Nesplneno" - "still not realized after the cutoff" - as
  // failure; "Splneno_pozde" is late but did happen, a weaker signal, see
  // docs/BUSINESS_RULES.md section 12)
  // ==========================================================================

  if (complianceLog.length >= 2) {
    const cHeaders = (complianceLog[0] as string[]).map((h) => String(h));
    const cidx = (name: string) => cHeaders.indexOf(name);

    // COMPLIANCE_LOG is append-only - Compliance Engine re-evaluates every
    // published planned visit on every run, so the same (posId, week, year)
    // can have several rows over time (e.g. Pending, then later Nesplneno).
    // Without deduping to the newest evaluation first, repeated weekly
    // Compliance Engine runs would progressively dilute these rates (each
    // re-run adds another row for the same visit), silently making
    // TECHNICIAN_OVERLOAD/REGIONAL_UNDERPERFORMANCE *less* sensitive over
    // time - the exact opposite of what an overload alert should do. Found
    // during end-to-end simulation against real data (tools/sim/), not
    // caught by unit tests alone since it only manifests after Compliance
    // Engine runs more than once for the same week.
    interface DedupRow extends TimestampedRow {
      week: number;
      year: number;
      status: string;
      tech: string;
      posId: string;
    }
    let rawRows: DedupRow[] = [];
    for (let i = 1; i < complianceLog.length; i++) {
      const row = complianceLog[i];
      const posId = String(row[cidx("posId")]);
      const week = Number(row[cidx("plannedWeek")]);
      const year = Number(row[cidx("plannedYear")]);
      rawRows.push({
        key: posId + "|" + week + "|" + year,
        timestamp: String(row[cidx("evaluatedAt")]),
        week,
        year,
        status: String(row[cidx("status")]),
        tech: String(row[cidx("technician")]),
        posId,
      });
    }
    const dedupedRows = latestByKey(rawRows);

    let latestWeek = 0;
    let latestYear = 0;
    for (const r of dedupedRows) {
      if (r.year > latestYear || (r.year == latestYear && r.week > latestWeek)) {
        latestWeek = r.week;
        latestYear = r.year;
      }
    }

    let techRows: ComplianceOutcome[] = [];
    let regionRows: ComplianceOutcome[] = [];
    for (const r of dedupedRows) {
      // weeksBetween-equivalent inline (52-week approximation, same
      // documented simplification as core.ts's weeksBetween)
      const withinWindow = latestWeek - r.week + (latestYear - r.year) * 52 < TREND_WINDOW;
      if (!withinWindow) {
        continue;
      }
      techRows.push({ group: r.tech, status: r.status });
      regionRows.push({ group: posArea[r.posId] || "", status: r.status });
    }

    const techRates = computeFailureRateByGroup(techRows, ["Nesplneno"]);
    const regionRates = computeFailureRateByGroup(regionRows, ["Nesplneno"]);

    for (const t of techRates) {
      if (t.rate >= OVERLOAD_CRITICAL_RATE) {
        alertRows.push(["TECHNICIAN_OVERLOAD", "CRITICAL", "TECHNICIAN", t.group,
          "Technik " + t.group + ": " + t.failed + "/" + t.total + " planovanych navstev nesplneno za posledni " + TREND_WINDOW + " tydny.", now]);
      } else if (t.rate >= OVERLOAD_WARNING_RATE) {
        alertRows.push(["TECHNICIAN_OVERLOAD", "WARNING", "TECHNICIAN", t.group,
          "Technik " + t.group + ": " + t.failed + "/" + t.total + " planovanych navstev nesplneno za posledni " + TREND_WINDOW + " tydny.", now]);
      }
    }
    for (const r of regionRates) {
      if (!r.group) {
        continue;
      }
      if (r.rate >= OVERLOAD_CRITICAL_RATE) {
        alertRows.push(["REGIONAL_UNDERPERFORMANCE", "CRITICAL", "REGION", r.group,
          "Region " + r.group + ": " + r.failed + "/" + r.total + " planovanych navstev nesplneno za posledni " + TREND_WINDOW + " tydny.", now]);
      } else if (r.rate >= OVERLOAD_WARNING_RATE) {
        alertRows.push(["REGIONAL_UNDERPERFORMANCE", "WARNING", "REGION", r.group,
          "Region " + r.group + ": " + r.failed + "/" + r.total + " planovanych navstev nesplneno za posledni " + TREND_WINDOW + " tydny.", now]);
      }
    }
  } else {
    console.log("Advisor Engine: COMPLIANCE_LOG is empty - skipping TECHNICIAN_OVERLOAD/REGIONAL_UNDERPERFORMANCE (run Compliance Engine first for those alert types).");
  }

  // ==========================================================================
  // VOLUME_TREND_SIGNAL (Planning Cycle Advisor v1 - see file header and
  // docs/ARCHITECTURE.md section 19). Reads VISIT_HISTORY_ACTUAL directly
  // (real calendar week/year - isoWeekNumber() at import time, unlike
  // MANAGER_PLAN/PLAN_LIFECYCLE's campaign-relative counter - see
  // ReportingEngine.ts's PLANNING READINESS section for that distinction),
  // so this signal is meaningful regardless of how many campaign cycles
  // have run. Deliberately informational only: severity "INFO", message
  // states the fact, never recommends a specific action.
  // ==========================================================================

  const visitHistoryActual = readTable("VISIT_HISTORY_ACTUAL");
  if (visitHistoryActual.length >= 2) {
    const vHeaders = (visitHistoryActual[0] as string[]).map((h) => String(h));
    const vidx = (name: string) => vHeaders.indexOf(name);
    let countsByWeek: { [key: string]: WeeklyVolume } = {};
    for (let i = 1; i < visitHistoryActual.length; i++) {
      const r = visitHistoryActual[i];
      const week = Number(r[vidx("week")]);
      const year = Number(r[vidx("year")]);
      if (!week || !year) {
        continue;
      }
      const key = year + "|" + week;
      if (!countsByWeek[key]) {
        countsByWeek[key] = { week, year, count: 0 };
      }
      countsByWeek[key].count++;
    }
    const signal = computeVolumeTrend(
      Object.values(countsByWeek),
      VOLUME_TRAILING_WEEKS,
      VOLUME_BASELINE_WEEKS,
      VOLUME_THRESHOLD_PERCENT
    );
    if (signal && signal.significant) {
      const direction = signal.ratioPercent > 100 ? "vyssi" : "nizsi";
      alertRows.push([
        "VOLUME_TREND_SIGNAL", "INFO", "NETWORK", "ALL",
        "Objem realizovanych navstev za posledni " + VOLUME_TRAILING_WEEKS + " tydny je " +
          Math.abs(Math.round(signal.ratioPercent - 100)) + "% " + direction +
          " nez v predchozich " + VOLUME_BASELINE_WEEKS + " tydnech (" +
          Math.round(signal.trailingAvg * 10) / 10 + " vs " + Math.round(signal.baselineAvg * 10) / 10 +
          " navstev/tyden v prumeru). Informativni signal, zadna akce neni automaticky navrzena.",
        now,
      ]);
    }
  } else {
    console.log("Advisor Engine: VISIT_HISTORY_ACTUAL is empty - skipping VOLUME_TREND_SIGNAL (run Compliance Engine after a SalesApp import first).");
  }

  // ==========================================================================
  // PUBLISHED PLAN DRIFT + UNPLANNED ACTIVE POS (docs/ARCHITECTURE.md
  // section 21). Reads MANAGER_PLAN_PUBLISHED (never Draft MANAGER_PLAN -
  // same "only the frozen commitment matters" rule as ComplianceEngine.ts)
  // joined against PLAN_LIFECYCLE to find weeks still Published/Active (not
  // yet Closed - a Closed week is history, drift no longer matters), and
  // compares those rows against the CURRENT POS_MASTER. Diagnostic only:
  // never edits MANAGER_PLAN_PUBLISHED or POS_MASTER.
  // ==========================================================================

  const managerPlanPublished = readTable("MANAGER_PLAN_PUBLISHED");
  const planLifecycleForDrift = readTable("PLAN_LIFECYCLE");
  if (managerPlanPublished.length >= 2) {
    let openWeeks = new Set<string>(); // "year|week", Published or Active only
    if (planLifecycleForDrift.length >= 2) {
      const plHeaders = (planLifecycleForDrift[0] as string[]).map((h) => String(h));
      const plIdx = (name: string) => plHeaders.indexOf(name);
      for (let i = 1; i < planLifecycleForDrift.length; i++) {
        const row = planLifecycleForDrift[i];
        const status = String(row[plIdx("status")]);
        if (status == "Published" || status == "Active") {
          openWeeks.add(String(row[plIdx("year")]) + "|" + String(row[plIdx("week")]));
        }
      }
    }

    const mpHeaders = (managerPlanPublished[0] as string[]).map((h) => String(h));
    const mpIdx = (name: string) => mpHeaders.indexOf(name);
    const cWeek3 = mpIdx("WEEK");
    const cPos3 = mpIdx("POS");
    const cTech3 = mpIdx("TECHNICIAN");

    let openPlanRows: OpenPlanRow[] = [];
    let everPlannedPosIds = new Set<string>();
    for (let i = 1; i < managerPlanPublished.length; i++) {
      const row = managerPlanPublished[i];
      const posId = String(row[cPos3]);
      const week = String(row[cWeek3]);
      if (!posId) {
        continue;
      }
      everPlannedPosIds.add(posId);
      // openWeeks doesn't carry a per-row year for MANAGER_PLAN_PUBLISHED
      // (only PLAN_LIFECYCLE does) - matches on week alone across whatever
      // years are currently open. Narrow enough in practice: a week number
      // only stays "open" (Published/Active, not yet Closed) for a few
      // weeks at a time, so a cross-year collision here is not a realistic
      // false positive.
      let isOpen = false;
      for (const key of openWeeks) {
        if (key.endsWith("|" + week)) {
          isOpen = true;
          break;
        }
      }
      if (isOpen) {
        openPlanRows.push({ posId, plannedTechnician: String(row[cTech3]) });
      }
    }

    let posState: { [posId: string]: POSCurrentState } = {};
    let activePosIds: string[] = [];
    for (let i = 1; i < posMaster.length; i++) {
      const posId = String(posMaster[i][midx("posId")]);
      if (!posId) {
        continue;
      }
      const status = String(posMaster[i][midx("status")]);
      posState[posId] = { status, assignedTechnician: String(posMaster[i][midx("assignedTechnician")]) };
      if (status == "Active") {
        activePosIds.push(posId);
      }
    }

    const driftAlerts = findPublishedPlanDrift(openPlanRows, posState);
    for (const d of driftAlerts) {
      if (d.type == "CLOSED_POS_IN_PLAN") {
        alertRows.push([
          "CLOSED_POS_IN_PLAN", "WARNING", "POS", d.posId,
          "POS " + d.posId + " je v aktualne otevrenem publikovanem planu (technik " + d.plannedTechnician +
            "), ale v POS_MASTER je nyni veden jako Closed.",
          now,
        ]);
      } else {
        alertRows.push([
          "TECHNICIAN_REASSIGNED", "WARNING", "POS", d.posId,
          "POS " + d.posId + " byl publikovan pro technika " + d.plannedTechnician +
            ", ale POS_MASTER nyni uvadi jineho technika (" + d.currentTechnician + ").",
          now,
        ]);
      }
    }

    for (const posId of findUnplannedActivePOS(activePosIds, everPlannedPosIds)) {
      alertRows.push([
        "UNPLANNED_ACTIVE_POS", "INFO", "POS", posId,
        "POS " + posId + " je Active v POS_MASTER, ale nebyl zatim soucasti zadneho publikovaneho planu.",
        now,
      ]);
    }
  } else {
    console.log("Advisor Engine: MANAGER_PLAN_PUBLISHED is empty - skipping Published Plan Drift checks (run Publish Engine first).");
  }

  // ==========================================================================
  // WRITE ADVISOR_LOG (append-only - each run's alerts are a new snapshot,
  // old alerts stay for trend history rather than being overwritten)
  // ==========================================================================

  const advisorWs = workbook.getWorksheet("ADVISOR_LOG");
  const existing = advisorWs.getUsedRange();
  const startRow = existing ? existing.getRowCount() : 1;
  if (alertRows.length > 0) {
    advisorWs.getRangeByIndexes(startRow, 0, alertRows.length, 6).setValues(alertRows);
  }

  console.log(
    "Advisor Engine: " + alertRows.length + " alerts written (" +
      alertRows.filter((r) => r[0] == "NEGLECT_RISK").length + " neglect risk, " +
      alertRows.filter((r) => r[0] == "TECHNICIAN_OVERLOAD").length + " technician overload, " +
      alertRows.filter((r) => r[0] == "REGIONAL_UNDERPERFORMANCE").length + " regional underperformance, " +
      alertRows.filter((r) => r[0] == "VOLUME_TREND_SIGNAL").length + " volume trend signal, " +
      alertRows.filter((r) => r[0] == "CLOSED_POS_IN_PLAN").length + " closed POS in plan, " +
      alertRows.filter((r) => r[0] == "TECHNICIAN_REASSIGNED").length + " technician reassigned, " +
      alertRows.filter((r) => r[0] == "UNPLANNED_ACTIVE_POS").length + " unplanned active POS)."
  );
}
