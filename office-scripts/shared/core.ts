// Pure, Excel-independent business logic for Planning Engine.
//
// This is the authoritative, unit-tested source of truth for scoring/selection
// logic (see tests/core.test.ts). PlanningEngine.ts (the deployable Office
// Script) contains a synced copy of this logic inlined into main(), because
// Office Scripts cannot import across files - see office-scripts/README.md.
// When you change behaviour here, re-sync PlanningEngine.ts and re-run the
// tests before deploying.
//
// Why this file exists as a separate module (architectural change, does not
// alter business logic or workflow): the original plan was to keep
// PlanningEngine.ts fully self-contained and verify it only via an ad-hoc
// Python simulation. That works, but it means every future change to scoring/
// selection logic has no repeatable, fast, automated check - exactly the
// testability problem that was the whole argument for choosing Office
// Scripts over VBA in the first place. Extracting the ExcelScript-independent
// core into a plain TypeScript module lets it run under a normal Node test
// runner (see tests/core.test.ts), which is faster and more repeatable than
// re-deriving a Python simulation by hand every time.

export interface POSItem {
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

export interface CadenceRule {
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

export interface ScoreWeights {
  core: number;
  kategorizaceA: number;
  ppt: number;
  neglectedBonus: number;
}

export function distanceKm(ax: number, ay: number, bx: number, by: number): number {
  const dx = (ax - bx) * 111;
  const dy = (ay - by) * 72;
  return Math.sqrt(dx * dx + dy * dy);
}

// Category filter with an explicit "*" default row - replaces V10.5.5's
// implicit startsWith("1")->CORE / else NORMAL code fallback with a visible
// config row (docs/BUSINESS_RULES.md 15b).
export function categoryRule(
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

export function computeScore(item: POSItem, weights: ScoreWeights, minGap: number, neglectedAfter: number): { score: number; gapReason: string } {
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

export interface GeoClusterConfig {
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
export function computeGeoClusterBonus(
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

export function applyPremiumTier(items: POSItem[], premiumPercent: number): void {
  const sorted = [...items].sort((a, b) => b.score - a.score);
  const limit = Math.ceil((sorted.length * premiumPercent) / 100);
  const premiumSet = new Set(sorted.slice(0, limit).map((i) => i.pos));
  for (const item of items) {
    item.premium = premiumSet.has(item.pos);
  }
}

// Uppercase + strip combining diacritics, same semantics as text.ts's norm()
// (kept as a local copy here since core.ts must stay Excel-independent and
// this file is not allowed to import across files either, for consistency
// with the rest of office-scripts/shared/).
function normalizeAddressKey(v: string): string {
  return v
    .toUpperCase()
    .normalize("NFD")
    .replace(/[\u0300-\u036f]/g, "")
    .trim();
}

// Whether a CadenceRule's scope/matchValue covers a given POS - CATEGORY and
// CATEGORYPREFIX match on category, MARKET matches on market (added for
// CORN, whose real business condition is `market = CORN`, not a category).
// Callers pass already-normalized (norm()'d) strings - this function does no
// normalization itself, consistent with categoryRule() above.
export function matchesCadenceRuleScope(
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

// Whether a POS is overdue for a RECURRING+HARD cadence rule (e.g. CORN,
// GECO): never visited (weeksSinceLastVisit is null), or at/beyond
// maxIntervalWeeks since the last real visit. A rule with no
// maxIntervalWeeks configured can never trigger this (nothing to be overdue
// against) - deliberately conservative rather than treating "no interval"
// as "always overdue".
export function isOverdueForCadenceRule(rule: CadenceRule, weeksSinceLastVisit: number | null): boolean {
  return (
    rule.maxIntervalWeeks != null &&
    (weeksSinceLastVisit === null || weeksSinceLastVisit >= rule.maxIntervalWeeks)
  );
}

export function pickMandatory(list: POSItem[], mandatoryRules: CadenceRule[]): POSItem[] {
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

export function selectWeekPOS(
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

export interface GpsBonusConfig {
  enabled: boolean;
  radiusMeters: number;
  maxVisits: number;
}

// Corrected spec (docs/BUSINESS_RULES.md 6a): deliberate, bounded overflow
// beyond capacity for very close POS - not a hard cap. Every POS added here
// must later be placed by geoDays() (perDayTarget is derived from the actual
// list length, not a fixed constant) so nothing is silently lost, which was
// the actual V10.5.5 defect (not the overflow behaviour itself).
export function addGpsBonus(selected: POSItem[], pool: POSItem[], config: GpsBonusConfig): POSItem[] {
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

export interface WorkDay {
  day: string;
  dateIso: string;
}

export function geoDays(
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

// ============================================================================
// COMPLIANCE (Planning Engine and Compliance Engine share this module so the
// week arithmetic is defined exactly once and tested exactly once)
// ============================================================================

// Standard ISO-8601 week numbering (Monday-start weeks, week containing the
// year's first Thursday is week 1). This is the inverse of PlanningEngine's
// isoMonday()/workDays() (which go week -> date); this goes date -> week.
export function isoWeekNumber(date: Date): { week: number; year: number } {
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

// Simplified week-distance: treats every year as 52 weeks. Good enough for
// comparing weeks within the same campaign/quarter; a visit planned in week
// 52 of one year vs. week 1 of the next would be off by up to 1 week in
// edge cases. Flagged here rather than hidden - not worth a full calendar
// model for what is currently only used to classify "how late" a visit was.
export function weeksBetween(week1: number, year1: number, week2: number, year2: number): number {
  return week2 - week1 + (year2 - year1) * 52;
}

export type ComplianceStatus =
  | "Splneno_vcas"
  | "Splneno_pozde"
  | "Nesplneno"
  | "Pending";

// "Pending" (not one of the four states in BUSINESS_RULES.md section 12) is
// added here deliberately: a planned visit whose deadline hasn't arrived yet
// is not the same as "Nesplneno" (failed). Without a live clock inside the
// workbook, `latestKnownWeek/Year` (the newest week present in the SalesApp
// import) is used as the reference "now" - a data-driven proxy, not a guess,
// but worth flagging: this means Nesplneno is only ever assigned in
// hindsight, once a later SalesApp import has been processed.
export function determineComplianceStatus(
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

// Capacity is fundamentally a WEEKLY number - a per-technician/week
// CAPACITY_OVERRIDE always wins if present. Below that, targetVisitsWeek
// (a flat weekly target, e.g. CONTROL.TARGET_VISITS_WEEK) is used directly
// if configured (product owner, 2026-07-03: wants to work in weekly
// capacity, not derive it from a daily rate). Only if NEITHER a per-
// technician override NOR a flat weekly target exists does this fall back
// to the original workDaysCount x targetVisitsPerDay derivation (still
// useful as a holiday-aware default, and kept for backward compatibility -
// existing workbooks with only TARGET_VISITS_DAY configured keep working
// exactly as before).
export function resolveCapacity(
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

// ============================================================================
// ADVISOR (diagnostic only - these functions never select or exclude a POS
// from a plan, they only classify already-known facts into alerts. Advisor
// Engine writes their output to ADVISOR_LOG, never to MANAGER_PLAN or
// POS_MASTER's decision fields - see docs/BUSINESS_RULES.md section 13.)
// ============================================================================

export interface NeglectCandidate {
  posId: string;
  weeksSinceLastVisit: number | null;
}

// Returns posIds at or beyond thresholdWeeks since their last real visit.
// Called twice by Advisor Engine (once per configured threshold) to produce
// WARNING vs CRITICAL tiers - this function itself has no notion of severity,
// it is a plain threshold classifier, kept that way so it is trivial to
// reason about and test.
export function findNeglected(items: NeglectCandidate[], thresholdWeeks: number): string[] {
  return items
    .filter((i) => i.weeksSinceLastVisit !== null && i.weeksSinceLastVisit >= thresholdWeeks)
    .map((i) => i.posId);
}

export interface ComplianceOutcome {
  group: string; // technician name or region name - caller decides the grouping
  status: string;
}

export interface GroupFailureRate {
  group: string;
  total: number;
  failed: number;
  rate: number; // failed / total, in [0, 1]
}

// Generic grouped failure-rate calculator, reused for both "technician
// overload" and "regional underperformance" alerts (docs/BUSINESS_RULES.md
// section 13) - same shape, different grouping key, so one implementation.
// Rows with an empty group are skipped (e.g. "Navic_evidovano" rows with no
// resolved technician - see ComplianceEngine.ts file header).
//
// CALLER RESPONSIBILITY: this function counts every row it is given. If the
// source is an append-only log re-evaluated on every engine run (like
// COMPLIANCE_LOG), the caller MUST dedupe to one row per logical subject
// first (see latestByKey below) - otherwise re-running the upstream engine
// repeatedly dilutes the computed rate. This exact bug shipped once in
// AdvisorEngine.ts (found via end-to-end simulation, not unit tests, since
// it only appears after the same week is evaluated more than once) - see
// docs/ARCHITECTURE.md Phase 6 notes.
export function computeFailureRateByGroup(
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

// ============================================================================
// REPORTING (Reporting Engine computes nothing new - see ARCHITECTURE.md
// section 5 - it only aggregates. This helper handles one recurring need:
// append-only logs like COMPLIANCE_LOG/ADVISOR_LOG can contain several rows
// for the same logical subject over time (re-evaluated on each engine run);
// a dashboard should show current state, i.e. only the newest row per key.)
// ============================================================================

export interface TimestampedRow {
  key: string;
  timestamp: string; // ISO string, lexicographically comparable
}

export function latestByKey<T extends TimestampedRow>(rows: T[]): T[] {
  let latest: { [key: string]: T } = {};
  for (const row of rows) {
    if (!latest[row.key] || row.timestamp > latest[row.key].timestamp) {
      latest[row.key] = row;
    }
  }
  return Object.values(latest);
}

// ============================================================================
// PLAN LIFECYCLE (Draft -> Published -> Active -> Closed)
// ============================================================================
// Draft->Published only ever happens via an explicit manager action
// (PublishEngine.ts), never automatically - this function does not produce
// that transition. Published->Active->Closed are mechanical/derived from
// data already available (today's date vs. the week's Monday, and whether
// any planned visit for that week is still Pending) and are recomputed by
// Compliance Engine on every run - see docs/BUSINESS_RULES.md section 11.

export type PlanStatus = "Draft" | "Published" | "Active" | "Closed";

export function advanceLifecycleStatus(
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

// ============================================================================
// PLANNING CYCLE ADVISOR (docs/ARCHITECTURE.md section 19) - v1 is a
// deterministic moving-average heuristic, deliberately NOT a predictive
// model. The point of this function's signature (plain historical counts
// in, a plain signal out - no dependency on any specific sheet, engine, or
// even on what "week" means to the caller) is that a future statistical or
// ML-based recommender can be swapped in behind the exact same call site in
// AdvisorEngine.ts without changing the data model or any other engine -
// see the architecture doc for the full reasoning. Advisory only: it never
// writes a plan, only a signal a human reads in ADVISOR_LOG.
// ============================================================================

export interface WeeklyVolume {
  week: number;
  year: number;
  count: number;
}

export interface VolumeTrendSignal {
  trailingAvg: number;
  baselineAvg: number;
  ratioPercent: number; // trailingAvg / baselineAvg * 100, rounded to 1 decimal
  significant: boolean;
}

// Compares the average weekly visit count over the most recent
// `trailingWindow` weeks against the `baselineWindow` weeks immediately
// before that. Returns null when there isn't yet enough history to compare
// (expected and correct during the first weeks of use, not an error state -
// callers should treat null as "stay silent", not as a failure) or when the
// baseline average is zero (a ratio against zero is meaningless).
export function computeVolumeTrend(
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

// ============================================================================
// PUBLISHED PLAN DRIFT (docs/ARCHITECTURE.md section 21) - diagnostic only,
// same AdvisorEngine.ts contract as everything else in this file: never
// changes MANAGER_PLAN_PUBLISHED or POS_MASTER, only flags that the two have
// drifted apart since publish. A Published/Active week is a commitment
// already sent to technicians (PublishEngine.ts) - Planning Engine will
// never touch it again, by design (docs/BUSINESS_RULES.md section 11).
// These functions answer "does that frozen commitment still match today's
// POS_MASTER reality", not "should the plan be regenerated" - the answer to
// that second question is always a human decision.
// ============================================================================

export interface OpenPlanRow {
  posId: string;
  plannedTechnician: string;
}

export interface POSCurrentState {
  status: string; // "Active" | "Closed"
  assignedTechnician: string;
}

export interface DriftAlert {
  posId: string;
  type: "CLOSED_POS_IN_PLAN" | "TECHNICIAN_REASSIGNED";
  plannedTechnician: string;
  currentTechnician: string;
}

// openPlanRows: one entry per (posId, still-open published week) - callers
// should already have filtered to Published/Active weeks only (a Closed
// week is history, not a live commitment, and Draft weeks never reach this
// function at all since they aren't in MANAGER_PLAN_PUBLISHED). Rows whose
// posId has no POS_MASTER entry are skipped (nothing to compare against -
// a genuinely missing POS_MASTER row is a different, unrelated data
// problem, not "drift").
export function findPublishedPlanDrift(
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

// Active POS that have never appeared in any published plan at all - a
// different signal from findPublishedPlanDrift above (that one is about a
// commitment going stale; this one is about a POS that was never part of
// any commitment yet, typically because it's new since the last publish).
export function findUnplannedActivePOS(activePosIds: string[], everPlannedPosIds: Set<string>): string[] {
  return activePosIds.filter((posId) => !everPlannedPosIds.has(posId));
}
