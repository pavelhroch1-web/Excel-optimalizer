// ============================================================================
// FIELD FORCE OPTIMIZER V11 - PLANNING ENGINE (first functional Generate Plan)
// ============================================================================
// Deployable Office Script - paste the whole file into Excel's Code Editor.
// Run AFTER Import Engine (POS_MASTER must be populated).
//
// SCOPE (deliberately narrow, per product-owner request "co nejdriv funkcni
// Generate Plan nad realnymi daty"):
//   - Reads POS_MASTER (not RAW_DATA) - Import Engine already ran.
//   - Filters: TERMINAL_RULES, MARKET_RULES, CATEGORY_RULES (config-driven,
//     replaces V10.5.5's hardcoded categoryRule() fallback with the explicit
//     "*" default row).
//   - Cadence: CORE (SOFT_HIGH_WEIGHT, evolution of V10.5.5's score constant)
//     and MANDATORY (config-driven, generalizes V10.5.5's hardcoded 9PODNIK
//     check - reads scope/matchValue/dedupBy from CADENCE_RULES instead of a
//     literal string). GECO/CORN rows are seeded `active=NO` in CADENCE_RULES
//     and are skipped entirely by this engine until activated - not guessed.
//   - Pareto: PER_TECHNICIAN top-20%, exactly preserving V10.5.5 behaviour,
//     scope read from PARETO_GROUPS (switchable later without code changes).
//   - Campaign hold-back: preserves V10.5.5's campaignChangeSoon() reorder.
//   - GPS bonus: corrected spec (docs/BUSINESS_RULES.md 6a) - bounded,
//     capacity-aware overflow, tagged "GPS BONUS", every selected POS is
//     guaranteed a day slot (fixes the V10.5.5 addNearby() silent-loss bug).
//   - Geo cluster bonus (added 2026-07-06): a small score nudge toward
//     candidates near OTHER valuable candidates for the same technician -
//     product owner, after real generated plans showed a p90 daily route of
//     ~118km (worst case 311km/9 visits): "chci tourplany, co davaji smysl
//     z hlediska prinosu i trasy". Value stays the primary selection driver
//     (see computeGeoClusterBonus's own comment for why the bonus is capped
//     small) - this only nudges which near-tied candidates get picked, it
//     does not reorder core/classification/neglected priority.
//   - Capacity: CAPACITY_OVERRIDE table if present, else workDays x
//     TARGET_VISITS_DAY (holiday-adjusted, no external calendar).
//   - Output: MANAGER_PLAN only, with a REASON text column (structured
//     SCORE_LOG breakdown deferred - noted as a follow-up, not blocking).
//   - Manual overrides: FORCE_EXCLUDE always removes a POS. FORCE_INCLUDE
//     bypasses Filters (proposed default from BUSINESS_RULES.md 10, not yet
//     formally confirmed - flagged here, not silently buried).
//   - NOT in this version: Advisor Engine, Plan lifecycle (Draft/Published/
//     Active/Closed), TECHNICIAN_PLAN, SEASONAL_STRATEGY profile switching.
//
// SYNC NOTE: the scoring/selection algorithm (categoryRule, computeScore,
// applyPremiumTier, pickMandatory, selectWeekPOS, addGpsBonus, geoDays,
// resolveCapacity) is copied VERBATIM from office-scripts/shared/core.ts,
// which is the unit-tested source of truth (tests/core.test.ts). Everything
// between a SYNC-BLOCK-START/END pair must be byte-identical to core.ts
// (module-level `export ` keywords aside) - run `python3 tools/check_sync.py`
// after any change to either file. Reason-string tagging ("PREMIUM |",
// "GPS BONUS |", "NEARBY |") is deliberately NOT part of core.ts (it's
// presentation, not selection logic) and is added here as a thin wrapper
// around the synced functions instead.
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

  // SYNC-BLOCK-START: geo.ts
  function distanceKm(ax: number, ay: number, bx: number, by: number): number {
    const dx = (ax - bx) * 111;
    const dy = (ay - by) * 72;
    return Math.sqrt(dx * dx + dy * dy);
  }
  // SYNC-BLOCK-END: geo.ts

  // ==========================================================================
  // SYNC-BLOCK-START: core.ts (planning)
  // Verbatim from office-scripts/shared/core.ts - do not hand-edit here.
  // ==========================================================================

  interface POSItem {
    pos: string;
    tech: string;
    kategorie: string;
    market: string;
    classification: string;
    nazev: string;
    ulice: string;
    cislo: string;
    mesto: string;
    oblast: string;
    posArea: string;
    ppt: number;
    x: number;
    y: number;
    weeksSinceLastVisit: number | null;
    forceInclude: boolean;
    core: boolean;
    mandatoryRuleId: string | null;
    premium: boolean;
    score: number;
    reason: string;
  }

  interface CadenceRule {
    ruleId: string;
    scope: string; // normalized: "CATEGORY" | "CATEGORYPREFIX" | "MARKET"
    matchValue: string[]; // normalized
    minGapWeeks: number | null;
    maxIntervalWeeks: number | null;
    intervalType: string; // "RECURRING" | "ONCE_PER_CAMPAIGN"
    guaranteeType: string; // "HARD" | "SOFT_HIGH_WEIGHT"
    dedupBy: string; // "NONE" | "ADDRESS"
    campaignChangeOverride: boolean;
    priority: number;
  }

  interface ScoreWeights {
    core: number;
    kategorizaceA: number;
    ppt: number;
    neglectedBonus: number;
  }

  function categoryRule(
    categoryRulesTable: { key: string; value: string }[], // key/value already normalized (upper, no diacritics)
    categoryNormalized: string
  ): string {
    let starPrefixRule: string | null = null;
    for (const row of categoryRulesTable) {
      if (row.key == categoryNormalized) {
        return row.value; // exact match always wins immediately
      }
      if (row.key == "STARTS_1" && categoryNormalized.startsWith("1")) {
        starPrefixRule = row.value;
      }
      if (row.key == "*") {
        starPrefixRule = starPrefixRule ?? row.value;
      }
    }
    return starPrefixRule ?? "NORMAL";
  }

  function computeScore(item: POSItem, weights: ScoreWeights, minGap: number, neglectedAfter: number): { score: number; gapReason: string } {
    let gapAdjustment = 0;
    let gapReason = "";
    if (item.weeksSinceLastVisit !== null) {
      if (item.weeksSinceLastVisit < minGap && !item.forceInclude) {
        gapAdjustment = -1000000;
      }
      if (item.weeksSinceLastVisit >= neglectedAfter) {
        gapAdjustment += weights.neglectedBonus;
        gapReason = "NEGLECTED POS | ";
      }
    }
    const score =
      (item.core ? weights.core : 0) +
      (item.classification == "A" ? weights.kategorizaceA : 0) +
      item.ppt * weights.ppt +
      gapAdjustment;
    return { score, gapReason };
  }

  interface GeoClusterConfig {
    radiusKm: number;
    bonusFactor: number;
    maxBonus: number;
  }

  // Small score nudge toward geographic clustering - product owner (2026-07-06,
  // after reviewing real generated plans: p90 daily route was ~118km, worst
  // case 311km for 9 visits): "chci tourplany, co davaji smysl z hlediska
  // prinosu i trasy". Confirmed approach: a SMALL bonus for being near other
  // valuable candidates, not a route-first redesign - value stays the primary
  // driver (see docs/BUSINESS_RULES.md).
  //
  // Must be called AFTER every item in a technician's candidate pool has its
  // base computeScore() already set - "nearby" bonuses are based on neighbors'
  // REAL base value, not inflated by their own cluster bonus (that would
  // double-count clustering as clusters formed, snowballing without bound).
  // maxBonus caps the total so this can only break near-ties within the same
  // core/classification/premium tier - it can never outweigh being CORE
  // (weights.core) or classification A (weights.kategorizaceA), only nudge
  // selection order among otherwise-similar candidates toward ones that keep
  // the technician's day tighter.
  function computeGeoClusterBonus(
    item: POSItem,
    allItemsForTech: POSItem[],
    config: GeoClusterConfig
  ): number {
    if (item.x == 0 && item.y == 0) {
      return 0; // no GPS on record - can't judge proximity, no bonus
    }
    let bonus = 0;
    for (const other of allItemsForTech) {
      if (other.pos == item.pos || (other.x == 0 && other.y == 0)) {
        continue;
      }
      if (distanceKm(item.x, item.y, other.x, other.y) <= config.radiusKm) {
        bonus += other.score * config.bonusFactor;
      }
    }
    return Math.min(bonus, config.maxBonus);
  }

  function applyPremiumTier(items: POSItem[], premiumPercent: number): void {
    const sorted = [...items].sort((a, b) => b.score - a.score);
    const limit = Math.ceil((sorted.length * premiumPercent) / 100);
    const premiumSet = new Set(sorted.slice(0, limit).map((i) => i.pos));
    for (const item of items) {
      item.premium = premiumSet.has(item.pos);
    }
  }

  function normalizeAddressKey(v: string): string {
    return v
      .toUpperCase()
      .normalize("NFD")
      .replace(/[\u0300-\u036f]/g, "")
      .trim();
  }

  function matchesCadenceRuleScope(
    rule: CadenceRule,
    categoryNormalized: string,
    marketNormalized: string
  ): boolean {
    return (
      (rule.scope == "CATEGORY" && rule.matchValue.includes(categoryNormalized)) ||
      (rule.scope == "CATEGORYPREFIX" && rule.matchValue.some((p) => categoryNormalized.startsWith(p))) ||
      (rule.scope == "MARKET" && rule.matchValue.includes(marketNormalized))
    );
  }

  function isOverdueForCadenceRule(rule: CadenceRule, weeksSinceLastVisit: number | null): boolean {
    return (
      rule.maxIntervalWeeks != null &&
      (weeksSinceLastVisit === null || weeksSinceLastVisit >= rule.maxIntervalWeeks)
    );
  }

  function pickMandatory(list: POSItem[], mandatoryRules: CadenceRule[]): POSItem[] {
    let byAddress: { [key: string]: POSItem } = {};
    let noDedup: POSItem[] = [];
    for (const p of list) {
      if (!p.mandatoryRuleId) {
        continue;
      }
      const rule = mandatoryRules.find((r) => r.ruleId == p.mandatoryRuleId);
      if (rule && rule.dedupBy == "ADDRESS") {
        const key = normalizeAddressKey(p.ulice + "|" + p.mesto);
        if (!byAddress[key] || p.ppt > byAddress[key].ppt) {
          byAddress[key] = p;
        }
      } else {
        noDedup.push(p);
      }
    }
    return [...Object.values(byAddress), ...noDedup];
  }

  function selectWeekPOS(
    list: POSItem[],
    capacity: number,
    mandatoryRules: CadenceRule[],
    holdPremium: boolean
  ): POSItem[] {
    let result: POSItem[] = [];
    const mandatory = pickMandatory(list, mandatoryRules);
    let remainingCapacity = capacity;
    for (const m of mandatory) {
      result.push(m);
      remainingCapacity--;
    }
    let candidates = list.filter((p) => !result.includes(p));
    candidates.sort((a, b) => {
      if (a.forceInclude != b.forceInclude) {
        return a.forceInclude ? -1 : 1;
      }
      if (holdPremium) {
        const ap = a.premium ? 1 : 0;
        const bp = b.premium ? 1 : 0;
        if (ap != bp) {
          return ap - bp;
        }
      }
      return b.score - a.score;
    });
    while (result.length < capacity && candidates.length > 0) {
      const p = candidates.shift();
      if (p) {
        result.push(p);
      }
    }
    return result;
  }

  interface GpsBonusConfig {
    enabled: boolean;
    radiusMeters: number;
    maxVisits: number;
  }

  function addGpsBonus(selected: POSItem[], pool: POSItem[], config: GpsBonusConfig): POSItem[] {
    if (!config.enabled) {
      return selected;
    }
    let result = [...selected];
    let added = 0;
    const radiusKm = config.radiusMeters / 1000;
    for (const anchor of selected) {
      if (added >= config.maxVisits) {
        break;
      }
      let near = pool
        .filter((p) => !result.includes(p) && distanceKm(anchor.x, anchor.y, p.x, p.y) <= radiusKm)
        .sort((a, b) => b.score - a.score);
      for (const n of near) {
        if (added >= config.maxVisits) {
          break;
        }
        result.push(n);
        added++;
      }
    }
    return result;
  }

  interface WorkDay {
    day: string;
    dateIso: string;
  }

  function geoDays(
    list: POSItem[],
    days: WorkDay[]
  ): { pos: POSItem; day: string; dateIso: string; group: number }[] {
    let remaining = [...list];
    let result: { pos: POSItem; day: string; dateIso: string; group: number }[] = [];
    let group = 1;
    const perDayTarget = days.length > 0 ? Math.ceil(list.length / days.length) : 0;
    for (const d of days) {
      if (remaining.length == 0) {
        break;
      }
      remaining.sort((a, b) => b.score - a.score);
      const anchor = remaining.shift();
      if (!anchor) {
        break;
      }
      result.push({ pos: anchor, day: d.day, dateIso: d.dateIso, group });
      remaining.sort(
        (a, b) => distanceKm(anchor.x, anchor.y, a.x, a.y) - distanceKm(anchor.x, anchor.y, b.x, b.y)
      );
      const take = Math.min(perDayTarget - 1, remaining.length);
      for (let i = 0; i < take; i++) {
        const near = remaining.shift();
        if (near) {
          result.push({ pos: near, day: d.day, dateIso: d.dateIso, group });
        }
      }
      group++;
    }
    return result;
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

  // SYNC-BLOCK-END: core.ts (planning)

  // ==========================================================================
  // LOAD SHEETS
  // ==========================================================================

  function readTable(sheetName: string): (string | number | boolean)[][] {
    const ws = workbook.getWorksheet(sheetName);
    const range = ws.getUsedRange();
    return range ? range.getValues() : [];
  }

  const posMaster = readTable("POS_MASTER");
  const control = readTable("CONTROL");
  const activity = readTable("ACTIVITY_PLAN");
  const terminals = readTable("TERMINAL_RULES");
  const markets = readTable("MARKET_RULES");
  const categoryRulesRaw = readTable("CATEGORY_RULES");
  const cadenceRulesRaw = readTable("CADENCE_RULES");
  const paretoGroups = readTable("PARETO_GROUPS");
  const scoreProfiles = readTable("SCORE_PROFILES");
  const capacityOverride = readTable("CAPACITY_OVERRIDE");
  const planLifecycle = readTable("PLAN_LIFECYCLE");
  const existingManagerPlan = readTable("MANAGER_PLAN");

  const outWs = workbook.getWorksheet("MANAGER_PLAN");

  // ==========================================================================
  // CONFIG READERS (adapter code - reshapes sheet rows into the plain
  // structures the synced core.ts functions above expect)
  // ==========================================================================

  function setting(name: string, fallback: number): number {
    for (let i = 1; i < control.length; i++) {
      if (norm(String(control[i][0])) == norm(name)) {
        const v = Number(control[i][1]);
        return isNaN(v) ? fallback : v;
      }
    }
    return fallback;
  }

  // Distinct from setting(): returns null when the row is absent/blank
  // rather than a numeric fallback, so callers can tell "not configured"
  // apart from "configured as zero".
  function settingOptional(name: string): number | null {
    for (let i = 1; i < control.length; i++) {
      if (norm(String(control[i][0])) == norm(name)) {
        const raw = control[i][1];
        if (raw === "" || raw === undefined || raw === null) {
          return null;
        }
        const v = Number(raw);
        return isNaN(v) ? null : v;
      }
    }
    return null;
  }

  const START_WEEK = setting("CAMPAIGN_START_WEEK", 30);
  const CAMPAIGN_LENGTH = setting("CAMPAIGN_LENGTH", 4);
  const TARGET_DAY = setting("TARGET_VISITS_DAY", 8);
  // Optional: a flat weekly capacity target, used instead of deriving
  // capacity from workDays x TARGET_VISITS_DAY when configured - product
  // owner (2026-07-03) wants to work with weekly capacity as the primary
  // unit. Per-technician/week CAPACITY_OVERRIDE still wins over this if
  // both are present - see resolveCapacity() below.
  const TARGET_WEEK = settingOptional("TARGET_VISITS_WEEK");
  const STANDARD_GAP = setting("STANDARD_VISIT_GAP", 8);
  const NEGLECTED_AFTER = setting("NEGLECTED_AFTER_WEEKS", 26);
  const YEAR = setting("YEAR", new Date().getFullYear());
  const SYNC_WINDOW = setting("SYNC_WINDOW_WEEKS", 1);
  const GPS_CONFIG: GpsBonusConfig = {
    enabled: setting("GPS_EXTRA_ENABLED", 0) === 1,
    radiusMeters: setting("GPS_EXTRA_RADIUS_METERS", 300),
    maxVisits: setting("GPS_EXTRA_MAX_VISITS", 5),
  };
  // GEO_CLUSTER config (see computeGeoClusterBonus's comment above) -
  // confirmed defaults (product owner, 2026-07-06): 3km radius, 1% of a
  // neighbor's own score per neighbor, capped well below the smallest
  // meaningful score tier (neglectedBonus=50000) so it only nudges selection
  // order among near-ties, never overrides core/classification/neglected.
  const GEO_CLUSTER_CONFIG: GeoClusterConfig = {
    radiusKm: setting("GEO_CLUSTER_RADIUS_KM", 3),
    bonusFactor: setting("GEO_CLUSTER_BONUS_FACTOR", 0.01),
    maxBonus: setting("GEO_CLUSTER_MAX_BONUS", 5000),
  };

  // PLAN LIFECYCLE: a week that has been Published/Active/Closed is locked -
  // Planning Engine must never regenerate or overwrite its rows (this is
  // what makes the later Published snapshot in MANAGER_PLAN_PUBLISHED
  // trustworthy). Only Draft weeks (or weeks with no PLAN_LIFECYCLE row yet,
  // i.e. never touched before) are freely regenerated on every run. Single-
  // year scope assumed (matches core.ts's weeksBetween 52-week
  // simplification elsewhere) - a week number alone identifies the lock.
  let lockedWeeks = new Set<number>();
  if (planLifecycle.length >= 2) {
    const plHeaders = (planLifecycle[0] as string[]).map((h) => String(h));
    const plIdx = (name: string) => plHeaders.indexOf(name);
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

  // Existing MANAGER_PLAN rows belonging to a locked week are preserved
  // as-is; only Draft-week rows are dropped and regenerated below.
  let keptRows: (string | number)[][] = [];
  if (existingManagerPlan.length >= 2) {
    for (let i = 1; i < existingManagerPlan.length; i++) {
      const row = existingManagerPlan[i];
      if (!row[0]) {
        continue;
      }
      if (lockedWeeks.has(Number(row[0]))) {
        keptRows.push(row as (string | number)[]);
      }
    }
  }

  let activeTerms: string[] = [];
  for (let i = 1; i < terminals.length; i++) {
    if (norm(String(terminals[i][1])) == "YES") {
      activeTerms.push(norm(String(terminals[i][0])));
    }
  }
  function terminalOK(v: string): boolean {
    const value = norm(v);
    return activeTerms.some((t) => value.includes(t));
  }

  let activeMarkets: string[] = [];
  for (let i = 1; i < markets.length; i++) {
    if (norm(String(markets[i][1])) == "YES") {
      activeMarkets.push(norm(String(markets[i][0])));
    }
  }
  function marketOK(v: string): boolean {
    return activeMarkets.includes(norm(v));
  }

  // CATEGORY_RULES sheet rows -> {key,value}[] for categoryRule()
  let categoryRulesTable: { key: string; value: string }[] = [];
  for (let i = 1; i < categoryRulesRaw.length; i++) {
    categoryRulesTable.push({
      key: norm(String(categoryRulesRaw[i][0])),
      value: norm(String(categoryRulesRaw[i][1])),
    });
  }

  const cadHeaders = (cadenceRulesRaw[0] as string[]).map((h) => String(h));
  const cIdx = (name: string) => cadHeaders.indexOf(name);
  let activeCadenceRules: CadenceRule[] = [];
  for (let i = 1; i < cadenceRulesRaw.length; i++) {
    const row = cadenceRulesRaw[i];
    if (norm(String(row[cIdx("active")])) != "YES") {
      continue;
    }
    activeCadenceRules.push({
      ruleId: String(row[cIdx("ruleId")]),
      scope: norm(String(row[cIdx("scope")])),
      matchValue: String(row[cIdx("matchValue")])
        .split(";")
        .map((s) => norm(s))
        .filter((s) => s.length > 0),
      minGapWeeks: row[cIdx("minGapWeeks")] === "" ? null : Number(row[cIdx("minGapWeeks")]),
      maxIntervalWeeks:
        row[cIdx("maxIntervalWeeks")] === "" ? null : Number(row[cIdx("maxIntervalWeeks")]),
      intervalType: norm(String(row[cIdx("intervalType")])),
      guaranteeType: norm(String(row[cIdx("guaranteeType")])),
      dedupBy: norm(String(row[cIdx("dedupBy")])),
      campaignChangeOverride: norm(String(row[cIdx("campaignChangeOverride")])) == "YES",
      priority: Number(row[cIdx("priority")]) || 0,
    });
  }
  const coreRule = activeCadenceRules.find((r) => r.ruleId == "CORE") || null;
  const mandatoryRules = activeCadenceRules.filter(
    (r) => r.intervalType == "ONCE_PER_CAMPAIGN" && r.guaranteeType == "HARD"
  );
  // RECURRING + HARD (e.g. CORN, GECO once activated): "must be visited at
  // least every maxIntervalWeeks weeks", enforced on an ongoing basis, not
  // just once per campaign - see the overdue-matching loop below. Distinct
  // from mandatoryRules (ONCE_PER_CAMPAIGN) above and from coreRule
  // (SOFT_HIGH_WEIGHT, a scoring boost, not a hard guarantee).
  const recurringHardRules = activeCadenceRules.filter(
    (r) => r.intervalType == "RECURRING" && r.guaranteeType == "HARD"
  );
  // Passed to pickMandatory()/selectWeekPOS() so its dedupBy lookup resolves
  // correctly for BOTH kinds of forced-inclusion rule, not just the
  // ONCE_PER_CAMPAIGN ones.
  const allHardRules = [...mandatoryRules, ...recurringHardRules];

  let premiumPercent = 20;
  const parHeaders = (paretoGroups[0] as string[]).map((h) => String(h));
  const pIdx = (name: string) => parHeaders.indexOf(name);
  for (let i = 1; i < paretoGroups.length; i++) {
    const row = paretoGroups[i];
    if (String(row[pIdx("tierId")]) == "PREMIUM_TOP20" && norm(String(row[pIdx("active")])) == "YES") {
      premiumPercent = Number(row[pIdx("boundaryValue")]) || 20;
    }
  }
  // premiumScope column exists and is read implicitly via PARETO_GROUPS above,
  // but only PER_TECHNICIAN is implemented here - see docs/BACKLOG.md.

  let weights: { [component: string]: number } = {};
  for (let i = 1; i < scoreProfiles.length; i++) {
    const row = scoreProfiles[i];
    if (norm(String(row[0])) == "DEFAULT") {
      weights[norm(String(row[1]))] = Number(row[2]) || 0;
    }
  }
  const SCORE_WEIGHTS: ScoreWeights = {
    core: weights["CORE"] ?? 100000000,
    kategorizaceA: weights["KATEGORIZACE_A"] ?? 10000000,
    ppt: weights["PPT"] ?? 1,
    neglectedBonus: weights["NEGLECTED_BONUS"] ?? 50000,
  };

  let capacityOverrideMap: { [key: string]: number } = {};
  for (let i = 1; i < capacityOverride.length; i++) {
    const row = capacityOverride[i];
    if (!row[0]) {
      continue;
    }
    capacityOverrideMap[String(row[0]) + "|" + String(row[1]) + "|" + String(row[2])] = Number(row[3]);
  }

  let los: { [week: number]: string } = {};
  let lot: { [week: number]: string } = {};
  for (let i = 1; i < activity.length; i++) {
    const row = activity[i];
    if (!row[0]) {
      continue;
    }
    for (let w = Number(row[2]); w <= Number(row[3]); w++) {
      if (norm(String(row[0])) == "LOS") {
        los[w] = String(row[1]);
      }
      if (norm(String(row[0])) == "LOT") {
        lot[w] = String(row[1]);
      }
    }
  }
  function campaignChangeSoon(week: number): boolean {
    for (let i = 1; i <= SYNC_WINDOW; i++) {
      const future = week + i;
      if (los[week] != los[future] && los[future]) {
        return true;
      }
      if (lot[week] != lot[future] && lot[future]) {
        return true;
      }
    }
    return false;
  }

  // ==========================================================================
  // BUILD CANDIDATE LIST FROM POS_MASTER
  // ==========================================================================

  const mHeaders = (posMaster[0] as string[]).map((h) => String(h));
  const midx = (name: string) => mHeaders.indexOf(name);

  let groups: { [tech: string]: POSItem[] } = {};

  for (let i = 1; i < posMaster.length; i++) {
    const r = posMaster[i];
    if (!r[midx("posId")]) {
      continue;
    }
    if (String(r[midx("status")]) != "Active") {
      continue; // Closed POS are never candidates (docs/BUSINESS_RULES.md section 2)
    }

    const overrideType = norm(String(r[midx("managerOverrideType")] ?? ""));
    if (overrideType == "FORCE_EXCLUDE") {
      continue; // manual override always wins - never even enters the pool
    }
    const forceInclude = overrideType == "FORCE_INCLUDE";

    const category = String(r[midx("category")]);
    const rule = categoryRule(categoryRulesTable, norm(category));
    const passesFilters =
      terminalOK(String(r[midx("terminalType")])) &&
      marketOK(String(r[midx("market")])) &&
      rule != "EXCLUDE";

    // FORCE_INCLUDE bypasses Filters entirely - proposed default per
    // docs/BUSINESS_RULES.md section 10, not yet formally reconfirmed.
    if (!passesFilters && !forceInclude) {
      continue;
    }

    const tech = String(
      r[midx("managerOverrideTechnician")] || r[midx("assignedTechnician")]
    );
    const weeksSince =
      r[midx("weeksSinceLastVisit")] === "" || r[midx("weeksSinceLastVisit")] === undefined
        ? null
        : Number(r[midx("weeksSinceLastVisit")]);

    const item: POSItem = {
      pos: String(r[midx("posId")]),
      tech: tech,
      kategorie: category,
      market: String(r[midx("market")]),
      classification: String(r[midx("classification")]),
      nazev: String(r[midx("nazev")]),
      ulice: String(r[midx("street")]),
      cislo: String(r[midx("houseNumber")]),
      mesto: String(r[midx("city")]),
      oblast: String(r[midx("area")]),
      posArea: String(r[midx("posArea")]),
      ppt: Number(r[midx("ppt")]) || 0,
      x: Number(r[midx("gpsX")]) || 0,
      y: Number(r[midx("gpsY")]) || 0,
      weeksSinceLastVisit: weeksSince,
      forceInclude: forceInclude,
      core: rule == "CORE",
      mandatoryRuleId: null,
      premium: false,
      score: 0,
      reason: "",
    };

    for (const mr of mandatoryRules) {
      if (matchesCadenceRuleScope(mr, norm(category), norm(item.market))) {
        item.mandatoryRuleId = mr.ruleId;
        break;
      }
    }

    // RECURRING + HARD overdue check (CORN/GECO): only if no ONCE_PER_CAMPAIGN
    // rule already claimed this item above - forces it through the same
    // pickMandatory()/selectWeekPOS() path, bypassing scored competition,
    // for whichever week of THIS run it's first overdue in. A POS with
    // maxIntervalWeeks >= CAMPAIGN_LENGTH (true for both CORN=4 and GECO=5
    // against the current 4-week campaign default) is naturally forced at
    // most once per run either way - see docs/BUSINESS_RULES.md for the
    // "at most once per Planning run" scoping note on this simplification.
    if (!item.mandatoryRuleId) {
      for (const rr of recurringHardRules) {
        if (
          matchesCadenceRuleScope(rr, norm(category), norm(item.market)) &&
          isOverdueForCadenceRule(rr, weeksSince)
        ) {
          item.mandatoryRuleId = rr.ruleId;
          break;
        }
      }
    }

    // NEW CAMPAIGN OVERRIDE min-gap exception from V10.5.5 is deferred - see
    // docs/BACKLOG.md (needs Compliance Engine's currentLosActivity/
    // currentLotActivity comparison, which is a separate, already-tracked gap).
    const minGap = item.core && coreRule ? coreRule.minGapWeeks ?? 2 : STANDARD_GAP;
    const { score, gapReason } = computeScore(item, SCORE_WEIGHTS, minGap, NEGLECTED_AFTER);
    item.score = score;
    item.reason += gapReason;

    if (!groups[tech]) {
      groups[tech] = [];
    }
    groups[tech].push(item);
  }

  // GEO CLUSTER BONUS (see computeGeoClusterBonus's comment above) - all
  // bonuses are computed from each item's BASE score first, THEN applied,
  // so a bonus never leaks into another item's bonus calculation within the
  // same pass (order-independent, matches the function's own "must be
  // called after base score is set" contract).
  for (const tech of Object.keys(groups)) {
    const bonuses = groups[tech].map((item) => computeGeoClusterBonus(item, groups[tech], GEO_CLUSTER_CONFIG));
    groups[tech].forEach((item, i) => {
      item.score += bonuses[i];
    });
  }

  // PREMIUM / PARETO TOP-20% (PER_TECHNICIAN, preserves V10.5.5 behaviour)
  for (const tech of Object.keys(groups)) {
    applyPremiumTier(groups[tech], premiumPercent);
  }

  // ==========================================================================
  // GENERATE PLAN
  // ==========================================================================

  // POS already committed in a locked week (per technician) must not be
  // re-selected for a Draft week - otherwise the same POS could appear
  // twice in the same campaign run.
  let committedByTech: { [tech: string]: Set<string> } = {};
  for (const row of keptRows) {
    const tech = String(row[3]);
    const posId = String(row[4]);
    if (!committedByTech[tech]) {
      committedByTech[tech] = new Set<string>();
    }
    committedByTech[tech].add(posId);
  }

  let output: (string | number)[][] = [];
  let touchedWeeks = new Set<number>();

  for (const tech of Object.keys(groups)) {
    let used: POSItem[] = groups[tech].filter((p) => committedByTech[tech]?.has(p.pos));
    for (let w = 0; w < CAMPAIGN_LENGTH; w++) {
      const week = START_WEEK + w;
      if (lockedWeeks.has(week)) {
        continue; // locked - existing rows already carried over via keptRows
      }
      touchedWeeks.add(week);
      const days = workDays(YEAR, week);
      const capacity = resolveCapacity(capacityOverrideMap, tech, YEAR, week, days.length, TARGET_DAY, TARGET_WEEK);

      if (capacity <= 0 || days.length == 0) {
        continue; // technician has zero capacity this week - skip cleanly
      }

      const available = groups[tech].filter((p) => !used.includes(p));
      const holdPremium = campaignChangeSoon(week);
      const baseSelection = selectWeekPOS(available, capacity, allHardRules, holdPremium);
      const preGpsIds = new Set(baseSelection.map((p) => p.pos));
      const selected = addGpsBonus(baseSelection, available, GPS_CONFIG);

      // Reason tagging (presentation, not selection logic - see file header):
      // GPS bonus additions are whatever addGpsBonus added beyond baseSelection.
      for (const p of selected) {
        if (p.mandatoryRuleId && !p.reason.includes("MANDATORY")) {
          p.reason += "MANDATORY (" + p.mandatoryRuleId + ") | ";
        } else if (!preGpsIds.has(p.pos)) {
          p.reason += "GPS BONUS | ";
        } else if (p.premium) {
          p.reason += "PREMIUM | ";
        }
      }

      const workDayInputs: WorkDay[] = days.map((d) => ({
        day: d.day,
        dateIso: d.date.toLocaleDateString("cs-CZ"),
      }));
      const planned = geoDays(selected, workDayInputs);

      // Only mark as used the POS that geoDays() actually placed - fixes the
      // V10.5.5 defect where selected-but-unplaced POS were silently
      // consumed without ever being visited or logged.
      let seenInGroup: { [group: number]: boolean } = {};
      for (const row of planned) {
        used.push(row.pos);
        if (!seenInGroup[row.group]) {
          seenInGroup[row.group] = true; // first item in a group = anchor
        } else if (!row.pos.reason.includes("NEARBY")) {
          row.pos.reason += "NEARBY | ";
        }
      }

      for (const row of planned) {
        const p = row.pos;
        let reason = "";
        if (p.core) {
          reason += "CORE | ";
        }
        reason += p.reason;
        output.push([
          week, row.dateIso, row.day, tech, p.pos,
          p.kategorie, p.nazev, p.ulice, p.cislo, p.mesto, p.oblast, p.posArea,
          p.ppt, los[week] || "", lot[week] || "", reason, row.group,
        ]);
      }
    }
  }

  // Locked-week rows (keptRows) + freshly generated Draft-week rows together
  // make up the new MANAGER_PLAN content. Locked rows are never rewritten
  // with different values - they are copied through byte-for-byte.
  const combined = [...keptRows, ...output];
  // contents only - see ImportEngine.ts for why (preserves ux_style.py formatting)
  outWs.getRange("A2:Q200000").clear(ExcelScript.ClearApplyTo.contents); // 17 output columns = A..Q
  if (combined.length > 0) {
    outWs.getRangeByIndexes(1, 0, combined.length, 17).setValues(combined);
  }

  // Register any newly-touched week in PLAN_LIFECYCLE as Draft, if it has no
  // row yet. Existing rows (Draft or locked) are left untouched here -
  // Draft->Published only happens via PublishEngine.ts, and
  // Published->Active->Closed only via ComplianceEngine.ts.
  if (touchedWeeks.size > 0) {
    const plWs = workbook.getWorksheet("PLAN_LIFECYCLE");
    const plExisting = plWs.getUsedRange();
    const plRows = plExisting ? plExisting.getValues() : [];
    let knownWeeks = new Set<number>();
    for (let i = 1; i < plRows.length; i++) {
      if (Number(plRows[i][0]) == YEAR) {
        knownWeeks.add(Number(plRows[i][1]));
      }
    }
    const newLifecycleRows: (string | number)[][] = [];
    for (const week of touchedWeeks) {
      if (!knownWeeks.has(week)) {
        newLifecycleRows.push([YEAR, week, "Draft", "", ""]);
      }
    }
    if (newLifecycleRows.length > 0) {
      const startRow = plRows.length > 0 ? plRows.length : 1;
      plWs.getRangeByIndexes(startRow, 0, newLifecycleRows.length, 5).setValues(newLifecycleRows);
    }
  }

  console.log(
    "Planning Engine: generated " + output.length + " new planned visits (" +
      keptRows.length + " locked-week visits carried over unchanged) across " +
      Object.keys(groups).length + " technicians (weeks " + START_WEEK + "-" +
      (START_WEEK + CAMPAIGN_LENGTH - 1) + ")."
  );
}
