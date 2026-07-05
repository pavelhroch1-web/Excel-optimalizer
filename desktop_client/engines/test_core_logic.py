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
    ActualWeek,
    CadenceRule,
    ComplianceOutcome,
    DriftAlert,
    GeoClusterConfig,
    GpsBonusConfig,
    NeglectCandidate,
    OpenPlanRow,
    POSCurrentState,
    POSItem,
    ScoreWeights,
    WeeklyVolume,
    WorkDay,
    add_gps_bonus,
    advance_lifecycle_status,
    apply_premium_tier,
    category_rule,
    compute_failure_rate_by_group,
    compute_geo_cluster_bonus,
    compute_score,
    compute_volume_trend,
    determine_compliance_status,
    distance_km,
    find_neglected,
    find_published_plan_drift,
    find_unplanned_active_pos,
    geo_days,
    is_overdue_for_cadence_rule,
    latest_by_key,
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

# --- computeGeoClusterBonus ---
geo_config = GeoClusterConfig(radiusKm=3, bonusFactor=0.01, maxBonus=5000)

item_no_gps = make_item("A", x=0, y=0, score=100)
neighbor_ok = make_item("B", x=14.0, y=50.0, score=100000)
check("computeGeoClusterBonus: no GPS on item gets no bonus",
      compute_geo_cluster_bonus(item_no_gps, [item_no_gps, neighbor_ok], geo_config) == 0)

item_a = make_item("A", x=14.0, y=50.0, score=100)
neighbor_no_gps = make_item("B", x=0, y=0, score=100000)
check("computeGeoClusterBonus: neighbor with no GPS is ignored",
      compute_geo_cluster_bonus(item_a, [item_a, neighbor_no_gps], geo_config) == 0)

item_a2 = make_item("A", x=14.0, y=50.0, score=100)
near = make_item("B", x=14.001, y=50.0, score=1000)
check("computeGeoClusterBonus: single nearby neighbor contributes bonusFactor * score",
      compute_geo_cluster_bonus(item_a2, [item_a2, near], geo_config) == 10)

item_a3 = make_item("A", x=14.0, y=50.0, score=100)
far = make_item("B", x=14.5, y=50.0, score=1000000)
check("computeGeoClusterBonus: neighbor beyond radiusKm contributes nothing",
      compute_geo_cluster_bonus(item_a3, [item_a3, far], geo_config) == 0)

item_a4 = make_item("A", x=14.0, y=50.0, score=0)
n1 = make_item("B", x=14.001, y=50.0, score=1000)
n2 = make_item("C", x=14.002, y=50.0, score=2000)
check("computeGeoClusterBonus: multiple neighbors sum contributions",
      compute_geo_cluster_bonus(item_a4, [item_a4, n1, n2], geo_config) == 30)

item_a5 = make_item("A", x=14.0, y=50.0, score=0)
many_neighbors = [make_item(f"N{i}", x=14.0 + i * 0.0001, y=50.0, score=1000000) for i in range(1, 6)]
check("computeGeoClusterBonus: bonus capped at maxBonus",
      compute_geo_cluster_bonus(item_a5, [item_a5, *many_neighbors], geo_config) == 5000)

item_a6 = make_item("A", x=14.0, y=50.0, score=999999)
item_a6_copy = make_item("A", x=14.0, y=50.0, score=999999)
check("computeGeoClusterBonus: itself excluded by pos, not object identity",
      compute_geo_cluster_bonus(item_a6, [item_a6, item_a6_copy], geo_config) == 0)

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

days2 = [WorkDay(day="MON", dateIso="27.7.2026"), WorkDay(day="TUE", dateIso="28.7.2026")]
items_stranded = [
    make_item("A-anchor", score=100, x=50.0, y=14.0),
    make_item("B-anchor", score=90, x=50.5, y=14.5),
    make_item("A2", score=10, x=50.001, y=14.001),
]
placed_stranded = geo_days(items_stranded, days2)
a2_day = next(p.day for p in placed_stranded if p.pos.pos == "A2")
check("geoDays global assignment keeps A2 on MON (near A-anchor), not stranded on TUE", a2_day == "MON")

cluster_a = [make_item(f"A{i}", score=100 - i * 2, x=50 + i * 0.001, y=14) for i in range(5)]
cluster_b = [make_item(f"B{i}", score=99 - i * 2, x=55 + i * 0.001, y=18) for i in range(5)]
placed_balanced = geo_days(cluster_a + cluster_b, days2)
mon_pos = [p.pos.pos for p in placed_balanced if p.day == "MON"]
tue_pos = [p.pos.pos for p in placed_balanced if p.day == "TUE"]
check("geoDays balanced clusters: MON is 5 items", len(mon_pos) == 5)
check("geoDays balanced clusters: TUE is 5 items", len(tue_pos) == 5)
check("geoDays balanced clusters: MON is all cluster A", all(p.startswith("A") for p in mon_pos))
check("geoDays balanced clusters: TUE is all cluster B", all(p.startswith("B") for p in tue_pos))

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
check("resolveCapacity uses flat weekly target when set", resolve_capacity({}, "Tech3", 2026, 31, 5, 8, 35) == 35)
check("resolveCapacity per-tech override still wins over weekly target", resolve_capacity(override, "Tech1", 2026, 31, 5, 8, 35) == 5)
check("resolveCapacity weekly target defaults to None (falls back to days*target)", resolve_capacity({}, "Tech4", 2026, 31, 5, 8) == 40)

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

# --- determineComplianceStatus ---
check("visit realized in the planned week = Splneno_vcas",
      determine_compliance_status(31, 2026, [ActualWeek(31, 2026)], 1, 33, 2026) == "Splneno_vcas")
check("visit realized one week late = Splneno_pozde",
      determine_compliance_status(31, 2026, [ActualWeek(32, 2026)], 1, 33, 2026) == "Splneno_pozde")
check("visit very late is still Splneno_pozde, not Nesplneno",
      determine_compliance_status(31, 2026, [ActualWeek(40, 2026)], 1, 41, 2026) == "Splneno_pozde")
check("no visit yet, deadline not reached = Pending",
      determine_compliance_status(31, 2026, [], 1, 31, 2026) == "Pending")
check("no visit, exactly at cutoff boundary = still Pending",
      determine_compliance_status(31, 2026, [], 1, 32, 2026) == "Pending")
check("no visit, one week past cutoff = Nesplneno",
      determine_compliance_status(31, 2026, [], 1, 33, 2026) == "Nesplneno")
check("multiple actual visits - earliest one determines the status",
      determine_compliance_status(31, 2026, [ActualWeek(33, 2026), ActualWeek(31, 2026)], 1, 33, 2026) == "Splneno_vcas")

# --- findNeglected ---
neglect_items = [
    NeglectCandidate(posId="A", weeksSinceLastVisit=26),
    NeglectCandidate(posId="B", weeksSinceLastVisit=25),
    NeglectCandidate(posId="C", weeksSinceLastVisit=30),
]
check("findNeglected: POS at or beyond threshold are flagged", find_neglected(neglect_items, 26) == ["A", "C"])
check("findNeglected: null weeksSinceLastVisit is not flagged",
      find_neglected([NeglectCandidate(posId="A", weeksSinceLastVisit=None)], 26) == [])
check("findNeglected: empty list returns empty", find_neglected([], 26) == [])

# --- computeFailureRateByGroup ---
failure_rows = [
    ComplianceOutcome(group="Novak", status="Nesplneno"),
    ComplianceOutcome(group="Novak", status="Nesplneno"),
    ComplianceOutcome(group="Novak", status="Splneno_vcas"),
    ComplianceOutcome(group="Svoboda", status="Splneno_vcas"),
]
failure_result = compute_failure_rate_by_group(failure_rows, ["Nesplneno"])
novak_rate = next(r for r in failure_result if r.group == "Novak")
svoboda_rate = next(r for r in failure_result if r.group == "Svoboda")
check("computeFailureRateByGroup: total/failed computed correctly", novak_rate.total == 3 and novak_rate.failed == 2)
check("computeFailureRateByGroup: rate computed correctly", abs(novak_rate.rate - 2 / 3) < 1e-9)
check("computeFailureRateByGroup: group with no failures has rate 0", svoboda_rate.rate == 0)
check("computeFailureRateByGroup: empty group is skipped",
      len(compute_failure_rate_by_group(
          [ComplianceOutcome(group="", status="Navic_evidovano"), ComplianceOutcome(group="Novak", status="Splneno_vcas")],
          ["Nesplneno"])) == 1)
check("computeFailureRateByGroup: multiple failure statuses counted together",
      compute_failure_rate_by_group(
          [ComplianceOutcome(group="Novak", status="Nesplneno"),
           ComplianceOutcome(group="Novak", status="Splneno_pozde"),
           ComplianceOutcome(group="Novak", status="Splneno_vcas")],
          ["Nesplneno", "Splneno_pozde"])[0].failed == 2)
check("computeFailureRateByGroup: empty input returns empty output", compute_failure_rate_by_group([], ["Nesplneno"]) == [])


class _TimestampedRow:
    def __init__(self, key, timestamp, **extra):
        self.key = key
        self.timestamp = timestamp
        for k, v in extra.items():
            setattr(self, k, v)


undeduped = [_TimestampedRow(key=None, timestamp=None, group="Novak", status="Pending"),
             _TimestampedRow(key=None, timestamp=None, group="Novak", status="Nesplneno")]
wrong_rate = compute_failure_rate_by_group(
    [ComplianceOutcome(group=r.group, status=r.status) for r in undeduped], ["Nesplneno"])[0].rate
check("regression: undeduped repeated evaluations dilute the rate", abs(wrong_rate - 0.5) < 1e-9)
timestamped = [
    _TimestampedRow(key="Novak|POS1", timestamp="2026-08-01T00:00:00Z", group="Novak", status="Pending"),
    _TimestampedRow(key="Novak|POS1", timestamp="2026-08-08T00:00:00Z", group="Novak", status="Nesplneno"),
]
deduped = latest_by_key(timestamped)
correct_rate = compute_failure_rate_by_group(
    [ComplianceOutcome(group=r.group, status=r.status) for r in deduped], ["Nesplneno"])[0].rate
check("regression: deduping with latestByKey first gives the correct rate", correct_rate == 1)

# --- latestByKey ---
lbk_rows = [
    _TimestampedRow(key="A|31", timestamp="2026-08-01T00:00:00Z", status="Pending"),
    _TimestampedRow(key="A|31", timestamp="2026-08-08T00:00:00Z", status="Nesplneno"),
    _TimestampedRow(key="B|31", timestamp="2026-08-01T00:00:00Z", status="Splneno_vcas"),
]
lbk_result = latest_by_key(lbk_rows)
lbk_a = next(r for r in lbk_result if r.key == "A|31")
check("latestByKey: keeps only the newest timestamp per key", lbk_a.status == "Nesplneno" and len(lbk_result) == 2)
single_row = [_TimestampedRow(key="A", timestamp="2026-08-01T00:00:00Z")]
check("latestByKey: single row per key returned unchanged", latest_by_key(single_row) == single_row)
check("latestByKey: empty input returns empty output", latest_by_key([]) == [])

# --- advanceLifecycleStatus ---
check("Draft never auto-advances", advance_lifecycle_status("Draft", True, True) == "Draft")
check("Draft never auto-advances (2)", advance_lifecycle_status("Draft", False, False) == "Draft")
check("Closed is terminal", advance_lifecycle_status("Closed", False, True) == "Closed")
check("Published stays Published if Monday hasn't passed and visits pending",
      advance_lifecycle_status("Published", False, True) == "Published")
check("Published becomes Active once Monday has passed and visits pending",
      advance_lifecycle_status("Published", True, True) == "Active")
check("Published closes immediately if no visits pending",
      advance_lifecycle_status("Published", False, False) == "Closed")
check("Active closes once no visits pending", advance_lifecycle_status("Active", True, False) == "Closed")
check("Active stays Active while visits pending", advance_lifecycle_status("Active", True, True) == "Active")
check("Active never regresses to Published", advance_lifecycle_status("Active", False, True) == "Active")

# --- computeVolumeTrend ---
def _weeks(counts, start_year=2026, start_week=1):
    return [WeeklyVolume(week=start_week + i, year=start_year, count=c) for i, c in enumerate(counts)]


check("computeVolumeTrend: not enough history returns None", compute_volume_trend(_weeks([10, 10, 10]), 4, 4, 25) is None)
check("computeVolumeTrend: baseline average of zero returns None",
      compute_volume_trend(_weeks([0, 0, 0, 0, 10, 10, 10, 10]), 4, 4, 25) is None)
stable_signal = compute_volume_trend(_weeks([10, 10, 10, 10, 10, 10, 10, 10]), 4, 4, 25)
check("computeVolumeTrend: stable volume is not significant", stable_signal.significant is False and stable_signal.ratioPercent == 100)
increase_signal = compute_volume_trend(_weeks([10, 10, 10, 10, 20, 20, 20, 20]), 4, 4, 25)
check("computeVolumeTrend: large increase flagged with correct ratio",
      increase_signal.trailingAvg == 20 and increase_signal.baselineAvg == 10
      and increase_signal.ratioPercent == 200 and increase_signal.significant is True)
decrease_signal = compute_volume_trend(_weeks([20, 20, 20, 20, 10, 10, 10, 10]), 4, 4, 25)
check("computeVolumeTrend: large decrease flagged too", decrease_signal.ratioPercent == 50 and decrease_signal.significant is True)
small_signal = compute_volume_trend(_weeks([10, 10, 10, 10, 11, 11, 11, 11]), 4, 4, 25)
check("computeVolumeTrend: small deviation under threshold not flagged", small_signal.significant is False)
shuffled = [
    WeeklyVolume(week=3, year=2026, count=20),
    WeeklyVolume(week=1, year=2026, count=10),
    WeeklyVolume(week=4, year=2026, count=20),
    WeeklyVolume(week=2, year=2026, count=10),
]
shuffled_signal = compute_volume_trend(shuffled, 2, 2, 25)
check("computeVolumeTrend: unsorted input is sorted internally",
      shuffled_signal.baselineAvg == 10 and shuffled_signal.trailingAvg == 20)

# --- findPublishedPlanDrift ---
check("findPublishedPlanDrift: flags Active-in-plan but Closed-in-master",
      find_published_plan_drift(
          [OpenPlanRow(posId="POS1", plannedTechnician="Novak")],
          {"POS1": POSCurrentState(status="Closed", assignedTechnician="Novak")})[0].type == "CLOSED_POS_IN_PLAN")
reassigned_alerts = find_published_plan_drift(
    [OpenPlanRow(posId="POS1", plannedTechnician="Novak")],
    {"POS1": POSCurrentState(status="Active", assignedTechnician="Svoboda")})
check("findPublishedPlanDrift: flags technician reassignment",
      len(reassigned_alerts) == 1 and reassigned_alerts[0].type == "TECHNICIAN_REASSIGNED"
      and reassigned_alerts[0].plannedTechnician == "Novak" and reassigned_alerts[0].currentTechnician == "Svoboda")
check("findPublishedPlanDrift: both reasons at once",
      len(find_published_plan_drift(
          [OpenPlanRow(posId="POS1", plannedTechnician="Novak")],
          {"POS1": POSCurrentState(status="Closed", assignedTechnician="Svoboda")})) == 2)
check("findPublishedPlanDrift: no drift when master matches plan",
      len(find_published_plan_drift(
          [OpenPlanRow(posId="POS1", plannedTechnician="Novak")],
          {"POS1": POSCurrentState(status="Active", assignedTechnician="Novak")})) == 0)
check("findPublishedPlanDrift: same POS in several open weeks flagged once per reason",
      len(find_published_plan_drift(
          [OpenPlanRow(posId="POS1", plannedTechnician="Novak"), OpenPlanRow(posId="POS1", plannedTechnician="Novak")],
          {"POS1": POSCurrentState(status="Closed", assignedTechnician="Novak")})) == 1)
check("findPublishedPlanDrift: no matching POS_MASTER entry is skipped",
      find_published_plan_drift([OpenPlanRow(posId="GHOST", plannedTechnician="Novak")], {}) == [])
check("findPublishedPlanDrift: empty input returns empty output", find_published_plan_drift([], {}) == [])

# --- findUnplannedActivePOS ---
check("findUnplannedActivePOS: finds Active POS absent from ever-planned set",
      find_unplanned_active_pos(["POS1", "POS2"], {"POS1"}) == ["POS2"])
check("findUnplannedActivePOS: empty when every Active POS was planned",
      find_unplanned_active_pos(["POS1", "POS2"], {"POS1", "POS2"}) == [])
check("findUnplannedActivePOS: empty input returns empty output", find_unplanned_active_pos([], set()) == [])

print(f"\n{passed} passed, {failed} failed")
sys.exit(1 if failed else 0)
