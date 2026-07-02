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

export function resolveCapacity(
  overrideMap: { [key: string]: number },
  tech: string,
  year: number,
  week: number,
  workDaysCount: number,
  targetVisitsPerDay: number
): number {
  const key = tech + "|" + year + "|" + week;
  return overrideMap[key] !== undefined ? overrideMap[key] : workDaysCount * targetVisitsPerDay;
}
