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


def apply_premium_tier(items: list[POSItem], premium_percent: float) -> None:
    sorted_items = sorted(items, key=lambda i: -i.score)
    limit = math.ceil((len(sorted_items) * premium_percent) / 100)
    premium_set = {i.pos for i in sorted_items[:limit]}
    for item in items:
        item.premium = item.pos in premium_set


def pick_mandatory(items: list[POSItem], mandatory_rules: list[CadenceRule]) -> list[POSItem]:
    by_address: dict[str, POSItem] = {}
    no_dedup: list[POSItem] = []
    for p in items:
        if not p.mandatoryRuleId:
            continue
        rule = next((r for r in mandatory_rules if r.ruleId == p.mandatoryRuleId), None)
        if rule and rule.dedupBy == "ADDRESS":
            key = normalize_address_key(p.ulice + "|" + p.mesto)
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
    remaining = list(items)
    result: list[PlacedVisit] = []
    group = 1
    per_day_target = math.ceil(len(items) / len(days)) if len(days) > 0 else 0
    for d in days:
        if len(remaining) == 0:
            break
        remaining.sort(key=lambda p: -p.score)
        anchor = remaining.pop(0)
        result.append(PlacedVisit(pos=anchor, day=d.day, dateIso=d.dateIso, group=group))
        remaining.sort(key=lambda p: distance_km(anchor.x, anchor.y, p.x, p.y))
        take = min(per_day_target - 1, len(remaining))
        for _ in range(max(take, 0)):
            near = remaining.pop(0)
            result.append(PlacedVisit(pos=near, day=d.day, dateIso=d.dateIso, group=group))
        group += 1
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
    override_map: dict[str, float], tech: str, year: int, week: int, work_days_count: int, target_visits_per_day: float
) -> float:
    key = f"{tech}|{year}|{week}"
    return override_map[key] if key in override_map else work_days_count * target_visits_per_day
