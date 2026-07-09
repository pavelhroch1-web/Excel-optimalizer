"""
Python port of office-scripts/PlanningEngine.ts's main(). Line-for-line
translation - see import_engine.py's module docstring for the duplication
rationale. The scoring/selection algorithm itself is NOT re-implemented
here - it's imported from core_logic.py, which is itself a port of
office-scripts/shared/core.ts (the same function names, same call order,
same tie-breaking as the TypeScript source). Only the "adapter" code (sheet
reading, config parsing, candidate-list building, output writing) is
translated in this file, mirroring how PlanningEngine.ts itself separates
its SYNC-BLOCK core from its adapter code.
"""
from __future__ import annotations

import datetime
from typing import Optional

from .core_logic import (
    ActivityPlanWindow,
    CadenceRule,
    GeoClusterConfig,
    GpsBonusConfig,
    HoldBackConfig,
    POSItem,
    ScoreWeights,
    add_gps_bonus,
    apply_premium_tier,
    campaign_starts_within,
    category_rule,
    compute_geo_cluster_bonus,
    compute_score,
    compute_urgency_boost,
    geo_days,
    iso_week_number,
    is_overdue_for_cadence_rule,
    matches_cadence_rule_scope,
    norm,
    pick_mandatory,
    resolve_capacity,
    select_week_pos,
    should_hold_back,
    WorkDay,
)
from .dates_logic import to_cs_cz_date_string, work_days
from .js_compat import at as _at, js_number as _js_number, num as _num, s as _s
from .mock_workbook import MockWorkbook


def _assert_breakdown(item) -> None:
    """Guards the score-breakdown read-out against ever drifting from the real
    algorithm: the recorded base components MUST sum to the engine's own
    baseScore (from core_logic.compute_score), and baseScore + urgencyBoost +
    gpsBonus MUST equal the item's final score. If either invariant breaks,
    raise loudly rather than show the manager a wrong breakdown."""
    base_sum = (
        item.pptComponent + item.coreBonus + item.aBonus
        + item.gapPenalty + item.neglectedBonus
    )
    if abs(base_sum - item.baseScore) > 1e-6:
        raise AssertionError(
            f"Score breakdown drift for POS {item.pos}: components {base_sum} "
            f"!= engine baseScore {item.baseScore}"
        )
    if abs((item.baseScore + item.urgencyBoost + item.gpsBonus) - item.score) > 1e-6:
        raise AssertionError(
            f"Score total drift for POS {item.pos}: base+boosts "
            f"{item.baseScore + item.urgencyBoost + item.gpsBonus} != score {item.score}"
        )


def run(
    workbook: MockWorkbook,
    candidates_out: "Optional[list]" = None,
    rejected_out: "Optional[list]" = None,
) -> str:
    """Runs the Planning Engine exactly as before. `candidates_out` is an
    OPTIONAL observability hook (added 2026-07-11 for the web "Kandidáti POS"
    screen): when a list is passed, run() appends one dict per (technician,
    week, candidate POS) describing that POS's score, its component breakdown,
    and whether/why it was selected - a pure read-out of decisions the engine
    already makes.

    `rejected_out` is a second OPTIONAL observability hook (added for the POS
    Detail panel): when a list is passed, run() appends one dict per POS that
    was filtered OUT before scoring, with the exact reason the engine itself
    used at that branch (inactive/closed, blacklist, FORCE_EXCLUDE, disabled
    terminal type, disabled partner, category EXCLUDE). It re-reads the same
    POS_MASTER cells and re-evaluates the same pure filter functions the
    engine just used - no new decision logic.

    When both are None (the normal path, and every equivalence-harness call),
    run() behaves byte-for-byte identically - nothing about selection or the
    MANAGER_PLAN output depends on either argument. Verified unchanged via
    tools/sim/compare_engines.py."""
    def read_table(sheet_name: str) -> list[list]:
        ws = workbook.get_worksheet(sheet_name)
        rng = ws.get_used_range()
        return rng.get_values() if rng else []

    pos_master = read_table("POS_MASTER")
    control = read_table("CONTROL")
    activity = read_table("ACTIVITY_PLAN")
    terminals = read_table("TERMINAL_RULES")
    markets = read_table("MARKET_RULES")
    category_rules_raw = read_table("CATEGORY_RULES")
    cadence_rules_raw = read_table("CADENCE_RULES")
    pareto_groups = read_table("PARETO_GROUPS")
    score_profiles = read_table("SCORE_PROFILES")
    capacity_override = read_table("CAPACITY_OVERRIDE")
    plan_lifecycle = read_table("PLAN_LIFECYCLE")
    existing_manager_plan = read_table("MANAGER_PLAN")
    # BLACKLIST (product owner, 2026-07-09) - see PlanningEngine.ts's matching comment.
    blacklist_raw = read_table("BLACKLIST")
    blacklisted_pos: set[str] = set()
    for i in range(1, len(blacklist_raw)):
        pos_id = _s(_at(blacklist_raw[i], 0)).strip()
        if pos_id:
            blacklisted_pos.add(pos_id)

    out_ws = workbook.get_worksheet("MANAGER_PLAN")

    # ==========================================================================
    # CONFIG READERS
    # ==========================================================================

    def setting(name: str, fallback: float) -> float:
        for i in range(1, len(control)):
            if norm(_s(_at(control[i], 0))) == norm(name):
                v = _js_number(_at(control[i], 1))
                return fallback if v != v else v  # isNaN(v) ? fallback : v
        return fallback

    def setting_optional(name: str) -> Optional[float]:
        for i in range(1, len(control)):
            if norm(_s(_at(control[i], 0))) == norm(name):
                raw = _at(control[i], 1)
                if raw in ("", None):
                    return None
                v = _js_number(raw)
                return None if v != v else v  # isNaN(v) ? None : v
        return None

    # Dynamic "current week" (product owner, 2026-07-08/09) - default chain:
    # explicit CONTROL override, else one past the highest WEEK already in
    # MANAGER_PLAN ("resume where the last run left off"), else TODAY's real
    # ISO week (first-ever run only). See PlanningEngine.ts's matching comment.
    now_iso_week, now_iso_year = iso_week_number(datetime.date.today())
    last_planned_week = 0
    for i in range(1, len(existing_manager_plan)):
        w = _js_number(_at(existing_manager_plan[i], 0))
        if w == w and w > last_planned_week:  # w == w is False only for NaN
            last_planned_week = w
    start_week_opt = setting_optional("CAMPAIGN_START_WEEK")
    if start_week_opt is not None:
        START_WEEK = int(start_week_opt)
    elif last_planned_week > 0:
        START_WEEK = int(last_planned_week) + 1
    else:
        START_WEEK = now_iso_week
    CAMPAIGN_LENGTH = int(setting("CAMPAIGN_LENGTH", 4))
    TARGET_DAY = setting("TARGET_VISITS_DAY", 8)
    # Optional flat weekly capacity target - see resolve_capacity()'s
    # docstring. Per-technician/week CAPACITY_OVERRIDE still wins over this.
    TARGET_WEEK = setting_optional("TARGET_VISITS_WEEK")
    STANDARD_GAP = setting("STANDARD_VISIT_GAP", 8)
    NEGLECTED_AFTER = setting("NEGLECTED_AFTER_WEEKS", 26)
    year_opt = setting_optional("YEAR")
    YEAR = int(year_opt) if year_opt is not None else now_iso_year
    SYNC_WINDOW = int(setting("SYNC_WINDOW_WEEKS", 1))
    GPS_CONFIG = GpsBonusConfig(
        enabled=setting("GPS_EXTRA_ENABLED", 0) == 1,
        radiusMeters=setting("GPS_EXTRA_RADIUS_METERS", 300),
        maxVisits=int(setting("GPS_EXTRA_MAX_VISITS", 5)),
    )
    # Geo cluster bonus config - see core_logic.py's compute_geo_cluster_bonus
    # docstring / PlanningEngine.ts's identical comment (product owner,
    # 2026-07-06).
    GEO_CLUSTER_CONFIG = GeoClusterConfig(
        radiusKm=setting("GEO_CLUSTER_RADIUS_KM", 3),
        bonusFactor=setting("GEO_CLUSTER_BONUS_FACTOR", 0.01),
        maxBonus=setting("GEO_CLUSTER_MAX_BONUS", 5000),
    )
    # SMART HOLD-BACK config (product owner, 2026-07-09, "Kriticke") - see
    # office-scripts/PlanningEngine.ts's identical comment.
    HOLDBACK_CONFIG = HoldBackConfig(
        lookaheadWeeks=setting("HOLDBACK_LOOKAHEAD_WEEKS", 3),
        toleranceAWeeks=setting("HOLDBACK_TOLERANCE_A_WEEKS", 1),
        toleranceOtherWeeks=setting("HOLDBACK_TOLERANCE_OTHER_WEEKS", 3),
    )
    URGENCY_BOOST_MAX = setting("URGENCY_BOOST_MAX", 20000)
    URGENCY_BOOST_RAMP_START = setting("URGENCY_BOOST_RAMP_START_RATIO", 0.5)

    # PLAN LIFECYCLE: locked weeks (Published/Active/Closed) are never
    # regenerated - see PlanningEngine.ts's identical comment.
    locked_weeks: set[int] = set()
    if len(plan_lifecycle) >= 2:
        pl_headers = [_s(h) for h in plan_lifecycle[0]]

        def pl_idx(name: str) -> int:
            return pl_headers.index(name) if name in pl_headers else -1

        for i in range(1, len(plan_lifecycle)):
            row = plan_lifecycle[i]
            if int(_num(_at(row, pl_idx("year")))) != YEAR:
                continue
            status = _s(_at(row, pl_idx("status")))
            if status in ("Published", "Active", "Closed"):
                locked_weeks.add(int(_num(_at(row, pl_idx("week")))))

    kept_rows: list[list] = []
    if len(existing_manager_plan) >= 2:
        for i in range(1, len(existing_manager_plan)):
            row = existing_manager_plan[i]
            if not _at(row, 0):
                continue
            if int(_num(_at(row, 0))) in locked_weeks:
                kept_rows.append(list(row))

    active_terms: list[str] = []
    for i in range(1, len(terminals)):
        if norm(_s(_at(terminals[i], 1))) == "YES":
            active_terms.append(norm(_s(_at(terminals[i], 0))))

    def terminal_ok(v: str) -> bool:
        value = norm(v)
        return any(t in value for t in active_terms)

    active_markets: list[str] = []
    for i in range(1, len(markets)):
        if norm(_s(_at(markets[i], 1))) == "YES":
            active_markets.append(norm(_s(_at(markets[i], 0))))

    def market_ok(v: str) -> bool:
        return norm(v) in active_markets

    category_rules_table: list[dict] = []
    for i in range(1, len(category_rules_raw)):
        category_rules_table.append({
            "key": norm(_s(_at(category_rules_raw[i], 0))),
            "value": norm(_s(_at(category_rules_raw[i], 1))),
        })

    cad_headers = [_s(h) for h in cadence_rules_raw[0]] if cadence_rules_raw else []

    def c_idx(name: str) -> int:
        return cad_headers.index(name) if name in cad_headers else -1

    active_cadence_rules: list[CadenceRule] = []
    for i in range(1, len(cadence_rules_raw)):
        row = cadence_rules_raw[i]
        if norm(_s(_at(row, c_idx("active")))) != "YES":
            continue
        min_gap_raw = _at(row, c_idx("minGapWeeks"))
        max_interval_raw = _at(row, c_idx("maxIntervalWeeks"))
        active_cadence_rules.append(CadenceRule(
            ruleId=_s(_at(row, c_idx("ruleId"))),
            scope=norm(_s(_at(row, c_idx("scope")))),
            matchValue=[norm(part) for part in _s(_at(row, c_idx("matchValue"))).split(";") if norm(part)],
            minGapWeeks=None if min_gap_raw == "" else _js_number(min_gap_raw),
            maxIntervalWeeks=None if max_interval_raw == "" else _js_number(max_interval_raw),
            intervalType=norm(_s(_at(row, c_idx("intervalType")))),
            guaranteeType=norm(_s(_at(row, c_idx("guaranteeType")))),
            dedupBy=norm(_s(_at(row, c_idx("dedupBy")))),
            campaignChangeOverride=norm(_s(_at(row, c_idx("campaignChangeOverride")))) == "YES",
            priority=_num(_at(row, c_idx("priority"))),
        ))
    core_rule = next((r for r in active_cadence_rules if r.ruleId == "CORE"), None)
    mandatory_rules = [
        r for r in active_cadence_rules if r.intervalType == "ONCE_PER_CAMPAIGN" and r.guaranteeType == "HARD"
    ]
    # RECURRING + HARD (CORN, GECO): "must be visited at least every
    # maxIntervalWeeks weeks", enforced on an ongoing basis - see
    # office-scripts/PlanningEngine.ts's identical comment for the "at most
    # once per Planning run" scoping note.
    recurring_hard_rules = [
        r for r in active_cadence_rules if r.intervalType == "RECURRING" and r.guaranteeType == "HARD"
    ]
    all_hard_rules = mandatory_rules + recurring_hard_rules

    premium_percent = 20.0
    par_headers = [_s(h) for h in pareto_groups[0]] if pareto_groups else []

    def p_idx(name: str) -> int:
        return par_headers.index(name) if name in par_headers else -1

    for i in range(1, len(pareto_groups)):
        row = pareto_groups[i]
        if _s(_at(row, p_idx("tierId"))) == "PREMIUM_TOP20" and norm(_s(_at(row, p_idx("active")))) == "YES":
            v = _num(_at(row, p_idx("boundaryValue")))
            premium_percent = v if v else 20.0

    weights: dict[str, float] = {}
    for i in range(1, len(score_profiles)):
        row = score_profiles[i]
        if norm(_s(_at(row, 0))) == "DEFAULT":
            weights[norm(_s(_at(row, 1)))] = _num(_at(row, 2))
    SCORE_WEIGHTS = ScoreWeights(
        core=weights.get("CORE", 100000000),
        kategorizaceA=weights.get("KATEGORIZACE_A", 10000000),
        ppt=weights.get("PPT", 1),
        neglectedBonus=weights.get("NEGLECTED_BONUS", 50000),
    )

    capacity_override_map: dict[str, float] = {}
    for i in range(1, len(capacity_override)):
        row = capacity_override[i]
        if not _at(row, 0):
            continue
        key = f"{_s(_at(row, 0))}|{_s(_at(row, 1))}|{_s(_at(row, 2))}"
        capacity_override_map[key] = _num(_at(row, 3))

    los: dict[int, str] = {}
    lot: dict[int, str] = {}
    for i in range(1, len(activity)):
        row = activity[i]
        if not _at(row, 0):
            continue
        start_w = int(_num(_at(row, 2)))
        end_w = int(_num(_at(row, 3)))
        for w in range(start_w, end_w + 1):
            if norm(_s(_at(row, 0))) == "LOS":
                los[w] = _s(_at(row, 1))
            if norm(_s(_at(row, 0))) == "LOT":
                lot[w] = _s(_at(row, 1))

    def campaign_change_soon(week: int) -> bool:
        for i in range(1, SYNC_WINDOW + 1):
            future = week + i
            if los.get(week) != los.get(future) and los.get(future):
                return True
            if lot.get(week) != lot.get(future) and lot.get(future):
                return True
        return False

    # ACTIVITY_PLAN rows reshaped for campaign_starts_within()/should_hold_back()
    # - see office-scripts/PlanningEngine.ts's identical comment.
    activity_plan_windows: list[ActivityPlanWindow] = []
    for i in range(1, len(activity)):
        row = activity[i]
        if not _at(row, 0):
            continue
        activity_plan_windows.append(ActivityPlanWindow(
            activityType=norm(_s(_at(row, 0))),
            activity=_s(_at(row, 1)),
            startWeek=_num(_at(row, 2)),
            endWeek=_num(_at(row, 3)),
        ))

    # ==========================================================================
    # BUILD CANDIDATE LIST FROM POS_MASTER
    # ==========================================================================

    m_headers = [_s(h) for h in pos_master[0]] if pos_master else []

    def midx(name: str) -> int:
        return m_headers.index(name) if name in m_headers else -1

    groups: dict[str, list[POSItem]] = {}

    def _record_rejection(r, reason: str) -> None:
        """Read-out only (rejected_out): capture WHY a POS was filtered out,
        re-reading the same POS_MASTER cells the engine just used. Never
        called when rejected_out is None, so it cannot affect selection."""
        cat = _s(_at(r, midx("category")))
        wsv = _at(r, midx("weeksSinceLastVisit"))
        tech_ov = _at(r, midx("managerOverrideTechnician"))
        rejected_out.append({
            "pos": _s(_at(r, midx("posId"))),
            "nazev": _s(_at(r, midx("nazev"))),
            "market": _s(_at(r, midx("market"))),
            "terminalType": _s(_at(r, midx("terminalType"))),
            "kategorie": cat,
            "categoryRule": category_rule(category_rules_table, norm(cat)),
            "classification": _s(_at(r, midx("classification"))),
            "tech": _s(tech_ov) if tech_ov else _s(_at(r, midx("assignedTechnician"))),
            "ppt": _num(_at(r, midx("ppt"))),
            "weeksSinceLastVisit": None if wsv in ("", None) else _js_number(wsv),
            "status": "Nezařazeno",
            "rejectReason": reason,
        })

    for i in range(1, len(pos_master)):
        r = pos_master[i]
        if not _at(r, midx("posId")):
            continue
        if _s(_at(r, midx("status"))) != "Active":
            if rejected_out is not None:
                _record_rejection(r, f"Neaktivní / uzavřený POS (status={_s(_at(r, midx('status')))})")
            continue
        if _s(_at(r, midx("posId"))) in blacklisted_pos:
            if rejected_out is not None:
                _record_rejection(r, "Na blacklistu")
            continue

        override_type = norm(_s(_at(r, midx("managerOverrideType")) or ""))
        if override_type == "FORCE_EXCLUDE":
            if rejected_out is not None:
                _record_rejection(r, "Ručně vyřazeno manažerem (FORCE_EXCLUDE)")
            continue
        force_include = override_type == "FORCE_INCLUDE"

        category = _s(_at(r, midx("category")))
        rule = category_rule(category_rules_table, norm(category))
        passes_filters = (
            terminal_ok(_s(_at(r, midx("terminalType"))))
            and market_ok(_s(_at(r, midx("market"))))
            and rule != "EXCLUDE"
        )

        if not passes_filters and not force_include:
            if rejected_out is not None:
                reasons = []
                if not terminal_ok(_s(_at(r, midx("terminalType")))):
                    reasons.append("vypnutý typ terminálu")
                if not market_ok(_s(_at(r, midx("market")))):
                    reasons.append("vypnutý partner")
                if rule == "EXCLUDE":
                    reasons.append("kategorie EXCLUDE")
                _record_rejection(r, "; ".join(reasons) or "nevyhovuje plánovacím filtrům")
            continue

        override_tech = _at(r, midx("managerOverrideTechnician"))
        tech = _s(override_tech) if override_tech else _s(_at(r, midx("assignedTechnician")))

        weeks_since_raw = _at(r, midx("weeksSinceLastVisit"))
        weeks_since = None if weeks_since_raw in ("", None) else _js_number(weeks_since_raw)

        item = POSItem(
            pos=_s(_at(r, midx("posId"))),
            tech=tech,
            kategorie=category,
            market=_s(_at(r, midx("market"))),
            classification=_s(_at(r, midx("classification"))),
            nazev=_s(_at(r, midx("nazev"))),
            ulice=_s(_at(r, midx("street"))),
            cislo=_s(_at(r, midx("houseNumber"))),
            mesto=_s(_at(r, midx("city"))),
            oblast=_s(_at(r, midx("area"))),
            posArea=_s(_at(r, midx("posArea"))),
            ppt=_num(_at(r, midx("ppt"))),
            x=_num(_at(r, midx("gpsX"))),
            y=_num(_at(r, midx("gpsY"))),
            weeksSinceLastVisit=weeks_since,
            forceInclude=force_include,
            core=(rule == "CORE"),
            mandatoryRuleId=None,
        )

        for mr in mandatory_rules:
            if matches_cadence_rule_scope(mr, norm(category), norm(item.market)):
                item.mandatoryRuleId = mr.ruleId
                break

        # RECURRING + HARD overdue check (CORN/GECO) - only if no
        # ONCE_PER_CAMPAIGN rule already claimed this item above.
        if not item.mandatoryRuleId:
            for rr in recurring_hard_rules:
                if matches_cadence_rule_scope(rr, norm(category), norm(item.market)) and is_overdue_for_cadence_rule(
                    rr, weeks_since
                ):
                    item.mandatoryRuleId = rr.ruleId
                    break

        # deadlineWeeks (Smart Hold-back / urgency boost) - the item's own
        # matched RECURRING+HARD cadence rule's maxIntervalWeeks (whether or
        # not it is overdue yet), else the global neglected-POS threshold -
        # see office-scripts/PlanningEngine.ts's identical comment.
        matched_hard_rule = next(
            (rr for rr in recurring_hard_rules if matches_cadence_rule_scope(rr, norm(category), norm(item.market))),
            None,
        )
        item.deadlineWeeks = (
            matched_hard_rule.maxIntervalWeeks
            if matched_hard_rule and matched_hard_rule.maxIntervalWeeks is not None
            else NEGLECTED_AFTER
        )

        if item.core and core_rule:
            min_gap = core_rule.minGapWeeks if core_rule.minGapWeeks is not None else 2
        else:
            min_gap = STANDARD_GAP
        score, gap_reason = compute_score(item, SCORE_WEIGHTS, min_gap, NEGLECTED_AFTER)
        item.score = score
        item.reason += gap_reason

        # Score-breakdown read-out (observability only - does NOT feed back
        # into `score`, which was set above by compute_score). Each component
        # mirrors the identical term in core_logic.compute_score(); an
        # assertion below (see _assert_breakdown) checks they still sum to the
        # engine's own base score, so this can never silently drift from the
        # real algorithm.
        if candidates_out is not None:
            item.pptComponent = item.ppt * SCORE_WEIGHTS.ppt
            item.coreBonus = SCORE_WEIGHTS.core if item.core else 0.0
            item.aBonus = SCORE_WEIGHTS.kategorizaceA if item.classification == "A" else 0.0
            item.gapPenalty = 0.0
            item.neglectedBonus = 0.0
            if item.weeksSinceLastVisit is not None:
                if item.weeksSinceLastVisit < min_gap and not item.forceInclude:
                    item.gapPenalty = -1000000.0
                if item.weeksSinceLastVisit >= NEGLECTED_AFTER:
                    item.neglectedBonus = SCORE_WEIGHTS.neglectedBonus
            item.baseScore = score

        groups.setdefault(tech, []).append(item)

    # ADDRESS DEDUP FOR MANDATORY-ELIGIBLE ITEMS (product owner, 2026-07-08,
    # "Kriticke"): two POS with the same street+city under the SAME cadence
    # rule (dedupBy=ADDRESS) must never both be candidates - only the
    # higher-PPT one should survive, for the WHOLE run, not just within a
    # single select_week_pos() call. Doing this here (once, removing the
    # loser from groups[tech] entirely) rather than relying on
    # pick_mandatory() alone is required because add_gps_bonus() draws from
    # the wider `available` pool afterward: two same-address POS are very
    # often GPS-adjacent too, so without this upfront removal the "nearby"
    # GPS bonus could silently re-add the loser right back in the same week.
    for tech in groups:
        mandatory_eligible = [p for p in groups[tech] if p.mandatoryRuleId]
        if not mandatory_eligible:
            continue
        kept = {p.pos for p in pick_mandatory(mandatory_eligible, all_hard_rules)}
        eliminated_ids = {p.pos for p in mandatory_eligible if p.pos not in kept}
        if eliminated_ids:
            groups[tech] = [p for p in groups[tech] if p.pos not in eliminated_ids]

    # PROACTIVE URGENCY BOOST - run BEFORE the geo cluster bonus pass, so a
    # boosted item's real value is what its neighbors' cluster bonus is
    # computed from - see office-scripts/PlanningEngine.ts's identical
    # comment.
    for tech in groups:
        for item in groups[tech]:
            _boost = compute_urgency_boost(
                item.weeksSinceLastVisit, item.deadlineWeeks, URGENCY_BOOST_MAX, URGENCY_BOOST_RAMP_START
            )
            item.score += _boost
            if candidates_out is not None:
                item.urgencyBoost = _boost

    # GEO CLUSTER BONUS - all bonuses computed from each item's BASE score
    # first, THEN applied, so a bonus never leaks into another item's bonus
    # calculation within the same pass (matches PlanningEngine.ts's identical
    # two-pass comment).
    for tech in groups:
        bonuses = [compute_geo_cluster_bonus(item, groups[tech], GEO_CLUSTER_CONFIG) for item in groups[tech]]
        for item, bonus in zip(groups[tech], bonuses):
            item.score += bonus
            if candidates_out is not None:
                item.gpsBonus = bonus

    # PREMIUM / PARETO TOP-20% (PER_TECHNICIAN)
    for tech in groups:
        apply_premium_tier(groups[tech], premium_percent)

    # ==========================================================================
    # GENERATE PLAN
    # ==========================================================================

    committed_by_tech: dict[str, set[str]] = {}
    for row in kept_rows:
        tech = _s(_at(row, 3))
        pos_id = _s(_at(row, 4))
        committed_by_tech.setdefault(tech, set()).add(pos_id)

    output: list[list] = []
    touched_weeks: set[int] = set()

    for tech in groups:
        used: list[POSItem] = [p for p in groups[tech] if p.pos in committed_by_tech.get(tech, set())]
        for w in range(CAMPAIGN_LENGTH):
            week = START_WEEK + w
            if week in locked_weeks:
                continue
            touched_weeks.add(week)
            days = work_days(YEAR, week)
            capacity = resolve_capacity(capacity_override_map, tech, YEAR, week, len(days), TARGET_DAY, TARGET_WEEK)

            if capacity <= 0 or len(days) == 0:
                continue

            # SMART HOLD-BACK (product owner, 2026-07-09, "Kriticke") - see
            # office-scripts/PlanningEngine.ts's identical comment. Mandatory
            # items are never held back.
            used_ids = set(id(p) for p in used)
            available = [
                p
                for p in groups[tech]
                if id(p) not in used_ids
                and not (
                    not p.mandatoryRuleId
                    and should_hold_back(
                        p.classification, p.weeksSinceLastVisit, p.deadlineWeeks, activity_plan_windows, week, HOLDBACK_CONFIG
                    )
                )
            ]
            hold_premium = campaign_change_soon(week)
            base_selection = select_week_pos(available, capacity, all_hard_rules, hold_premium)
            pre_gps_ids = set(p.pos for p in base_selection)
            selected = add_gps_bonus(base_selection, available, GPS_CONFIG)

            for p in selected:
                if p.mandatoryRuleId and "MANDATORY" not in p.reason:
                    p.reason += f"MANDATORY ({p.mandatoryRuleId}) | "
                elif p.pos not in pre_gps_ids:
                    p.reason += "GPS BONUS | "
                elif p.premium:
                    p.reason += "PREMIUM | "

            work_day_inputs = [WorkDay(day=d.day, dateIso=to_cs_cz_date_string(d.date)) for d in days]
            planned = geo_days(selected, work_day_inputs)

            seen_in_group: dict[int, bool] = {}
            for row in planned:
                used.append(row.pos)
                if row.group not in seen_in_group:
                    seen_in_group[row.group] = True
                elif "NEARBY" not in row.pos.reason:
                    row.pos.reason += "NEARBY | "

            for row in planned:
                p = row.pos
                reason = ""
                if p.core:
                    reason += "CORE | "
                reason += p.reason
                output.append([
                    week, row.dateIso, row.day, tech, p.pos,
                    p.kategorie, p.nazev, p.ulice, p.cislo, p.mesto, p.oblast, p.posArea,
                    p.ppt, los.get(week, ""), lot.get(week, ""), reason, row.group,
                ])

            # OBSERVABILITY READ-OUT (candidates_out) - records this week's
            # candidate pool and how each fared, straight from the same
            # `available`/`planned` structures the engine just used to decide.
            # No engine value is recomputed here; a per-item assertion checks
            # the recorded components still sum to the engine's own baseScore.
            if candidates_out is not None:
                available_item_ids = {id(p) for p in available}
                selected_pos_ids = {row.pos.pos for row in planned}
                for p in groups[tech]:
                    if id(p) in used_ids:
                        continue  # already committed to an earlier week this run
                    if p.pos in selected_pos_ids:
                        status = "Vybráno"
                    elif id(p) not in available_item_ids:
                        status = "Odloženo (hold-back)"
                    else:
                        status = "Nevybráno"
                    _assert_breakdown(p)
                    candidates_out.append({
                        "week": week, "tech": tech, "pos": p.pos, "nazev": p.nazev,
                        "kategorie": p.kategorie, "market": p.market,
                        "classification": p.classification, "core": p.core,
                        "ppt": p.ppt, "x": p.x, "y": p.y,
                        "weeksSinceLastVisit": p.weeksSinceLastVisit,
                        "deadlineWeeks": p.deadlineWeeks,
                        "mandatoryRuleId": p.mandatoryRuleId, "premium": p.premium,
                        "score": p.score, "baseScore": p.baseScore,
                        "pptComponent": p.pptComponent, "coreBonus": p.coreBonus,
                        "aBonus": p.aBonus, "gapPenalty": p.gapPenalty,
                        "neglectedBonus": p.neglectedBonus, "urgencyBoost": p.urgencyBoost,
                        "gpsBonus": p.gpsBonus, "status": status,
                        "reasonTags": ("CORE | " if p.core else "") + p.reason,
                    })

    combined = kept_rows + output
    out_ws.get_range("A2:Q200000").clear()
    if combined:
        out_ws.get_range_by_indexes(1, 0, len(combined), 17).set_values(combined)

    if touched_weeks:
        pl_ws = workbook.get_worksheet("PLAN_LIFECYCLE")
        pl_existing = pl_ws.get_used_range()
        pl_rows = pl_existing.get_values() if pl_existing else []
        known_weeks: set[int] = set()
        for i in range(1, len(pl_rows)):
            if int(_num(_at(pl_rows[i], 0))) == YEAR:
                known_weeks.add(int(_num(_at(pl_rows[i], 1))))
        new_lifecycle_rows = [[YEAR, week, "Draft", "", ""] for week in touched_weeks if week not in known_weeks]
        if new_lifecycle_rows:
            start_row = len(pl_rows) if len(pl_rows) > 0 else 1
            pl_ws.get_range_by_indexes(start_row, 0, len(new_lifecycle_rows), 5).set_values(new_lifecycle_rows)

    return (
        f"Planning Engine: generated {len(output)} new planned visits "
        f"({len(kept_rows)} locked-week visits carried over unchanged) across "
        f"{len(groups)} technicians (weeks {START_WEEK}-{START_WEEK + CAMPAIGN_LENGTH - 1})."
    )
