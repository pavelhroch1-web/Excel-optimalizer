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
// NOT IN THIS VERSION (docs/MANAGER_UX_ARCHITECTURE.md, explicitly deferred
// by product owner 2026-07-03): Merch/Visibility visit-purpose breakdown,
// GPS-based map data.
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
  // SYNC-BLOCK-END: core.ts (performance)

  function readTable(sheetName: string): (string | number | boolean)[][] {
    const ws = workbook.getWorksheet(sheetName);
    const range = ws.getUsedRange();
    return range ? range.getValues() : [];
  }

  const posMaster = readTable("POS_MASTER");
  const managerPlanPublished = readTable("MANAGER_PLAN_PUBLISHED");
  const complianceLog = readTable("COMPLIANCE_LOG");

  // ==========================================================================
  // POS_MASTER -> posId -> {area, technician} lookup (region info + fallback
  // technician attribution for Navic_evidovano rows)
  // ==========================================================================

  const pmHeaders = posMaster.length > 0 ? (posMaster[0] as string[]).map((h) => String(h)) : [];
  const pmIdx = (name: string) => pmHeaders.indexOf(name);
  let posArea: { [posId: string]: string } = {};
  let posTechnician: { [posId: string]: string } = {};
  for (let i = 1; i < posMaster.length; i++) {
    const row = posMaster[i];
    const posId = String(row[pmIdx("posId")]);
    if (!posId) {
      continue;
    }
    posArea[posId] = String(row[pmIdx("area")] ?? "");
    const override = String(row[pmIdx("managerOverrideTechnician")] ?? "");
    posTechnician[posId] = override || String(row[pmIdx("assignedTechnician")] ?? "");
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
    visitsByDay: number[]; // [Mon, Tue, Wed, Thu, Fri]
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
        visitsByDay: [0, 0, 0, 0, 0],
      };
    }
    return buckets[key];
  }

  // ==========================================================================
  // MANAGER_PLAN_PUBLISHED -> plannedVisits + region tally
  // ==========================================================================

  const mpHeaders = managerPlanPublished.length > 0 ? (managerPlanPublished[0] as string[]).map((h) => String(h)) : [];
  const mpIdx = (name: string) => mpHeaders.indexOf(name);
  for (let i = 1; i < managerPlanPublished.length; i++) {
    const row = managerPlanPublished[i];
    const tech = String(row[mpIdx("TECHNICIAN")] ?? "");
    const posId = String(row[mpIdx("POS")] ?? "");
    const dateVal = row[mpIdx("DATE")];
    if (!tech || !(dateVal instanceof Date)) {
      continue;
    }
    const { week, year } = isoWeekNumber(dateVal);
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
    rawRows.push({
      key: posId + "|" + week + "|" + year,
      timestamp: String(row[clIdx("evaluatedAt")]),
      posId,
      technician: String(row[clIdx("technician")] ?? ""),
      week,
      year,
      status: String(row[clIdx("status")]),
      matchedActualDate: dateVal instanceof Date ? dateVal : null,
    });
  }
  const dedupedRows = latestByKey(rawRows);

  const dayIndex: { [jsDay: number]: number } = { 1: 0, 2: 1, 3: 2, 4: 3, 5: 4 }; // Mon..Fri, Sat/Sun (0,6) excluded

  for (const r of dedupedRows) {
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
    } else if (r.status == "Splneno_pozde") {
      bucket.splnenoPozde++;
      bucket.realizedVisits++;
    } else if (r.status == "Nesplneno") {
      bucket.nesplneno++;
    } else if (r.status == "Navic_evidovano") {
      bucket.navicEvidovano++;
    }
    if (r.matchedActualDate && (r.status == "Splneno_vcas" || r.status == "Splneno_pozde")) {
      const jsDay = r.matchedActualDate.getDay();
      if (jsDay in dayIndex) {
        bucket.visitsByDay[dayIndex[jsDay]]++;
      }
    }
  }

  // ==========================================================================
  // WRITE TECHNICIAN_PERFORMANCE_LOG (full rebuild every run - bounded row
  // count, technicians x weeks, so this stays fast regardless of how large
  // the underlying append-only logs grow - see
  // docs/MANAGER_UX_ARCHITECTURE.md section 1b).
  // ==========================================================================

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
    outRows.push([
      b.technician, b.year, b.week, topArea,
      b.plannedVisits, b.realizedVisits,
      b.splnenoVcas, b.splnenoPozde, b.nesplneno, b.navicEvidovano,
      compliancePercent,
      b.visitsByDay[0], b.visitsByDay[1], b.visitsByDay[2], b.visitsByDay[3], b.visitsByDay[4],
      now,
    ]);
  }

  const headerRow = [
    "technician", "year", "week", "region",
    "plannedVisits", "realizedVisits",
    "splnenoVcas", "splnenoPozde", "nesplneno", "navicEvidovano",
    "compliancePercent",
    "visitsMon", "visitsTue", "visitsWed", "visitsThu", "visitsFri",
    "updatedAt",
  ];
  const outWs = workbook.getWorksheet("TECHNICIAN_PERFORMANCE_LOG");
  outWs.getRange("A2:Q100000").clear(ExcelScript.ClearApplyTo.contents);
  outWs.getRangeByIndexes(0, 0, 1, headerRow.length).setValues([headerRow]);
  if (outRows.length > 0) {
    outWs.getRangeByIndexes(1, 0, outRows.length, headerRow.length).setValues(outRows);
  }

  console.log(
    "Performance Engine: " + outRows.length +
      " technician/week rows written to TECHNICIAN_PERFORMANCE_LOG (from " +
      dedupedRows.length + " deduped compliance evaluations, " +
      (complianceLog.length - 1) + " raw rows before dedup)."
  );
}
