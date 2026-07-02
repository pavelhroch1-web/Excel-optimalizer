// ============================================================================
// FIELD FORCE OPTIMIZER V11 - REPORTING ENGINE (Dashboard)
// ============================================================================
// Deployable Office Script. Run any time after Import/Planning/Compliance/
// Advisor Engine (works with whatever data currently exists - a fresh
// workbook with only POS_MASTER populated still produces a coverage
// summary, just with empty compliance/advisor sections).
//
// COMPUTES NOTHING NEW - pure aggregation over POS_MASTER, COMPLIANCE_LOG,
// ADVISOR_LOG (see docs/ARCHITECTURE.md section 5, Reporting Engine
// responsibilities). No business logic, no filtering decisions - if a number
// here looks wrong, the bug is upstream in whichever engine produced the
// underlying row, not here.
//
// SCOPE OF THIS VERSION:
//   - Network overview: Active/Closed POS counts, by market.
//   - Compliance summary: latest-known status per (POS, planned week) -
//     COMPLIANCE_LOG is append-only and can hold several evaluations of the
//     same planned visit over time (Pending -> Nesplneno, etc.), so this
//     dedupes to the newest evaluation per key before counting, otherwise
//     the same visit would be counted multiple times.
//   - Technician KPI: per technician, completion rate = (Splneno_vcas +
//     Splneno_pozde) / (Splneno_vcas + Splneno_pozde + Nesplneno) - Pending
//     rows are excluded from the denominator since they are not yet due
//     (counting them would understate completion for no reason).
//   - Advisor summary: counts from the MOST RECENT AdvisorEngine.ts run only
//     (ADVISOR_LOG is append-only for trend history - a dashboard should
//     show current alerts, not every alert ever raised).
// ============================================================================

function main(workbook: ExcelScript.Workbook) {
  // SYNC-BLOCK-START: core.ts (reporting)
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
  // SYNC-BLOCK-END: core.ts (reporting)

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
  const advisorLog = readTable("ADVISOR_LOG");

  const dashWs = workbook.getWorksheet("DASHBOARD");
  dashWs.getRange("A1:F500").clear();

  let output: (string | number)[][] = [];
  function section(title: string) {
    output.push([title, "", "", "", "", ""]);
  }
  function row(...cells: (string | number)[]) {
    while (cells.length < 6) {
      cells.push("");
    }
    output.push(cells);
  }
  function blank() {
    output.push(["", "", "", "", "", ""]);
  }

  // ==========================================================================
  // NETWORK OVERVIEW
  // ==========================================================================

  section("NETWORK OVERVIEW");
  if (posMaster.length >= 2) {
    const mHeaders = (posMaster[0] as string[]).map((h) => String(h));
    const midx = (name: string) => mHeaders.indexOf(name);
    let active = 0;
    let closed = 0;
    let byMarket: { [market: string]: number } = {};
    for (let i = 1; i < posMaster.length; i++) {
      const r = posMaster[i];
      if (!r[midx("posId")]) {
        continue;
      }
      if (String(r[midx("status")]) == "Active") {
        active++;
        const market = String(r[midx("market")]);
        byMarket[market] = (byMarket[market] || 0) + 1;
      } else {
        closed++;
      }
    }
    row("Active POS", active);
    row("Closed POS", closed);
    for (const market of Object.keys(byMarket).sort()) {
      row("  " + market, byMarket[market]);
    }
  } else {
    row("(POS_MASTER is empty - run Import Engine first)");
  }
  blank();

  // ==========================================================================
  // COMPLIANCE SUMMARY (dedup to latest evaluation per POS+week)
  // ==========================================================================

  section("COMPLIANCE SUMMARY (latest known status per planned visit)");
  let latestCompliance: { key: string; timestamp: string; status: string; technician: string }[] = [];
  if (complianceLog.length >= 2) {
    const cHeaders = (complianceLog[0] as string[]).map((h) => String(h));
    const cidx = (name: string) => cHeaders.indexOf(name);
    let raw: { key: string; timestamp: string; status: string; technician: string }[] = [];
    for (let i = 1; i < complianceLog.length; i++) {
      const r = complianceLog[i];
      if (!r[cidx("posId")]) {
        continue;
      }
      raw.push({
        key: String(r[cidx("posId")]) + "|" + String(r[cidx("plannedWeek")]) + "|" + String(r[cidx("plannedYear")]),
        timestamp: String(r[cidx("evaluatedAt")]),
        status: String(r[cidx("status")]),
        technician: String(r[cidx("technician")]),
      });
    }
    latestCompliance = latestByKey(raw);
    let counts: { [status: string]: number } = {};
    for (const c of latestCompliance) {
      counts[c.status] = (counts[c.status] || 0) + 1;
    }
    for (const status of ["Splneno_vcas", "Splneno_pozde", "Nesplneno", "Pending", "Navic_evidovano"]) {
      row(status, counts[status] || 0);
    }
  } else {
    row("(COMPLIANCE_LOG is empty - run Compliance Engine after a SalesApp import)");
  }
  blank();

  // ==========================================================================
  // TECHNICIAN KPI
  // ==========================================================================

  section("TECHNICIAN KPI (completion rate excludes Pending - not yet due)");
  row("Technician", "Splneno_vcas", "Splneno_pozde", "Nesplneno", "Completion %");
  if (latestCompliance.length > 0) {
    let byTech: { [tech: string]: { vcas: number; pozde: number; nesplneno: number } } = {};
    for (const c of latestCompliance) {
      if (!c.technician) {
        continue; // Navic_evidovano rows have no resolved technician - see ComplianceEngine.ts
      }
      if (!byTech[c.technician]) {
        byTech[c.technician] = { vcas: 0, pozde: 0, nesplneno: 0 };
      }
      if (c.status == "Splneno_vcas") byTech[c.technician].vcas++;
      if (c.status == "Splneno_pozde") byTech[c.technician].pozde++;
      if (c.status == "Nesplneno") byTech[c.technician].nesplneno++;
    }
    for (const tech of Object.keys(byTech).sort()) {
      const t = byTech[tech];
      const denom = t.vcas + t.pozde + t.nesplneno;
      const rate = denom > 0 ? Math.round(((t.vcas + t.pozde) / denom) * 1000) / 10 : 0;
      row(tech, t.vcas, t.pozde, t.nesplneno, rate);
    }
  }
  blank();

  // ==========================================================================
  // ADVISOR SUMMARY (most recent run only)
  // ==========================================================================

  section("ADVISOR ALERTS (most recent Advisor Engine run)");
  if (advisorLog.length >= 2) {
    const aHeaders = (advisorLog[0] as string[]).map((h) => String(h));
    const aidx = (name: string) => aHeaders.indexOf(name);
    let latestRun = "";
    for (let i = 1; i < advisorLog.length; i++) {
      const ts = String(advisorLog[i][aidx("evaluatedAt")]);
      if (ts > latestRun) {
        latestRun = ts;
      }
    }
    let counts: { [key: string]: number } = {};
    for (let i = 1; i < advisorLog.length; i++) {
      const r = advisorLog[i];
      if (String(r[aidx("evaluatedAt")]) != latestRun) {
        continue;
      }
      const key = String(r[aidx("type")]) + " (" + String(r[aidx("severity")]) + ")";
      counts[key] = (counts[key] || 0) + 1;
    }
    if (Object.keys(counts).length == 0) {
      row("(no alerts in the most recent run)");
    }
    for (const key of Object.keys(counts).sort()) {
      row(key, counts[key]);
    }
  } else {
    row("(ADVISOR_LOG is empty - run Advisor Engine first)");
  }

  // ==========================================================================
  // WRITE DASHBOARD
  // ==========================================================================

  if (output.length > 0) {
    dashWs.getRangeByIndexes(0, 0, output.length, 6).setValues(output);
  }

  console.log("Reporting Engine: dashboard refreshed, " + output.length + " rows written.");
}
