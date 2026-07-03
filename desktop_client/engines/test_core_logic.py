"""
Plain-assert unit tests for core_logic.py, mirroring a subset of
tests/core.test.ts's cases so the Python port has its own fast, Node-free
regression net (in addition to the full cross-language check in
tools/sim/compare_engines.py, which requires ts-node and real/seed data).

Usage: python3 desktop_client/engines/test_core_logic.py
"""
from __future__ import annotations

import sys

from core_logic import (
    CadenceRule,
    GpsBonusConfig,
    POSItem,
    ScoreWeights,
    WorkDay,
    add_gps_bonus,
    apply_premium_tier,
    category_rule,
    compute_score,
    distance_km,
    geo_days,
    is_overdue_for_cadence_rule,
    matches_cadence_rule_scope,
    norm,
    pick_mandatory,
    resolve_capacity,
    select_week_pos,
)

passed = 0
failed = 0


def check(name: str, condition: bool):
    global passed, failed
    if condition:
        passed += 1
    else:
        failed += 1
        print(f"FAIL: {name}")


def make_item(pos, score=0, core=False, classification="B", ppt=0, x=0, y=0, weeks=None, force=False, mand=None):
    return POSItem(
        pos=pos, tech="T1", kategorie="9PODNIK", market="OSTATNI", classification=classification, nazev="",
        ulice="U", cislo="1", mesto="M", oblast="O", posArea="PA", ppt=ppt, x=x, y=y, weeksSinceLastVisit=weeks,
        forceInclude=force, core=core, mandatoryRuleId=mand, score=score,
    )


# --- norm() ---
check("norm uppercases and strips diacritics", norm("Příliš žluťoučký") == "PRILIS ZLUTOUCKY")

# --- categoryRule ---
table = [{"key": "1XYZ", "value": "CORE"}, {"key": "STARTS_1", "value": "PREMIUM"}, {"key": "*", "value": "NORMAL"}]
check("categoryRule exact match wins", category_rule(table, "1XYZ") == "CORE")
check("categoryRule STARTS_1 fallback", category_rule(table, "1ABC") == "PREMIUM")
check("categoryRule default *", category_rule(table, "ZZZ") == "NORMAL")
check("categoryRule empty table default NORMAL", category_rule([], "ANYTHING") == "NORMAL")

# --- computeScore ---
weights = ScoreWeights(core=100, kategorizaceA=10, ppt=1, neglectedBonus=50)
item = make_item("P1", core=True, classification="A", ppt=5, weeks=10)
score, reason = compute_score(item, weights, min_gap=8, neglected_after=26)
check("computeScore core+A+ppt", score == 100 + 10 + 5)
check("computeScore no neglect reason under threshold", reason == "")

item_gap = make_item("P2", weeks=3)
score2, _ = compute_score(item_gap, weights, min_gap=8, neglected_after=26)
check("computeScore below minGap penalized", score2 <= -1000000)

item_neglect = make_item("P3", weeks=30)
score3, reason3 = compute_score(item_neglect, weights, min_gap=8, neglected_after=26)
check("computeScore neglected bonus applied", score3 == weights.neglectedBonus)
check("computeScore neglected reason tagged", "NEGLECTED" in reason3)

item_force = make_item("P4", weeks=1, force=True)
score4, _ = compute_score(item_force, weights, min_gap=8, neglected_after=26)
check("computeScore forceInclude bypasses minGap penalty", score4 == 0)

# --- applyPremiumTier ---
items = [make_item(f"P{i}", score=100 - i) for i in range(10)]
apply_premium_tier(items, 20)
check("applyPremiumTier top 20% of 10 = top 2", sum(1 for i in items if i.premium) == 2)
check("applyPremiumTier highest score is premium", items[0].premium)

# --- pickMandatory dedup by address ---
rules = [CadenceRule(ruleId="R1", scope="CATEGORY", matchValue=[], minGapWeeks=None, maxIntervalWeeks=None,
                      intervalType="ONCE_PER_CAMPAIGN", guaranteeType="HARD", dedupBy="ADDRESS",
                      campaignChangeOverride=False, priority=1)]
a = make_item("A1", ppt=5, mand="R1")
a.ulice, a.mesto = "Main", "City"
b = make_item("A2", ppt=9, mand="R1")
b.ulice, b.mesto = "Main", "City"
c = make_item("A3", mand=None)
mandatory = pick_mandatory([a, b, c], rules)
check("pickMandatory dedups by address, keeps higher ppt", mandatory == [b])

# --- selectWeekPOS ---
pool = [make_item(f"S{i}", score=10 - i) for i in range(5)]
selected = select_week_pos(pool, capacity=3, mandatory_rules=[], hold_premium=False)
check("selectWeekPOS respects capacity", len(selected) == 3)
check("selectWeekPOS picks highest scores first", [p.pos for p in selected] == ["S0", "S1", "S2"])

force_item = make_item("FORCE", score=-999, force=True)
pool2 = [force_item] + [make_item(f"N{i}", score=10 - i) for i in range(4)]
selected2 = select_week_pos(pool2, capacity=2, mandatory_rules=[], hold_premium=False)
check("selectWeekPOS forceInclude always first", selected2[0].pos == "FORCE")

# --- distanceKm / geoDays / addGpsBonus ---
check("distanceKm zero for same point", distance_km(0, 0, 0, 0) == 0)

days = [WorkDay(day="MON", dateIso="1.1.2026"), WorkDay(day="TUE", dateIso="2.1.2026")]
geo_pool = [make_item(f"G{i}", score=10 - i, x=i * 0.01, y=0) for i in range(4)]
placed = geo_days(geo_pool, days)
check("geoDays places all items", len(placed) == 4)
check("geoDays anchor is highest score", placed[0].pos.pos == "G0")

gps_selected = [make_item("Anchor", score=100, x=0, y=0)]
gps_pool = [make_item("Near", score=50, x=0.001, y=0), make_item("Far", score=90, x=10, y=10)]
config = GpsBonusConfig(enabled=True, radiusMeters=300, maxVisits=5)
with_bonus = add_gps_bonus(gps_selected, gps_pool, config)
check("addGpsBonus adds nearby POS within radius", any(p.pos == "Near" for p in with_bonus))
check("addGpsBonus does not add far POS", not any(p.pos == "Far" for p in with_bonus))

disabled_config = GpsBonusConfig(enabled=False, radiusMeters=300, maxVisits=5)
check("addGpsBonus disabled returns selection unchanged", add_gps_bonus(gps_selected, gps_pool, disabled_config) == gps_selected)

# --- resolveCapacity ---
override = {"Tech1|2026|31": 5}
check("resolveCapacity uses override", resolve_capacity(override, "Tech1", 2026, 31, 5, 8) == 5)
check("resolveCapacity falls back to days*target", resolve_capacity(override, "Tech2", 2026, 31, 5, 8) == 40)

# --- matchesCadenceRuleScope / isOverdueForCadenceRule (CORN/GECO) ---
corn_rule = CadenceRule(ruleId="CORN", scope="MARKET", matchValue=["CORN"], minGapWeeks=None,
                         maxIntervalWeeks=4, intervalType="RECURRING", guaranteeType="HARD",
                         dedupBy="NONE", campaignChangeOverride=False, priority=80)
geco_rule = CadenceRule(ruleId="GECO", scope="CATEGORY", matchValue=["1GECO"], minGapWeeks=None,
                         maxIntervalWeeks=5, intervalType="RECURRING", guaranteeType="HARD",
                         dedupBy="NONE", campaignChangeOverride=False, priority=80)
check("matchesCadenceRuleScope: MARKET scope matches on market", matches_cadence_rule_scope(corn_rule, "9PODNIKC", "CORN"))
check("matchesCadenceRuleScope: MARKET scope does not match on category", not matches_cadence_rule_scope(corn_rule, "CORN", "OSTATNI"))
check("matchesCadenceRuleScope: CATEGORY scope matches on category", matches_cadence_rule_scope(geco_rule, "1GECO", "OSTATNI"))
check("isOverdueForCadenceRule: never visited is overdue", is_overdue_for_cadence_rule(corn_rule, None))
check("isOverdueForCadenceRule: at the interval is overdue", is_overdue_for_cadence_rule(corn_rule, 4))
check("isOverdueForCadenceRule: under the interval is not overdue", not is_overdue_for_cadence_rule(corn_rule, 3))
no_interval = CadenceRule(**{**corn_rule.__dict__, "maxIntervalWeeks": None})
check("isOverdueForCadenceRule: no maxIntervalWeeks never triggers", not is_overdue_for_cadence_rule(no_interval, None))

print(f"\n{passed} passed, {failed} failed")
sys.exit(1 if failed else 0)
