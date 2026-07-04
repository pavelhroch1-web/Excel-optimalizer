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
//   - Regional overview: same completion-rate calculation, grouped by
//     POS_MASTER.market instead of technician (reuses computeFailureRateByGroup,
//     already used identically by AdvisorEngine.ts for regional underperformance).
//   - Weekly trend: Splneno/Nesplneno counts grouped by COMPLIANCE_LOG's
//     plannedWeek/plannedYear (PlanningEngine's campaign-relative counter -
//     see note below, NOT a calendar ISO week).
//   - Technician workload: planned visit count vs. resolveCapacity() for the
//     most recent calendar week present in MANAGER_PLAN - the exact same
//     capacity formula PlanningEngine.ts uses to allocate, just read here for
//     reporting instead of allocation. No new business rule.
//   - Advisor summary: counts from the MOST RECENT AdvisorEngine.ts run only
//     (ADVISOR_LOG is append-only for trend history - a dashboard should
//     show current alerts, not every alert ever raised).
//   - POS_MAP_DATA: one (X, Y) column pair per technician (Active POS with
//     GPS coordinates, grouped by current assignedTechnician/
//     managerOverrideTechnician) - feeds the MAP sheet's territory-overview
//     scatter chart (tools/ux_style.py). Added 2026-07-06 after the
//     manager-analytics review; see the WRITE POS_MAP_DATA section below for
//     the fixed-size/column-orientation details.
//
// ALL SECTIONS BELOW ARE PURE AGGREGATION of numbers already decided by
// PlanningEngine/ComplianceEngine/AdvisorEngine - this file does not
// classify a visit's compliance status, does not decide capacity, and does
// not change the address-based dedup rule. If a number here looks wrong,
// the bug is upstream, not here.
// ============================================================================

function main(workbook: ExcelScript.Workbook) {
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

  function resolveCapacity(
    overrideMap: { [key: string]: number },
    tech: string,
    year: number,
    week: number,
    workDaysCount: number,
    targetVisitsPerDay: number,
    targetVisitsWeek: number | null = null
  ): number {
    const key = tech + "|" + year + "|" + week;
    if (overrideMap[key] !== undefined) {
      return overrideMap[key];
    }
    if (targetVisitsWeek !== null) {
      return targetVisitsWeek;
    }
    return workDaysCount * targetVisitsPerDay;
  }

  function weeksBetween(week1: number, year1: number, week2: number, year2: number): number {
    return week2 - week1 + (year2 - year1) * 52;
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
  // Rows 1-3 are the static title banner + KPI tile labels that
  // tools/ux_style.py's build_dashboard_template wrote once - never
  // touched here. Row 3's tile VALUES (B3:E3) are overwritten directly
  // below regardless, so there is no need to clear them first, and clearing
  // A1:F3 would also wipe the "DASHBOARD" title text and tile labels (a
  // content clear removes values even with ClearApplyTo.contents - only
  // formatting is preserved, not text). Detail sections start at row 5.
  // Widened from A5:F500 to A5:F2000 when the Regional/Weekly Trend/Workload
  // sections were added - more sections means more potential rows (one per
  // market/week/technician each), same static-cap trade-off as before, just
  // with headroom for the new sections too.
  dashWs.getRange("A5:F2000").clear(ExcelScript.ClearApplyTo.contents);

  // KPI tile values (B3/C3/D3/E3 - fixed positions pre-styled by
  // tools/ux_style.py's build_dashboard_template) - filled in as the
  // existing detail sections below compute the same underlying numbers, so
  // there is exactly one source of truth per number, just also mirrored to
  // a prominent tile.
  let kpiActivePos = 0;
  let kpiSplnenoVcas = 0;
  let kpiNesplneno = 0;
  let kpiOpenAlerts = 0;

  // Fixed-size chart-data collectors: native Excel charts (built once by
  // tools/ux_style.py) bind to FIXED cell ranges, unlike the flowing detail
  // sections below whose row count varies run to run - a chart pointed at a
  // variable-length range would silently go stale or show blank rows. These
  // arrays mirror a subset of the same numbers already computed for the
  // flowing sections (single source of truth logic, just also captured here
  // for a stable-shaped write later - see "CHART DATA BLOCKS" below).
  let chartWeekly: { label: string; vcas: number; pozde: number; nesplneno: number }[] = [];
  let chartWorkload: { tech: string; planned: number; capacity: number; utilization: number }[] = [];
  let chartRegional: { market: string; completionPercent: number }[] = [];

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
    kpiActivePos = active;
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
  let latestCompliance: {
    key: string;
    timestamp: string;
    status: string;
    technician: string;
    posId: string;
    plannedWeek: number;
    plannedYear: number;
  }[] = [];
  if (complianceLog.length >= 2) {
    const cHeaders = (complianceLog[0] as string[]).map((h) => String(h));
    const cidx = (name: string) => cHeaders.indexOf(name);
    let raw: typeof latestCompliance = [];
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
        posId: String(r[cidx("posId")]),
        plannedWeek: Number(r[cidx("plannedWeek")]),
        plannedYear: Number(r[cidx("plannedYear")]),
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
    kpiSplnenoVcas = counts["Splneno_vcas"] || 0;
    kpiNesplneno = counts["Nesplneno"] || 0;
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
  // REGIONAL OVERVIEW (same completion-rate calculation as Technician KPI,
  // grouped by POS_MASTER.market instead - reuses computeFailureRateByGroup,
  // the identical function AdvisorEngine.ts uses for its own regional
  // underperformance alert, just for reporting instead of alerting)
  // ==========================================================================

  section("REGIONAL OVERVIEW (completion rate by market)");
  row("Market", "Total evaluated", "Nesplneno", "Completion %");
  if (posMaster.length >= 2 && latestCompliance.length > 0) {
    const mHeaders = (posMaster[0] as string[]).map((h) => String(h));
    const midx = (name: string) => mHeaders.indexOf(name);
    let marketByPos: { [posId: string]: string } = {};
    for (let i = 1; i < posMaster.length; i++) {
      const r = posMaster[i];
      if (r[midx("posId")]) {
        marketByPos[String(r[midx("posId")])] = String(r[midx("market")]);
      }
    }
    const regionalOutcomes: { group: string; status: string }[] = latestCompliance
      .filter((c) => c.status != "Pending")
      .map((c) => ({ group: marketByPos[c.posId] || "", status: c.status }));
    const regionalRates = computeFailureRateByGroup(regionalOutcomes, ["Nesplneno"]);
    for (const r of regionalRates.sort((a, b) => a.group.localeCompare(b.group))) {
      const completionPercent = Math.round((1 - r.rate) * 1000) / 10;
      row(r.group, r.total, r.failed, completionPercent);
      chartRegional.push({ market: r.group, completionPercent });
    }
  }
  blank();

  // ==========================================================================
  // WEEKLY TREND (Splneno/Nesplneno counts by planned week - uses
  // PlanningEngine's campaign-relative week counter, NOT a calendar ISO
  // week, since that is the only per-week key COMPLIANCE_LOG has; see
  // docs/BACKLOG.md for the known year-boundary simplification this
  // inherits)
  // ==========================================================================

  section("WEEKLY TREND (podle plánovaného týdne kampaně, ne kalendářního)");
  row("Week", "Splneno_vcas", "Splneno_pozde", "Nesplneno", "Completion %");
  if (latestCompliance.length > 0) {
    let byWeek: { [key: string]: { week: number; year: number; vcas: number; pozde: number; nesplneno: number } } = {};
    for (const c of latestCompliance) {
      if (c.status == "Pending") {
        continue;
      }
      const key = c.plannedYear + "|" + c.plannedWeek;
      if (!byWeek[key]) {
        byWeek[key] = { week: c.plannedWeek, year: c.plannedYear, vcas: 0, pozde: 0, nesplneno: 0 };
      }
      if (c.status == "Splneno_vcas") byWeek[key].vcas++;
      if (c.status == "Splneno_pozde") byWeek[key].pozde++;
      if (c.status == "Nesplneno") byWeek[key].nesplneno++;
    }
    const weekKeys = Object.keys(byWeek).sort((a, b) => {
      const wa = byWeek[a], wb = byWeek[b];
      return wa.year != wb.year ? wa.year - wb.year : wa.week - wb.week;
    });
    for (const key of weekKeys) {
      const w = byWeek[key];
      const denom = w.vcas + w.pozde + w.nesplneno;
      const rate = denom > 0 ? Math.round(((w.vcas + w.pozde) / denom) * 1000) / 10 : 0;
      row(w.year + " / " + w.week, w.vcas, w.pozde, w.nesplneno, rate);
      chartWeekly.push({ label: w.year + "/" + w.week, vcas: w.vcas, pozde: w.pozde, nesplneno: w.nesplneno });
    }
  }
  blank();

  // ==========================================================================
  // TECHNICIAN WORKLOAD (capacity utilization for the most recent calendar
  // week present in MANAGER_PLAN - reads MANAGER_PLAN.DATE, a real calendar
  // date, NOT the WEEK column, so this is unaffected by the campaign-relative
  // counter's year-boundary simplification noted above. Uses resolveCapacity(),
  // the exact same formula PlanningEngine.ts already uses to allocate -
  // reporting on an approved formula, not deciding a new one.)
  // ==========================================================================

  section("TECHNICIAN WORKLOAD (nejnovější kalendářní týden v MANAGER_PLAN)");
  row("Technician", "Planned visits", "Capacity", "Utilization %");
  const managerPlan = readTable("MANAGER_PLAN");
  const capacityOverrideRows = readTable("CAPACITY_OVERRIDE");
  const controlRows = readTable("CONTROL");
  if (managerPlan.length >= 2) {
    let controlMap: { [key: string]: string } = {};
    for (let i = 1; i < controlRows.length; i++) {
      if (controlRows[i][0]) {
        controlMap[String(controlRows[i][0])] = String(controlRows[i][1]);
      }
    }
    const targetVisitsDay = controlMap["TARGET_VISITS_DAY"] ? Number(controlMap["TARGET_VISITS_DAY"]) : 8;
    // Mirrors PlanningEngine.ts's TARGET_WEEK - keeps the dashboard's
    // utilization % consistent with whatever capacity model Planning
    // actually used to build the plan.
    const targetVisitsWeekRaw = controlMap["TARGET_VISITS_WEEK"] ? Number(controlMap["TARGET_VISITS_WEEK"]) : NaN;
    const targetVisitsWeek = isNaN(targetVisitsWeekRaw) ? null : targetVisitsWeekRaw;

    let capacityOverrideMap: { [key: string]: number } = {};
    for (let i = 1; i < capacityOverrideRows.length; i++) {
      const r = capacityOverrideRows[i];
      if (r[0]) {
        capacityOverrideMap[String(r[0]) + "|" + String(r[1]) + "|" + String(r[2])] = Number(r[3]);
      }
    }

    // Column layout: B=DATE, D=TECHNICIAN (see scaffold_workbook.py).
    let latestWeek = { week: 0, year: 0 };
    let visitsByTechWeek: { [key: string]: { week: number; year: number; tech: string; count: number } } = {};
    for (let i = 1; i < managerPlan.length; i++) {
      const r = managerPlan[i];
      const dateVal = r[1];
      const tech = r[3] ? String(r[3]) : "";
      if (!tech || !(dateVal instanceof Date)) {
        continue;
      }
      const { week, year } = isoWeekNumber(dateVal);
      if (year > latestWeek.year || (year == latestWeek.year && week > latestWeek.week)) {
        latestWeek = { week, year };
      }
      const key = tech + "|" + year + "|" + week;
      if (!visitsByTechWeek[key]) {
        visitsByTechWeek[key] = { week, year, tech, count: 0 };
      }
      visitsByTechWeek[key].count++;
    }

    if (latestWeek.week > 0) {
      const days = workDays(latestWeek.year, latestWeek.week).length;
      for (const key of Object.keys(visitsByTechWeek).sort()) {
        const v = visitsByTechWeek[key];
        if (v.week != latestWeek.week || v.year != latestWeek.year) {
          continue;
        }
        const capacity = resolveCapacity(capacityOverrideMap, v.tech, v.year, v.week, days, targetVisitsDay, targetVisitsWeek);
        const utilization = capacity > 0 ? Math.round((v.count / capacity) * 1000) / 10 : 0;
        row(v.tech, v.count, capacity, utilization);
        chartWorkload.push({ tech: v.tech, planned: v.count, capacity, utilization });
      }
    }
  } else {
    row("(MANAGER_PLAN is empty - run Planning Engine first)");
  }
  blank();

  // ==========================================================================
  // PLANNING READINESS (raw signals only - NOT a recommendation. Product
  // owner asked for a future "kdy je vhodne pripravit dalsi plan" advisor,
  // explicitly as a data-model/architecture readiness step, not a decision
  // to implement yet - see docs/ARCHITECTURE.md section 18 and the new,
  // still-inactive PLANNING_HORIZON_RULES table. This section surfaces the
  // facts a manager would use to decide that manually today: how far the
  // committed (Published/Active) plan reaches, how much Draft runway exists
  // beyond it, and how many days remain. It does not say "plan now" - that
  // judgment call, and how a seasonal exception gets defined, is still open.
  // ==========================================================================

  section("PLANNING READINESS (signály, ne doporučení)");
  const planLifecycle = readTable("PLAN_LIFECYCLE");
  if (planLifecycle.length >= 2 || managerPlan.length >= 2) {
    // Both PLAN_LIFECYCLE.week and MANAGER_PLAN.WEEK (column A) use the same
    // PlanningEngine campaign-relative counter (PublishEngine.ts writes
    // PLAN_LIFECYCLE.week directly from a MANAGER_PLAN row's WEEK value) -
    // comparing them via weeksBetween() only makes sense if BOTH sides use
    // that same counter. Deliberately NOT using isoWeekNumber(DATE) here for
    // the Draft side (unlike Technician Workload above) - that would mix a
    // real calendar week against a campaign-relative one, silently producing
    // a meaningless comparison. PLAN_LIFECYCLE.year is also a flat
    // CONTROL.YEAR setting, not per-row (see PublishEngine.ts) - read the
    // same setting here for the Draft side so both years agree too.
    let controlMapForYear: { [key: string]: string } = {};
    for (let i = 1; i < controlRows.length; i++) {
      if (controlRows[i][0]) {
        controlMapForYear[String(controlRows[i][0])] = String(controlRows[i][1]);
      }
    }
    const projectYear = controlMapForYear["YEAR"] ? Number(controlMapForYear["YEAR"]) : new Date().getFullYear();

    let latestCommitted = { week: 0, year: 0 };
    for (let i = 1; i < planLifecycle.length; i++) {
      const r = planLifecycle[i];
      const status = String(r[2]);
      if (status != "Published" && status != "Active") {
        continue;
      }
      const week = Number(r[1]);
      const year = Number(r[0]);
      if (year > latestCommitted.year || (year == latestCommitted.year && week > latestCommitted.week)) {
        latestCommitted = { week, year };
      }
    }

    let latestDraft = { week: 0, year: projectYear };
    for (let i = 1; i < managerPlan.length; i++) {
      const week = Number(managerPlan[i][0]);
      if (!isNaN(week) && week > latestDraft.week) {
        latestDraft = { week, year: projectYear };
      }
    }

    if (latestCommitted.week > 0) {
      const endOfWeek = new Date(isoMonday(latestCommitted.year, latestCommitted.week));
      endOfWeek.setDate(endOfWeek.getDate() + 6);
      const today = new Date();
      const daysRemaining = Math.round((endOfWeek.getTime() - today.getTime()) / (24 * 3600 * 1000));
      row("Poslední publikovaný/aktivní týden", latestCommitted.year + " / " + latestCommitted.week);
      row("Konec tohoto týdne", endOfWeek.toISOString().slice(0, 10));
      row("Dní do konce publikovaného plánu", daysRemaining);
    } else {
      row("(zatím žádný publikovaný týden - spusť Publish Engine)");
    }

    if (latestDraft.week > 0) {
      row("Poslední naplánovaný (Draft) týden", latestDraft.year + " / " + latestDraft.week);
      if (latestCommitted.week > 0) {
        row(
          "Draft runway (kolik týdnů dopředu je Draft nad rámec publikovaného)",
          weeksBetween(latestCommitted.week, latestCommitted.year, latestDraft.week, latestDraft.year)
        );
      }
    }
  } else {
    row("(PLAN_LIFECYCLE i MANAGER_PLAN jsou prázdné - spusť Planning Engine)");
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
      kpiOpenAlerts += counts[key];
    }
  } else {
    // Note this message is ambiguous by design: an empty ADVISOR_LOG means
    // either "Advisor Engine has never run" or "it ran and found zero
    // alerts" - the two are indistinguishable from this sheet alone (found
    // during end-to-end simulation). Not worth a dedicated "last run" marker
    // for this alone; worth revisiting if it causes real confusion.
    row("(no alerts on record - run Advisor Engine if you have not yet)");
  }

  // ==========================================================================
  // WRITE DASHBOARD
  // ==========================================================================

  // KPI tiles: fixed cells B3/C3/D3/E3 (row index 2, col index 1/2/3/4),
  // matching tools/ux_style.py's build_dashboard_template layout exactly.
  dashWs.getRangeByIndexes(2, 1, 1, 4).setValues([[kpiActivePos, kpiSplnenoVcas, kpiNesplneno, kpiOpenAlerts]]);

  // Detail sections start at row 5 (index 4), leaving rows 1-4 for the
  // title banner and KPI tiles that ux_style.py pre-styled.
  if (output.length > 0) {
    dashWs.getRangeByIndexes(4, 0, output.length, 6).setValues(output);
  }

  // ==========================================================================
  // CHART DATA BLOCKS (columns H:K) - FIXED-size ranges that the native
  // Excel charts tools/ux_style.py built once are bound to. Only the DATA
  // rows are cleared/rewritten here - the label/header rows above each
  // block (H1:K2, H17:K18, H35:I36) are static text ux_style.py wrote once,
  // same pattern as the DASHBOARD title/KPI-tile labels. A chart pointed at
  // a flowing, variable-length range (like the detail sections above) would
  // silently show blank or stale rows once the real row count changed -
  // fixed ranges avoid that at the cost of a cap (12 weeks / 14 technicians
  // / 12 markets), padded with blank rows when there is less data than that.
  // ==========================================================================

  const WEEKLY_CHART_ROWS = 12;
  const WORKLOAD_CHART_ROWS = 14;
  const REGIONAL_CHART_ROWS = 12;

  dashWs.getRangeByIndexes(2, 7, WEEKLY_CHART_ROWS, 4).clear(ExcelScript.ClearApplyTo.contents); // H3:K14
  const weeklyChartRows = chartWeekly.slice(-WEEKLY_CHART_ROWS);
  if (weeklyChartRows.length > 0) {
    dashWs.getRangeByIndexes(2, 7, weeklyChartRows.length, 4).setValues(
      weeklyChartRows.map((w) => [w.label, w.vcas, w.pozde, w.nesplneno])
    );
  }

  dashWs.getRangeByIndexes(18, 7, WORKLOAD_CHART_ROWS, 4).clear(ExcelScript.ClearApplyTo.contents); // H19:K32
  const workloadChartRows = chartWorkload.slice(0, WORKLOAD_CHART_ROWS);
  if (workloadChartRows.length > 0) {
    dashWs.getRangeByIndexes(18, 7, workloadChartRows.length, 4).setValues(
      workloadChartRows.map((w) => [w.tech, w.planned, w.capacity, w.utilization])
    );
  }

  dashWs.getRangeByIndexes(36, 7, REGIONAL_CHART_ROWS, 2).clear(ExcelScript.ClearApplyTo.contents); // H37:I48
  const regionalChartRows = chartRegional.slice(0, REGIONAL_CHART_ROWS);
  if (regionalChartRows.length > 0) {
    dashWs.getRangeByIndexes(36, 7, regionalChartRows.length, 2).setValues(
      regionalChartRows.map((r) => [r.market, r.completionPercent])
    );
  }

  // ==========================================================================
  // WRITE POS_MAP_DATA - one (X, Y) column pair per technician, feeding the
  // MAP sheet's XY scatter chart (tools/ux_style.py) - a territory overview
  // colored by technician (product owner, 2026-07-06, after the manager-
  // analytics review: "území techniků, barva = technik"). No real basemap:
  // this project has no online map service (architecture mandate: no
  // external APIs), so it's a flat scatter of GPS coordinates, same
  // flat-earth approximation already used for distanceKm().
  //
  // Fixed-size (MAX_MAP_TECHS slots x MAX_POS_PER_TECH rows each), same
  // "clear + rewrite a fixed range" pattern as the DASHBOARD chart data
  // blocks above - a real Excel chart cannot be bound to a variable-length
  // range. Technician order is alphabetical and stable across runs, so a
  // given technician keeps the same series color/legend position week to
  // week instead of reshuffling.
  //
  // Chart X = POS_MASTER.gpsY, chart Y = POS_MASTER.gpsX - POS_MASTER's own
  // gpsX/gpsY columns are latitude/longitude respectively (see
  // PlanningEngine.ts/PerformanceEngine.ts's distanceKm(): the "*111" factor
  // is invariant per-degree LATITUDE, applied to the first argument), the
  // reverse of what the column names might suggest - swapped here so the
  // chart reads as a normal north-up map (longitude rightward, latitude
  // upward), not a sideways one.
  // ==========================================================================

  const MAX_MAP_TECHS = 40;
  const MAX_POS_PER_TECH = 700; // real data's largest single territory is ~530 (2026-07-06)

  const mHeadersForMap = (posMaster[0] as string[]).map((h) => String(h));
  const mIdxForMap = (name: string) => mHeadersForMap.indexOf(name);
  let posByTechForMap: { [tech: string]: { x: number; y: number }[] } = {};
  for (let i = 1; i < posMaster.length; i++) {
    const row = posMaster[i];
    if (String(row[mIdxForMap("status")]) != "Active") {
      continue;
    }
    const lat = Number(row[mIdxForMap("gpsX")]);
    const lon = Number(row[mIdxForMap("gpsY")]);
    if (isNaN(lat) || isNaN(lon) || (lat == 0 && lon == 0)) {
      continue; // no GPS on record - skip, don't guess a position
    }
    const override = String(row[mIdxForMap("managerOverrideTechnician")] ?? "");
    const tech = override || String(row[mIdxForMap("assignedTechnician")] ?? "");
    if (!tech) {
      continue;
    }
    if (!posByTechForMap[tech]) {
      posByTechForMap[tech] = [];
    }
    posByTechForMap[tech].push({ x: lon, y: lat });
  }

  const allMapTechs = Object.keys(posByTechForMap).sort();
  const mapTechs = allMapTechs.slice(0, MAX_MAP_TECHS);

  const mapWs = workbook.getWorksheet("POS_MAP_DATA");
  mapWs.getRangeByIndexes(0, 0, 1 + MAX_POS_PER_TECH, MAX_MAP_TECHS * 2).clear(ExcelScript.ClearApplyTo.contents);
  for (let slot = 0; slot < mapTechs.length; slot++) {
    const tech = mapTechs[slot];
    const points = posByTechForMap[tech].slice(0, MAX_POS_PER_TECH);
    mapWs.getRangeByIndexes(0, slot * 2, 1, 1).setValue(tech);
    if (points.length > 0) {
      mapWs.getRangeByIndexes(1, slot * 2, points.length, 2).setValues(points.map((p) => [p.x, p.y]));
    }
  }

  console.log(
    "Reporting Engine: dashboard refreshed, " + output.length + " detail rows + 4 KPI tiles + 3 chart data blocks written. " +
      "POS_MAP_DATA refreshed (" + mapTechs.length + " technician territories" +
      (allMapTechs.length > MAX_MAP_TECHS
        ? ", " + (allMapTechs.length - MAX_MAP_TECHS) + " technician(s) beyond the " + MAX_MAP_TECHS + "-slot cap not shown"
        : "") +
      ")."
  );
}
