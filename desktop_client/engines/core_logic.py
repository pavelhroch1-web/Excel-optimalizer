"""
Python port of office-scripts/shared/core.ts's pure scoring/selection logic.

THIS IS A DELIBERATE, DOCUMENTED DUPLICATION of business logic that used to
live only in office-scripts/ (Excel/Office Scripts) - see docs/ARCHITECTURE.md
"Desktop Client local engine execution" for why and what mitigates it. Do NOT
hand-edit this file without also updating core.ts (or vice versa) and re-
running tools/sim/compare_engines.py, the cross-language equivalence check
that replaces the TypeScript-only check_sync.py guarantee for this file.

Every function below must stay behaviourally identical to its core.ts
counterpart - same name, same order of operations, same tie-breaking. Comments
noting *why* a line exists are kept from core.ts where they still apply.
"""
from __future__ import annotations

import math
import unicodedata
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Optional


def iso_now() -> str:
    """Matches JS's `new Date().toISOString()` format: milliseconds + "Z"."""
    now = datetime.now(timezone.utc)
    return now.strftime("%Y-%m-%dT%H:%M:%S.") + f"{now.microsecond // 1000:03d}Z"


def norm(v: str) -> str:
    """Uppercase + strip combining diacritics - matches text.ts's norm()."""
    v = v.upper()
    v = unicodedata.normalize("NFD", v)
    v = "".join(c for c in v if unicodedata.category(c) != "Mn")
    return v.strip()


def normalize_address_key(v: str) -> str:
    return norm(v)


def distance_km(ax: float, ay: float, bx: float, by: float) -> float:
    dx = (ax - bx) * 111
    dy = (ay - by) * 72
    return math.sqrt(dx * dx + dy * dy)


def category_rule(category_rules_table: list[dict], category_normalized: str) -> str:
    """category_rules_table: list of {"key": str, "value": str}, already normalized."""
    star_prefix_rule: Optional[str] = None
    for row in category_rules_table:
        if row["key"] == category_normalized:
            return row["value"]
        if row["key"] == "STARTS_1" and category_normalized.startswith("1"):
            star_prefix_rule = row["value"]
        if row["key"] == "*":
            star_prefix_rule = star_prefix_rule if star_prefix_rule is not None else row["value"]
    return star_prefix_rule if star_prefix_rule is not None else "NORMAL"


@dataclass
class POSItem:
    pos: str
    tech: str
    kategorie: str
    market: str
    classification: str
    nazev: str
    ulice: str
    cislo: str
    mesto: str
    oblast: str
    posArea: str
    ppt: float
    x: float
    y: float
    weeksSinceLastVisit: Optional[float]
    forceInclude: bool
    core: bool
    mandatoryRuleId: Optional[str]
    deadlineWeeks: Optional[float] = None
    premium: bool = False
    score: float = 0.0
    reason: str = ""


@dataclass
class CadenceRule:
    ruleId: str
    scope: str
    matchValue: list[str]
    minGapWeeks: Optional[float]
    maxIntervalWeeks: Optional[float]
    intervalType: str
    guaranteeType: str
    dedupBy: str
    campaignChangeOverride: bool
    priority: float


@dataclass
class ScoreWeights:
    core: float
    kategorizaceA: float
    ppt: float
    neglectedBonus: float


def compute_score(item: POSItem, weights: ScoreWeights, min_gap: float, neglected_after: float) -> tuple[float, str]:
    gap_adjustment = 0.0
    gap_reason = ""
    if item.weeksSinceLastVisit is not None:
        if item.weeksSinceLastVisit < min_gap and not item.forceInclude:
            gap_adjustment = -1000000
        if item.weeksSinceLastVisit >= neglected_after:
            gap_adjustment += weights.neglectedBonus
            gap_reason = "NEGLECTED POS | "
    score = (
        (weights.core if item.core else 0)
        + (weights.kategorizaceA if item.classification == "A" else 0)
        + item.ppt * weights.ppt
        + gap_adjustment
    )
    return score, gap_reason


@dataclass
class GeoClusterConfig:
    radiusKm: float
    bonusFactor: float
    maxBonus: float


def compute_geo_cluster_bonus(item: POSItem, all_items_for_tech: list[POSItem], config: GeoClusterConfig) -> float:
    """Small score nudge toward geographic clustering - see core.ts's
    computeGeoClusterBonus() for the full rationale (product owner,
    2026-07-06: "chci tourplany, co davaji smysl z hlediska prinosu i trasy").
    Must be called AFTER every item in a technician's pool has its base
    compute_score() already set - bonuses reflect neighbors' real base value,
    not a moving target."""
    if item.x == 0 and item.y == 0:
        return 0.0  # no GPS on record - can't judge proximity, no bonus
    bonus = 0.0
    for other in all_items_for_tech:
        if other.pos == item.pos or (other.x == 0 and other.y == 0):
            continue
        if distance_km(item.x, item.y, other.x, other.y) <= config.radiusKm:
            bonus += other.score * config.bonusFactor
    return min(bonus, config.maxBonus)


def apply_premium_tier(items: list[POSItem], premium_percent: float) -> None:
    sorted_items = sorted(items, key=lambda i: -i.score)
    limit = math.ceil((len(sorted_items) * premium_percent) / 100)
    premium_set = {i.pos for i in sorted_items[:limit]}
    for item in items:
        item.premium = item.pos in premium_set


def matches_cadence_rule_scope(rule: CadenceRule, category_normalized: str, market_normalized: str) -> bool:
    """Port of core.ts's matchesCadenceRuleScope() - callers pass already-
    normalized (norm()'d) strings, same convention as category_rule()."""
    return (
        (rule.scope == "CATEGORY" and category_normalized in rule.matchValue)
        or (rule.scope == "CATEGORYPREFIX" and any(category_normalized.startswith(p) for p in rule.matchValue))
        or (rule.scope == "MARKET" and market_normalized in rule.matchValue)
    )


def is_overdue_for_cadence_rule(rule: CadenceRule, weeks_since_last_visit: Optional[float]) -> bool:
    """Port of core.ts's isOverdueForCadenceRule()."""
    return rule.maxIntervalWeeks is not None and (
        weeks_since_last_visit is None or weeks_since_last_visit >= rule.maxIntervalWeeks
    )


@dataclass
class ActivityPlanWindow:
    activityType: str  # "LOS" | "LOT"
    activity: str
    startWeek: float
    endWeek: float


def campaign_starts_within(activity_plan: list[ActivityPlanWindow], week: float, lookahead_weeks: float) -> bool:
    """Port of core.ts's campaignStartsWithin() - True if any ACTIVITY_PLAN
    campaign STARTS strictly after `week` and at or before
    `week + lookaheadWeeks`."""
    return any(a.startWeek > week and a.startWeek <= week + lookahead_weeks for a in activity_plan)


@dataclass
class HoldBackConfig:
    lookaheadWeeks: float
    toleranceAWeeks: float
    toleranceOtherWeeks: float


def should_hold_back(
    classification: str,
    weeks_since_last_visit: Optional[float],
    deadline_weeks: Optional[float],
    activity_plan: list[ActivityPlanWindow],
    week: float,
    config: HoldBackConfig,
) -> bool:
    """Port of core.ts's shouldHoldBack() - see its own comment there for the
    conservative safety guarantees (never defers unknown history, never
    defers past the item's own deadline)."""
    if weeks_since_last_visit is None or deadline_weeks is None or deadline_weeks <= 0:
        return False
    tolerance = config.toleranceAWeeks if classification == "A" else config.toleranceOtherWeeks
    lookahead = min(tolerance, config.lookaheadWeeks)
    if lookahead <= 0:
        return False
    if not campaign_starts_within(activity_plan, week, lookahead):
        return False
    return weeks_since_last_visit + lookahead < deadline_weeks


def compute_urgency_boost(
    weeks_since_last_visit: Optional[float],
    deadline_weeks: Optional[float],
    max_boost: float,
    ramp_start_ratio: float,
) -> float:
    """Port of core.ts's computeUrgencyBoost() - smooth linear ramp toward
    maxBoost as weeksSinceLastVisit approaches deadlineWeeks, starting at
    rampStartRatio."""
    if weeks_since_last_visit is None or deadline_weeks is None or deadline_weeks <= 0:
        return 0.0
    ratio = min(1.0, weeks_since_last_visit / deadline_weeks)
    if ratio < ramp_start_ratio:
        return 0.0
    if ramp_start_ratio >= 1:
        return max_boost
    return max_boost * ((ratio - ramp_start_ratio) / (1 - ramp_start_ratio))


def pick_mandatory(items: list[POSItem], mandatory_rules: list[CadenceRule]) -> list[POSItem]:
    by_address: dict[str, POSItem] = {}
    no_dedup: list[POSItem] = []
    for p in items:
        if not p.mandatoryRuleId:
            continue
        rule = next((r for r in mandatory_rules if r.ruleId == p.mandatoryRuleId), None)
        if rule and rule.dedupBy == "ADDRESS":
            # Keyed by ruleId + address, not address alone - two POS at the
            # same address only compete against each other if they fall
            # under the SAME cadence rule (product owner, 2026-07-08).
            key = p.mandatoryRuleId + "|" + normalize_address_key(p.ulice + "|" + p.mesto)
            if key not in by_address or p.ppt > by_address[key].ppt:
                by_address[key] = p
        else:
            no_dedup.append(p)
    return list(by_address.values()) + no_dedup


def select_week_pos(
    items: list[POSItem], capacity: int, mandatory_rules: list[CadenceRule], hold_premium: bool
) -> list[POSItem]:
    result: list[POSItem] = []
    mandatory = pick_mandatory(items, mandatory_rules)
    for m in mandatory:
        result.append(m)
    result_ids = set(id(p) for p in result)
    candidates = [p for p in items if id(p) not in result_ids]

    def sort_key(p: POSItem):
        force_key = 0 if p.forceInclude else 1
        premium_key = 0
        if hold_premium:
            premium_key = 1 if p.premium else 0
        return (force_key, premium_key, -p.score)

    candidates.sort(key=sort_key)
    while len(result) < capacity and candidates:
        result.append(candidates.pop(0))
    return result


@dataclass
class GpsBonusConfig:
    enabled: bool
    radiusMeters: float
    maxVisits: int


def add_gps_bonus(selected: list[POSItem], pool: list[POSItem], config: GpsBonusConfig) -> list[POSItem]:
    if not config.enabled:
        return selected
    result = list(selected)
    added = 0
    radius_km = config.radiusMeters / 1000
    for anchor in selected:
        if added >= config.maxVisits:
            break
        result_ids = set(id(p) for p in result)
        near = [
            p
            for p in pool
            if id(p) not in result_ids and distance_km(anchor.x, anchor.y, p.x, p.y) <= radius_km
        ]
        near.sort(key=lambda p: -p.score)
        for n in near:
            if added >= config.maxVisits:
                break
            result.append(n)
            added += 1
    return result


@dataclass
class WorkDay:
    day: str
    dateIso: str


@dataclass
class PlacedVisit:
    pos: POSItem
    day: str
    dateIso: str
    group: int


def geo_days(items: list[POSItem], days: list[WorkDay]) -> list[PlacedVisit]:
    """Day assignment (mirrors office-scripts/shared/core.ts's geoDays()).

    The technician's own start point each day is not known (product owner,
    2026-07-06: "ja nevim odkud bude vyjizdet"), so a true route-ordering
    (nearest-neighbor chain) cannot be computed reliably - deliberately NOT
    attempted here. What CAN be improved without knowing the start point is
    which POS get grouped onto the SAME day: a prior version picked each
    day's anchor sequentially (highest score still remaining) and grabbed
    whatever was nearest to only that one anchor, so day 2's anchor could be
    anywhere and day 1 could already have swept up the points that would
    have made day 2's cluster tight, stranding leftovers across whichever
    days still had room ("litaji jako blbci" per product owner, 2026-07-06).

    Fixed by keeping value/PPT as the ONLY thing deciding which POS become
    day-anchors (anchors are simply the top-scoring items, one per day -
    value stays the primary driver), then assigning every other POS via a
    capacitated nearest-anchor match considered GLOBALLY across all days at
    once (sort every (point, day-anchor) pair by distance ascending, greedily
    assign each point to its nearest anchor that still has room) instead of
    day-by-day sequentially - the standard capacitated nearest-centroid
    heuristic. Not the mathematically optimal partition, but a point is never
    stuck on a distant day just because a closer day happened to fill first.
    """
    if len(days) == 0 or len(items) == 0:
        return []
    per_day_target = math.ceil(len(items) / len(days))
    sorted_items = sorted(items, key=lambda p: -p.score)
    num_days = min(len(days), len(items))
    anchors = sorted_items[:num_days]
    rest = sorted_items[num_days:]

    day_capacity = [max(per_day_target - 1, 0) for _ in anchors]
    day_items: list[list[POSItem]] = [[a] for a in anchors]

    candidates = []
    for item_idx, item in enumerate(rest):
        for day_idx, anchor in enumerate(anchors):
            candidates.append((distance_km(item.x, item.y, anchor.x, anchor.y), item_idx, day_idx))
    candidates.sort(key=lambda c: c[0])

    assigned = [False] * len(rest)
    for _distance, item_idx, day_idx in candidates:
        if assigned[item_idx] or day_capacity[day_idx] <= 0:
            continue
        day_items[day_idx].append(rest[item_idx])
        day_capacity[day_idx] -= 1
        assigned[item_idx] = True

    # Every day's capacity can be exhausted before every point is assigned
    # (per_day_target is a ceiling, so total capacity can undershoot
    # len(items) by up to num_days-1) - remaining points must still be
    # placed somewhere, so they overflow onto whichever day still has room,
    # or the last day.
    for item_idx, item in enumerate(rest):
        if assigned[item_idx]:
            continue
        target = next((i for i, c in enumerate(day_capacity) if c > 0), -1)
        if target == -1:
            target = num_days - 1
        else:
            day_capacity[target] -= 1
        day_items[target].append(item)

    result: list[PlacedVisit] = []
    for day_idx in range(num_days):
        for p in day_items[day_idx]:
            result.append(PlacedVisit(pos=p, day=days[day_idx].day, dateIso=days[day_idx].dateIso, group=day_idx + 1))
    return result


def iso_week_number(d: "__import__('datetime').date") -> tuple[int, int]:
    """Port of core.ts's isoWeekNumber() (Monday-start ISO-8601 weeks, week
    containing the year's first Thursday is week 1). Takes a naive
    datetime.date (or datetime.datetime, only the date part is used),
    matching core.ts's use of calendar Y/M/D only, no timezone."""
    import datetime as _dt

    if isinstance(d, _dt.datetime):
        d = d.date()
    day_num = d.isoweekday() - 1  # Mon=0..Sun=6, matches JS's (getUTCDay()+6)%7
    thursday = d + _dt.timedelta(days=-day_num + 3)
    iso_year = thursday.year
    first_thursday_raw = _dt.date(iso_year, 1, 4)
    first_day_num = first_thursday_raw.isoweekday() - 1
    first_thursday = first_thursday_raw + _dt.timedelta(days=-first_day_num + 3)
    week = 1 + round((thursday - first_thursday).days / 7)
    return week, iso_year


def resolve_capacity(
    override_map: dict[str, float],
    tech: str,
    year: int,
    week: int,
    work_days_count: int,
    target_visits_per_day: float,
    target_visits_week: Optional[float] = None,
) -> float:
    """Port of core.ts's resolveCapacity() - capacity is fundamentally
    weekly: a per-technician/week override always wins; below that, a flat
    weekly target (if configured) is used directly; only if neither exists
    does this fall back to work_days_count * target_visits_per_day."""
    key = f"{tech}|{year}|{week}"
    if key in override_map:
        return override_map[key]
    if target_visits_week is not None:
        return target_visits_week
    return work_days_count * target_visits_per_day


def weeks_between(week1: int, year1: int, week2: int, year2: int) -> int:
    """Port of core.ts's weeksBetween() - simplified week-distance (treats
    every year as 52 weeks, same documented limitation as the TS original)."""
    return week2 - week1 + (year2 - year1) * 52


@dataclass
class ActualWeek:
    week: int
    year: int


def determine_compliance_status(
    planned_week: int,
    planned_year: int,
    actual_weeks: list[ActualWeek],
    late_cutoff_weeks: int,
    latest_known_week: int,
    latest_known_year: int,
) -> str:
    """Port of core.ts's determineComplianceStatus(). Returns one of
    "Splneno_vcas" | "Splneno_pozde" | "Nesplneno" | "Pending"."""
    if len(actual_weeks) == 0:
        elapsed = weeks_between(planned_week, planned_year, latest_known_week, latest_known_year)
        if elapsed > late_cutoff_weeks:
            return "Nesplneno"
        return "Pending"
    earliest = min(
        actual_weeks,
        key=lambda w: weeks_between(planned_week, planned_year, w.week, w.year),
    )
    delta = weeks_between(planned_week, planned_year, earliest.week, earliest.year)
    if delta <= 0:
        return "Splneno_vcas"
    return "Splneno_pozde"


def advance_lifecycle_status(current: str, monday_has_passed: bool, has_pending_visits: bool) -> str:
    """Port of core.ts's advanceLifecycleStatus(). current/return values are
    one of "Draft" | "Published" | "Active" | "Closed"."""
    if current == "Closed":
        return "Closed"
    if current == "Draft":
        return "Draft"
    if not has_pending_visits:
        return "Closed"
    if current == "Active":
        return "Active"
    return "Active" if monday_has_passed else "Published"


@dataclass
class NeglectCandidate:
    posId: str
    weeksSinceLastVisit: Optional[float]


def find_neglected(items: list[NeglectCandidate], threshold_weeks: float) -> list[str]:
    """Port of core.ts's findNeglected()."""
    return [
        i.posId
        for i in items
        if i.weeksSinceLastVisit is not None and i.weeksSinceLastVisit >= threshold_weeks
    ]


@dataclass
class ComplianceOutcome:
    group: str
    status: str


@dataclass
class GroupFailureRate:
    group: str
    total: int
    failed: int
    rate: float


def compute_failure_rate_by_group(
    rows: list[ComplianceOutcome], failure_statuses: list[str]
) -> list[GroupFailureRate]:
    """Port of core.ts's computeFailureRateByGroup(). Caller must dedupe an
    append-only source (e.g. COMPLIANCE_LOG) to one row per logical subject
    first via latest_by_key() - see core.ts's comment for the bug this
    prevents."""
    by_group: dict[str, dict[str, int]] = {}
    for row in rows:
        if not row.group:
            continue
        g = by_group.setdefault(row.group, {"total": 0, "failed": 0})
        g["total"] += 1
        if row.status in failure_statuses:
            g["failed"] += 1
    return [
        GroupFailureRate(group=group, total=v["total"], failed=v["failed"], rate=v["failed"] / v["total"])
        for group, v in by_group.items()
    ]


def latest_by_key(rows: list) -> list:
    """Port of core.ts's latestByKey(). Each row must have `.key` and
    `.timestamp` (ISO string, lexicographically comparable) attributes."""
    latest: dict[str, object] = {}
    for row in rows:
        if row.key not in latest or row.timestamp > latest[row.key].timestamp:
            latest[row.key] = row
    return list(latest.values())


@dataclass
class WeeklyVolume:
    week: int
    year: int
    count: int


@dataclass
class VolumeTrendSignal:
    trailingAvg: float
    baselineAvg: float
    ratioPercent: float
    significant: bool


def compute_volume_trend(
    weekly_volumes: list[WeeklyVolume],
    trailing_window: int,
    baseline_window: int,
    threshold_percent: float,
) -> Optional[VolumeTrendSignal]:
    """Port of core.ts's computeVolumeTrend(). Returns None when there isn't
    enough history yet, or when the baseline average is zero."""
    sorted_volumes = sorted(weekly_volumes, key=lambda v: (v.year, v.week))
    if len(sorted_volumes) < trailing_window + baseline_window:
        return None
    trailing = sorted_volumes[len(sorted_volumes) - trailing_window :]
    baseline = sorted_volumes[
        len(sorted_volumes) - trailing_window - baseline_window : len(sorted_volumes) - trailing_window
    ]

    def avg(rows: list[WeeklyVolume]) -> float:
        return sum(r.count for r in rows) / len(rows)

    trailing_avg = avg(trailing)
    baseline_avg = avg(baseline)
    if baseline_avg == 0:
        return None
    ratio_percent = round((trailing_avg / baseline_avg) * 1000) / 10
    significant = abs(ratio_percent - 100) >= threshold_percent
    return VolumeTrendSignal(
        trailingAvg=trailing_avg, baselineAvg=baseline_avg, ratioPercent=ratio_percent, significant=significant
    )


@dataclass
class OpenPlanRow:
    posId: str
    plannedTechnician: str


@dataclass
class POSCurrentState:
    status: str
    assignedTechnician: str


@dataclass
class DriftAlert:
    posId: str
    type: str  # "CLOSED_POS_IN_PLAN" | "TECHNICIAN_REASSIGNED"
    plannedTechnician: str
    currentTechnician: str


def find_published_plan_drift(
    open_plan_rows: list[OpenPlanRow], pos_state: dict[str, POSCurrentState]
) -> list[DriftAlert]:
    """Port of core.ts's findPublishedPlanDrift()."""
    seen: set[str] = set()
    alerts: list[DriftAlert] = []
    for row in open_plan_rows:
        current = pos_state.get(row.posId)
        if not current:
            continue
        if current.status == "Closed":
            key = f"{row.posId}|CLOSED_POS_IN_PLAN"
            if key not in seen:
                seen.add(key)
                alerts.append(
                    DriftAlert(
                        posId=row.posId,
                        type="CLOSED_POS_IN_PLAN",
                        plannedTechnician=row.plannedTechnician,
                        currentTechnician=current.assignedTechnician,
                    )
                )
        if current.assignedTechnician and current.assignedTechnician != row.plannedTechnician:
            key = f"{row.posId}|TECHNICIAN_REASSIGNED"
            if key not in seen:
                seen.add(key)
                alerts.append(
                    DriftAlert(
                        posId=row.posId,
                        type="TECHNICIAN_REASSIGNED",
                        plannedTechnician=row.plannedTechnician,
                        currentTechnician=current.assignedTechnician,
                    )
                )
    return alerts


def find_unplanned_active_pos(active_pos_ids: list[str], ever_planned_pos_ids: set[str]) -> list[str]:
    """Port of core.ts's findUnplannedActivePOS()."""
    return [p for p in active_pos_ids if p not in ever_planned_pos_ids]
