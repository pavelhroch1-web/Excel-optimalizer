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
//   All threshold VALUES below are proposed defaults, not confirmed business
//   rules - see the CONTROL rows added by scaffold_workbook.py, each with an
//   explicit "proposed default, tune on real data" note. The MECHANISM
//   (config-driven, no hardcoded alert types beyond these three) is the
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
      alertRows.filter((r) => r[0] == "REGIONAL_UNDERPERFORMANCE").length + " regional underperformance)."
  );
}
