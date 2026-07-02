// Unit tests for office-scripts/shared/core.ts (pure logic, no Excel dependency).
// Run with: npx ts-node tests/core.test.ts
// No external test framework needed - Node's built-in assert is enough for
// this size of test suite and keeps the project dependency-free.

import * as assert from "assert";
import {
  POSItem,
  CadenceRule,
  categoryRule,
  computeScore,
  applyPremiumTier,
  pickMandatory,
  selectWeekPOS,
  addGpsBonus,
  geoDays,
  resolveCapacity,
  isoWeekNumber,
  weeksBetween,
  determineComplianceStatus,
  findNeglected,
  computeFailureRateByGroup,
  latestByKey,
  advanceLifecycleStatus,
  computeVolumeTrend,
  findPublishedPlanDrift,
  findUnplannedActivePOS,
} from "../office-scripts/shared/core";

let passed = 0;
let failed = 0;

function test(name: string, fn: () => void) {
  try {
    fn();
    passed++;
    console.log("  PASS  " + name);
  } catch (e) {
    failed++;
    console.log("  FAIL  " + name);
    console.log("        " + (e as Error).message);
  }
}

function makeItem(overrides: Partial<POSItem>): POSItem {
  return {
    pos: "1",
    tech: "TECH_A",
    kategorie: "4OSTATNI",
    classification: "B",
    nazev: "Test Shop",
    ulice: "Main St",
    cislo: "1",
    mesto: "Praha",
    oblast: "Praha",
    posArea: "RSA",
    ppt: 1000,
    x: 50.0,
    y: 14.0,
    weeksSinceLastVisit: null,
    forceInclude: false,
    core: false,
    mandatoryRuleId: null,
    premium: false,
    score: 0,
    reason: "",
    ...overrides,
  };
}

// ==========================================================================
console.log("categoryRule()");
// ==========================================================================

const categoryTable = [
  { key: "STARTS_1", value: "CORE" },
  { key: "1FAST", value: "NORMAL" },
  { key: "1CD", value: "EXCLUDE" },
  { key: "1POSTA", value: "EXCLUDE" },
  { key: "*", value: "NORMAL" },
];

test("exact-match category overrides the STARTS_1 default", () => {
  assert.strictEqual(categoryRule(categoryTable, "1FAST"), "NORMAL");
});
test("unlisted 1-prefixed category falls back to STARTS_1 -> CORE", () => {
  assert.strictEqual(categoryRule(categoryTable, "1GECO"), "CORE");
});
test("category with no rule and no 1-prefix falls back to explicit * default", () => {
  assert.strictEqual(categoryRule(categoryTable, "4OSTATNI"), "NORMAL");
});
test("explicit EXCLUDE category is respected", () => {
  assert.strictEqual(categoryRule(categoryTable, "1CD"), "EXCLUDE");
});
test("missing * default row still falls back to NORMAL (not a crash)", () => {
  const noDefaultTable = categoryTable.filter((r) => r.key != "*");
  assert.strictEqual(categoryRule(noDefaultTable, "4OSTATNI"), "NORMAL");
});

// ==========================================================================
console.log("computeScore()");
// ==========================================================================

const weights = { core: 100000000, kategorizaceA: 10000000, ppt: 1, neglectedBonus: 50000 };

test("CORE weight applied when item.core = true", () => {
  const item = makeItem({ core: true, ppt: 500 });
  const { score } = computeScore(item, weights, 8, 26);
  assert.strictEqual(score, 100000000 + 500);
});
test("KATEGORIZACE A weight applied", () => {
  const item = makeItem({ classification: "A", ppt: 500 });
  const { score } = computeScore(item, weights, 8, 26);
  assert.strictEqual(score, 10000000 + 500);
});
test("gap below minGap without forceInclude is heavily penalized", () => {
  const item = makeItem({ weeksSinceLastVisit: 2, ppt: 500 });
  const { score } = computeScore(item, weights, 8, 26);
  assert.strictEqual(score, 500 - 1000000);
});
test("forceInclude bypasses the gap penalty", () => {
  const item = makeItem({ weeksSinceLastVisit: 2, forceInclude: true, ppt: 500 });
  const { score } = computeScore(item, weights, 8, 26);
  assert.strictEqual(score, 500);
});
test("neglected bonus applies exactly at the threshold (boundary)", () => {
  const item = makeItem({ weeksSinceLastVisit: 26, ppt: 0 });
  const { score, gapReason } = computeScore(item, weights, 8, 26);
  assert.strictEqual(score, 50000);
  assert.strictEqual(gapReason, "NEGLECTED POS | ");
});
test("weeksSinceLastVisit=25 (one below threshold) gets no neglected bonus", () => {
  const item = makeItem({ weeksSinceLastVisit: 25, ppt: 0 });
  const { score } = computeScore(item, weights, 8, 26);
  assert.strictEqual(score, 0);
});
test("null weeksSinceLastVisit (new POS, no history) gets no gap adjustment at all", () => {
  const item = makeItem({ weeksSinceLastVisit: null, ppt: 500 });
  const { score } = computeScore(item, weights, 8, 26);
  assert.strictEqual(score, 500);
});

// ==========================================================================
console.log("applyPremiumTier()");
// ==========================================================================

test("top 20% of a 10-item list are flagged premium (exactly 2)", () => {
  const items = Array.from({ length: 10 }, (_, i) => makeItem({ pos: String(i), score: 100 - i }));
  applyPremiumTier(items, 20);
  const premiumCount = items.filter((i) => i.premium).length;
  assert.strictEqual(premiumCount, 2);
  assert.ok(items[0].premium && items[1].premium);
  assert.ok(!items[2].premium);
});
test("small list (3 items, 20%) rounds up to 1 via ceil - boundary case", () => {
  const items = [makeItem({ pos: "a", score: 3 }), makeItem({ pos: "b", score: 2 }), makeItem({ pos: "c", score: 1 })];
  applyPremiumTier(items, 20);
  assert.strictEqual(items.filter((i) => i.premium).length, 1);
  assert.ok(items.find((i) => i.pos == "a")!.premium);
});
test("empty list does not crash", () => {
  assert.doesNotThrow(() => applyPremiumTier([], 20));
});

// ==========================================================================
console.log("pickMandatory()");
// ==========================================================================

const mandatoryRule: CadenceRule = {
  ruleId: "MANDATORY_9PODNIK",
  scope: "CATEGORY",
  matchValue: ["9PODNIKC", "9PODNIKFC"],
  minGapWeeks: null,
  maxIntervalWeeks: null,
  intervalType: "ONCE_PER_CAMPAIGN",
  guaranteeType: "HARD",
  dedupBy: "ADDRESS",
  campaignChangeOverride: false,
  priority: 100,
};

test("dedup by address keeps only the highest-PPT POS per street+city", () => {
  const list = [
    makeItem({ pos: "A", mandatoryRuleId: "MANDATORY_9PODNIK", ulice: "Hlavni", mesto: "Praha", ppt: 500 }),
    makeItem({ pos: "B", mandatoryRuleId: "MANDATORY_9PODNIK", ulice: "Hlavni", mesto: "Praha", ppt: 900 }),
    makeItem({ pos: "C", mandatoryRuleId: "MANDATORY_9PODNIK", ulice: "Jina", mesto: "Brno", ppt: 100 }),
  ];
  const result = pickMandatory(list, [mandatoryRule]);
  assert.strictEqual(result.length, 2);
  assert.ok(result.find((p) => p.pos == "B"));
  assert.ok(!result.find((p) => p.pos == "A"));
  assert.ok(result.find((p) => p.pos == "C"));
});
test("POS without a mandatoryRuleId are ignored", () => {
  const list = [makeItem({ pos: "A", mandatoryRuleId: null })];
  assert.strictEqual(pickMandatory(list, [mandatoryRule]).length, 0);
});
test("address dedup is case- and diacritics-insensitive (Hlavni vs HLAVNÍ is the same address)", () => {
  const list = [
    makeItem({ pos: "A", mandatoryRuleId: "MANDATORY_9PODNIK", ulice: "Hlavní", mesto: "Praha", ppt: 500 }),
    makeItem({ pos: "B", mandatoryRuleId: "MANDATORY_9PODNIK", ulice: "hlavni", mesto: "PRAHA", ppt: 900 }),
  ];
  const result = pickMandatory(list, [mandatoryRule]);
  assert.strictEqual(result.length, 1); // deduped to the higher-PPT POS (B)
  assert.strictEqual(result[0].pos, "B");
});

// ==========================================================================
console.log("selectWeekPOS()");
// ==========================================================================

test("capacity is never exceeded by the base selection (before GPS bonus)", () => {
  const list = Array.from({ length: 50 }, (_, i) => makeItem({ pos: String(i), score: 50 - i }));
  const result = selectWeekPOS(list, 40, [], false);
  assert.strictEqual(result.length, 40);
});
test("mandatory POS are included even when they would not win on score alone", () => {
  const list = [
    makeItem({ pos: "M", mandatoryRuleId: "MANDATORY_9PODNIK", score: -999, ulice: "X", mesto: "Y" }),
    ...Array.from({ length: 45 }, (_, i) => makeItem({ pos: "n" + i, score: 100 - i })),
  ];
  const result = selectWeekPOS(list, 40, [mandatoryRule], false);
  assert.ok(result.find((p) => p.pos == "M"));
  assert.strictEqual(result.length, 40);
});
test("forceInclude POS are always selected first regardless of score", () => {
  const list = [
    makeItem({ pos: "F", score: -999999, forceInclude: true }),
    ...Array.from({ length: 45 }, (_, i) => makeItem({ pos: "n" + i, score: 100 - i })),
  ];
  const result = selectWeekPOS(list, 40, [], false);
  assert.ok(result.find((p) => p.pos == "F"));
});
test("holdPremium pushes premium items to the back within the same capacity", () => {
  const list = [
    makeItem({ pos: "P1", score: 100, premium: true }),
    makeItem({ pos: "P2", score: 90, premium: true }),
    makeItem({ pos: "N1", score: 50, premium: false }),
  ];
  const result = selectWeekPOS(list, 2, [], true);
  assert.strictEqual(result.length, 2);
  assert.ok(result.find((p) => p.pos == "N1")); // non-premium wins the limited slots
  assert.ok(!result.find((p) => p.pos == "P1") || !result.find((p) => p.pos == "P2"));
});
test("capacity=0 returns only mandatory POS, no crash", () => {
  const list = [
    makeItem({ pos: "M", mandatoryRuleId: "MANDATORY_9PODNIK", ulice: "X", mesto: "Y" }),
    makeItem({ pos: "n1", score: 100 }),
  ];
  const result = selectWeekPOS(list, 0, [mandatoryRule], false);
  assert.strictEqual(result.length, 1);
  assert.strictEqual(result[0].pos, "M");
});
test("empty candidate list returns empty result, no crash", () => {
  assert.deepStrictEqual(selectWeekPOS([], 40, [], false), []);
});

// ==========================================================================
console.log("addGpsBonus()");
// ==========================================================================

test("disabled config returns the selection unchanged", () => {
  const selected = [makeItem({ pos: "A" })];
  const pool = [makeItem({ pos: "A" }), makeItem({ pos: "B" })];
  const result = addGpsBonus(selected, pool, { enabled: false, radiusMeters: 300, maxVisits: 5 });
  assert.strictEqual(result.length, 1);
});
test("POS just within the radius is added, just outside is not (boundary)", () => {
  const anchor = makeItem({ pos: "ANCHOR", x: 50.0, y: 14.0 });
  // ~250m away (well within 300m radius)
  const near = makeItem({ pos: "NEAR", x: 50.002, y: 14.0 });
  // ~1.1km away (outside 300m radius)
  const far = makeItem({ pos: "FAR", x: 50.01, y: 14.0 });
  const result = addGpsBonus([anchor], [anchor, near, far], {
    enabled: true,
    radiusMeters: 300,
    maxVisits: 5,
  });
  assert.ok(result.find((p) => p.pos == "NEAR"), "expected NEAR to be added");
  assert.ok(!result.find((p) => p.pos == "FAR"), "expected FAR to be excluded");
});
test("maxVisits caps the total bonus additions across all anchors", () => {
  const anchor1 = makeItem({ pos: "A1", x: 50.0, y: 14.0 });
  const anchor2 = makeItem({ pos: "A2", x: 50.0, y: 14.1 });
  const nearby = Array.from({ length: 10 }, (_, i) =>
    makeItem({ pos: "extra" + i, x: 50.0 + 0.0001 * (i + 1), y: 14.0 })
  );
  const result = addGpsBonus([anchor1, anchor2], [anchor1, anchor2, ...nearby], {
    enabled: true,
    radiusMeters: 5000, // generous radius so all "nearby" items qualify
    maxVisits: 5,
  });
  assert.strictEqual(result.length, 2 + 5); // 2 anchors + at most 5 bonus
});

// ==========================================================================
console.log("geoDays()");
// ==========================================================================

test("perDayTarget adapts to selection size larger than a fixed constant would allow (V10.5.5 bug fix)", () => {
  // 45 selected items (e.g. 40 capacity + 5 GPS bonus) across 5 days - every
  // item must be placed, none silently dropped like V10.5.5's addNearby() bug.
  const items = Array.from({ length: 45 }, (_, i) => makeItem({ pos: String(i), score: 45 - i, x: 50 + i * 0.001, y: 14 }));
  const days = [
    { day: "MON", dateIso: "2026-07-27" },
    { day: "TUE", dateIso: "2026-07-28" },
    { day: "WED", dateIso: "2026-07-29" },
    { day: "THU", dateIso: "2026-07-30" },
    { day: "FRI", dateIso: "2026-07-31" },
  ];
  const result = geoDays(items, days);
  assert.strictEqual(result.length, 45, "every selected POS must be placed, none lost");
});
test("empty days list (fully-holiday week) returns empty result without crashing", () => {
  const items = [makeItem({ pos: "A" })];
  assert.deepStrictEqual(geoDays(items, []), []);
});
test("empty item list returns empty result without crashing", () => {
  const days = [{ day: "MON", dateIso: "2026-07-27" }];
  assert.deepStrictEqual(geoDays([], days), []);
});

// ==========================================================================
console.log("resolveCapacity()");
// ==========================================================================

test("uses override when present", () => {
  const overrideMap = { "Novak|2026|42": 32 };
  assert.strictEqual(resolveCapacity(overrideMap, "Novak", 2026, 42, 5, 8), 32);
});
test("falls back to workDays x targetPerDay when no override", () => {
  assert.strictEqual(resolveCapacity({}, "Novak", 2026, 43, 5, 8), 40);
});
test("override of 0 is respected (not treated as missing)", () => {
  const overrideMap = { "Novak|2026|44": 0 };
  assert.strictEqual(resolveCapacity(overrideMap, "Novak", 2026, 44, 5, 8), 0);
});

// ==========================================================================

// ==========================================================================
console.log("isoWeekNumber() / weeksBetween()");
// ==========================================================================

test("isoWeekNumber matches the known campaign week (2026-07-27 = week 31, per real CONTROL/OUTPUT_PLAN data)", () => {
  const { week, year } = isoWeekNumber(new Date(2026, 6, 27)); // 27 July 2026, Monday
  assert.strictEqual(week, 31);
  assert.strictEqual(year, 2026);
});
test("isoWeekNumber matches the real SalesApp data range (2026-06-01 = week 23)", () => {
  const { week } = isoWeekNumber(new Date(2026, 5, 1));
  assert.strictEqual(week, 23);
});
test("weeksBetween is 0 for the same week", () => {
  assert.strictEqual(weeksBetween(31, 2026, 31, 2026), 0);
});
test("weeksBetween is positive when the second week is later", () => {
  assert.strictEqual(weeksBetween(31, 2026, 33, 2026), 2);
});
test("weeksBetween accounts for year boundary (52-week approximation, documented simplification)", () => {
  assert.strictEqual(weeksBetween(51, 2026, 2, 2027), 3);
});

// Year-boundary regression suite (added when ComplianceEngine.ts moved from
// a single flat "plannedYear" per run to a true per-row ISO week/year
// derived from each planned row's own DATE - see ComplianceEngine.ts
// "MATCH MANAGER_PLAN_PUBLISHED -> COMPLIANCE_LOG"). isoWeekNumber() itself
// was not changed by that fix - these tests exist to pin its already-correct
// ISO-8601 behavior at every boundary case the fix depends on, so a future
// change to this function cannot silently reintroduce the bug it fixed.
test("isoWeekNumber: ordinary mid-year date (regression baseline, unaffected by year boundary)", () => {
  const { week, year } = isoWeekNumber(new Date(2026, 5, 15)); // 15 June 2026, Monday
  assert.strictEqual(week, 25);
  assert.strictEqual(year, 2026);
});
test("isoWeekNumber: ISO week 1 of a year starting on Thursday (2026-01-01 is a Thursday)", () => {
  const { week, year } = isoWeekNumber(new Date(2026, 0, 1));
  assert.strictEqual(week, 1);
  assert.strictEqual(year, 2026);
});
test("isoWeekNumber: ISO week 53 exists for 2026 (2026-12-28, the last Monday of the year)", () => {
  const { week, year } = isoWeekNumber(new Date(2026, 11, 28));
  assert.strictEqual(week, 53);
  assert.strictEqual(year, 2026);
});
test("isoWeekNumber: Jan 1 2027 belongs to ISO week 53 of 2026, not week 1 of 2027 (the classic ISO edge case)", () => {
  const { week, year } = isoWeekNumber(new Date(2027, 0, 1));
  assert.strictEqual(week, 53);
  assert.strictEqual(year, 2026);
});
test("isoWeekNumber: first Monday of 2027 is correctly ISO week 1 of 2027", () => {
  const { week, year } = isoWeekNumber(new Date(2027, 0, 4));
  assert.strictEqual(week, 1);
  assert.strictEqual(year, 2027);
});
test("isoWeekNumber: December->January transition within the same ISO week (2026-12-31 and 2027-01-01 both week 53/2026)", () => {
  const dec31 = isoWeekNumber(new Date(2026, 11, 31));
  const jan1 = isoWeekNumber(new Date(2027, 0, 1));
  assert.deepStrictEqual(dec31, { week: 53, year: 2026 });
  assert.deepStrictEqual(jan1, { week: 53, year: 2026 });
});

// ==========================================================================
console.log("determineComplianceStatus()");
// ==========================================================================

test("visit realized in the planned week = Splneno_vcas", () => {
  const status = determineComplianceStatus(31, 2026, [{ week: 31, year: 2026 }], 1, 33, 2026);
  assert.strictEqual(status, "Splneno_vcas");
});
test("visit realized one week late = Splneno_pozde", () => {
  const status = determineComplianceStatus(31, 2026, [{ week: 32, year: 2026 }], 1, 33, 2026);
  assert.strictEqual(status, "Splneno_pozde");
});
test("visit realized very late (beyond cutoff) is still Splneno_pozde, not Nesplneno - it did happen", () => {
  const status = determineComplianceStatus(31, 2026, [{ week: 40, year: 2026 }], 1, 41, 2026);
  assert.strictEqual(status, "Splneno_pozde");
});
test("no visit yet, deadline not reached = Pending", () => {
  const status = determineComplianceStatus(31, 2026, [], 1, 31, 2026);
  assert.strictEqual(status, "Pending");
});
test("no visit, exactly at the cutoff boundary = still Pending (not yet failed)", () => {
  const status = determineComplianceStatus(31, 2026, [], 1, 32, 2026);
  assert.strictEqual(status, "Pending");
});
test("no visit, one week past the cutoff = Nesplneno", () => {
  const status = determineComplianceStatus(31, 2026, [], 1, 33, 2026);
  assert.strictEqual(status, "Nesplneno");
});
test("multiple actual visits - earliest one determines the status", () => {
  const status = determineComplianceStatus(
    31, 2026,
    [{ week: 33, year: 2026 }, { week: 31, year: 2026 }],
    1, 33, 2026
  );
  assert.strictEqual(status, "Splneno_vcas");
});

// ==========================================================================
console.log("findNeglected()");
// ==========================================================================

test("POS at or beyond the threshold are flagged", () => {
  const items = [
    { posId: "A", weeksSinceLastVisit: 26 },
    { posId: "B", weeksSinceLastVisit: 25 },
    { posId: "C", weeksSinceLastVisit: 30 },
  ];
  assert.deepStrictEqual(findNeglected(items, 26), ["A", "C"]);
});
test("null weeksSinceLastVisit (never visited, no history) is not flagged - not enough information, not a false positive", () => {
  const items = [{ posId: "A", weeksSinceLastVisit: null }];
  assert.deepStrictEqual(findNeglected(items, 26), []);
});
test("empty list returns empty, no crash", () => {
  assert.deepStrictEqual(findNeglected([], 26), []);
});

// ==========================================================================
console.log("computeFailureRateByGroup()");
// ==========================================================================

test("failure rate is computed correctly per group", () => {
  const rows = [
    { group: "Novak", status: "Nesplneno" },
    { group: "Novak", status: "Nesplneno" },
    { group: "Novak", status: "Splneno_vcas" },
    { group: "Svoboda", status: "Splneno_vcas" },
  ];
  const result = computeFailureRateByGroup(rows, ["Nesplneno"]);
  const novak = result.find((r) => r.group == "Novak")!;
  const svoboda = result.find((r) => r.group == "Svoboda")!;
  assert.strictEqual(novak.total, 3);
  assert.strictEqual(novak.failed, 2);
  assert.ok(Math.abs(novak.rate - 2 / 3) < 1e-9);
  assert.strictEqual(svoboda.rate, 0);
});
test("rows with an empty group are skipped (e.g. unresolved extra-visit executor)", () => {
  const rows = [
    { group: "", status: "Navic_evidovano" },
    { group: "Novak", status: "Splneno_vcas" },
  ];
  const result = computeFailureRateByGroup(rows, ["Nesplneno"]);
  assert.strictEqual(result.length, 1);
  assert.strictEqual(result[0].group, "Novak");
});
test("multiple failure statuses can be counted together", () => {
  const rows = [
    { group: "Novak", status: "Nesplneno" },
    { group: "Novak", status: "Splneno_pozde" },
    { group: "Novak", status: "Splneno_vcas" },
  ];
  const result = computeFailureRateByGroup(rows, ["Nesplneno", "Splneno_pozde"]);
  assert.strictEqual(result[0].failed, 2);
});
test("empty input returns empty output, no crash", () => {
  assert.deepStrictEqual(computeFailureRateByGroup([], ["Nesplneno"]), []);
});
test("regression: undeduped repeated evaluations of the same visit dilute the rate - this is why callers must dedupe with latestByKey first (real bug found in AdvisorEngine.ts via end-to-end simulation)", () => {
  // Same visit evaluated twice (Pending, then later Nesplneno) without dedup:
  const undeduped = [
    { group: "Novak", status: "Pending" },
    { group: "Novak", status: "Nesplneno" },
  ];
  const wrongRate = computeFailureRateByGroup(undeduped, ["Nesplneno"])[0].rate;
  assert.ok(Math.abs(wrongRate - 0.5) < 1e-9, "undeduped input understates the true 100% failure rate");

  // Correct usage: dedupe to the latest evaluation per subject first.
  const timestamped = [
    { key: "Novak|POS1", timestamp: "2026-08-01T00:00:00Z", group: "Novak", status: "Pending" },
    { key: "Novak|POS1", timestamp: "2026-08-08T00:00:00Z", group: "Novak", status: "Nesplneno" },
  ];
  const deduped = latestByKey(timestamped);
  const correctRate = computeFailureRateByGroup(deduped, ["Nesplneno"])[0].rate;
  assert.strictEqual(correctRate, 1);
});

// ==========================================================================
console.log("latestByKey()");
// ==========================================================================

test("keeps only the row with the newest timestamp per key", () => {
  const rows = [
    { key: "A|31", timestamp: "2026-08-01T00:00:00Z", status: "Pending" },
    { key: "A|31", timestamp: "2026-08-08T00:00:00Z", status: "Nesplneno" },
    { key: "B|31", timestamp: "2026-08-01T00:00:00Z", status: "Splneno_vcas" },
  ];
  const result = latestByKey(rows);
  const a = result.find((r) => r.key == "A|31")!;
  assert.strictEqual(a.status, "Nesplneno");
  assert.strictEqual(result.length, 2);
});
test("single row per key is returned unchanged", () => {
  const rows = [{ key: "A", timestamp: "2026-08-01T00:00:00Z" }];
  assert.deepStrictEqual(latestByKey(rows), rows);
});
test("empty input returns empty output, no crash", () => {
  assert.deepStrictEqual(latestByKey([]), []);
});

// ==========================================================================
console.log("advanceLifecycleStatus()");
// ==========================================================================

test("Draft never auto-advances - only PublishEngine moves it", () => {
  assert.strictEqual(advanceLifecycleStatus("Draft", true, true), "Draft");
  assert.strictEqual(advanceLifecycleStatus("Draft", false, false), "Draft");
});
test("Closed is terminal - never reopens even if flags suggest otherwise", () => {
  assert.strictEqual(advanceLifecycleStatus("Closed", false, true), "Closed");
});
test("Published stays Published if Monday hasn't passed and visits are still pending", () => {
  assert.strictEqual(advanceLifecycleStatus("Published", false, true), "Published");
});
test("Published becomes Active once Monday has passed and visits are still pending", () => {
  assert.strictEqual(advanceLifecycleStatus("Published", true, true), "Active");
});
test("Published closes immediately if no visits are pending, even before Monday", () => {
  assert.strictEqual(advanceLifecycleStatus("Published", false, false), "Closed");
});
test("Active closes once no visits are pending", () => {
  assert.strictEqual(advanceLifecycleStatus("Active", true, false), "Closed");
});
test("Active stays Active while visits are still pending", () => {
  assert.strictEqual(advanceLifecycleStatus("Active", true, true), "Active");
});
test("Active never regresses to Published even if mondayHasPassed is somehow false (monotonic - time doesn't run backward, found by exhaustive case enumeration)", () => {
  assert.strictEqual(advanceLifecycleStatus("Active", false, true), "Active");
});

// ==========================================================================
console.log("computeVolumeTrend()");
// ==========================================================================

function weeks(counts: number[], startYear = 2026, startWeek = 1) {
  return counts.map((count, i) => ({ year: startYear, week: startWeek + i, count }));
}

test("not enough history yet returns null (correct 'stay silent', not an error)", () => {
  assert.strictEqual(computeVolumeTrend(weeks([10, 10, 10]), 4, 4, 25), null);
});
test("baseline average of zero returns null (ratio against zero is meaningless)", () => {
  assert.strictEqual(computeVolumeTrend(weeks([0, 0, 0, 0, 10, 10, 10, 10]), 4, 4, 25), null);
});
test("stable volume is not flagged significant", () => {
  const signal = computeVolumeTrend(weeks([10, 10, 10, 10, 10, 10, 10, 10]), 4, 4, 25);
  assert.strictEqual(signal!.significant, false);
  assert.strictEqual(signal!.ratioPercent, 100);
});
test("a large increase is flagged significant with the correct ratio", () => {
  const signal = computeVolumeTrend(weeks([10, 10, 10, 10, 20, 20, 20, 20]), 4, 4, 25);
  assert.strictEqual(signal!.trailingAvg, 20);
  assert.strictEqual(signal!.baselineAvg, 10);
  assert.strictEqual(signal!.ratioPercent, 200);
  assert.strictEqual(signal!.significant, true);
});
test("a large decrease is flagged significant too (not just increases)", () => {
  const signal = computeVolumeTrend(weeks([20, 20, 20, 20, 10, 10, 10, 10]), 4, 4, 25);
  assert.strictEqual(signal!.ratioPercent, 50);
  assert.strictEqual(signal!.significant, true);
});
test("a small deviation under the threshold is not flagged", () => {
  const signal = computeVolumeTrend(weeks([10, 10, 10, 10, 11, 11, 11, 11]), 4, 4, 25);
  assert.strictEqual(signal!.significant, false);
});
test("unsorted input is sorted internally before windowing", () => {
  const shuffled = [
    { year: 2026, week: 3, count: 20 },
    { year: 2026, week: 1, count: 10 },
    { year: 2026, week: 4, count: 20 },
    { year: 2026, week: 2, count: 10 },
  ];
  const signal = computeVolumeTrend(shuffled, 2, 2, 25);
  assert.strictEqual(signal!.baselineAvg, 10);
  assert.strictEqual(signal!.trailingAvg, 20);
});

// ==========================================================================
console.log("findPublishedPlanDrift()");
// ==========================================================================

test("flags a POS that is Active in the plan but Closed in POS_MASTER", () => {
  const alerts = findPublishedPlanDrift(
    [{ posId: "POS1", plannedTechnician: "Novak" }],
    { POS1: { status: "Closed", assignedTechnician: "Novak" } }
  );
  assert.strictEqual(alerts.length, 1);
  assert.strictEqual(alerts[0].type, "CLOSED_POS_IN_PLAN");
});
test("flags a POS whose technician was reassigned since publish", () => {
  const alerts = findPublishedPlanDrift(
    [{ posId: "POS1", plannedTechnician: "Novak" }],
    { POS1: { status: "Active", assignedTechnician: "Svoboda" } }
  );
  assert.strictEqual(alerts.length, 1);
  assert.strictEqual(alerts[0].type, "TECHNICIAN_REASSIGNED");
  assert.strictEqual(alerts[0].plannedTechnician, "Novak");
  assert.strictEqual(alerts[0].currentTechnician, "Svoboda");
});
test("a POS can be flagged for both reasons at once (closed AND reassigned)", () => {
  const alerts = findPublishedPlanDrift(
    [{ posId: "POS1", plannedTechnician: "Novak" }],
    { POS1: { status: "Closed", assignedTechnician: "Svoboda" } }
  );
  assert.strictEqual(alerts.length, 2);
});
test("no drift when POS_MASTER still matches the published plan exactly", () => {
  const alerts = findPublishedPlanDrift(
    [{ posId: "POS1", plannedTechnician: "Novak" }],
    { POS1: { status: "Active", assignedTechnician: "Novak" } }
  );
  assert.strictEqual(alerts.length, 0);
});
test("a POS appearing in several still-open weeks is only flagged once per reason", () => {
  const alerts = findPublishedPlanDrift(
    [
      { posId: "POS1", plannedTechnician: "Novak" },
      { posId: "POS1", plannedTechnician: "Novak" },
    ],
    { POS1: { status: "Closed", assignedTechnician: "Novak" } }
  );
  assert.strictEqual(alerts.length, 1);
});
test("a row with no matching POS_MASTER entry is skipped, not flagged", () => {
  const alerts = findPublishedPlanDrift([{ posId: "GHOST", plannedTechnician: "Novak" }], {});
  assert.strictEqual(alerts.length, 0);
});
test("empty input returns empty output, no crash", () => {
  assert.deepStrictEqual(findPublishedPlanDrift([], {}), []);
});

// ==========================================================================
console.log("findUnplannedActivePOS()");
// ==========================================================================

test("finds an Active POS absent from the ever-planned set", () => {
  const result = findUnplannedActivePOS(["POS1", "POS2"], new Set(["POS1"]));
  assert.deepStrictEqual(result, ["POS2"]);
});
test("returns nothing when every Active POS has been planned at some point", () => {
  const result = findUnplannedActivePOS(["POS1", "POS2"], new Set(["POS1", "POS2"]));
  assert.deepStrictEqual(result, []);
});
test("empty input returns empty output, no crash", () => {
  assert.deepStrictEqual(findUnplannedActivePOS([], new Set()), []);
});

console.log("\n" + passed + " passed, " + failed + " failed");
if (failed > 0) {
  process.exit(1);
}
