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
    CadenceRule,
    GpsBonusConfig,
    POSItem,
    ScoreWeights,
    add_gps_bonus,
    apply_premium_tier,
    category_rule,
    compute_score,
    geo_days,
    is_overdue_for_cadence_rule,
    matches_cadence_rule_scope,
    norm,
    resolve_capacity,
    select_week_pos,
    WorkDay,
)
from .dates_logic import to_cs_cz_date_string, work_days
from .js_compat import at as _at, js_number as _js_number, num as _num, s as _s
from .mock_workbook import MockWorkbook


def run(workbook: MockWorkbook) -> str:
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

    START_WEEK = int(setting("CAMPAIGN_START_WEEK", 30))
    CAMPAIGN_LENGTH = int(setting("CAMPAIGN_LENGTH", 4))
    TARGET_DAY = setting("TARGET_VISITS_DAY", 8)
    # Optional flat weekly capacity target - see resolve_capacity()'s
    # docstring. Per-technician/week CAPACITY_OVERRIDE still wins over this.
    TARGET_WEEK = setting_optional("TARGET_VISITS_WEEK")
    STANDARD_GAP = setting("STANDARD_VISIT_GAP", 8)
    NEGLECTED_AFTER = setting("NEGLECTED_AFTER_WEEKS", 26)
    YEAR = int(setting("YEAR", datetime.date.today().year))
    SYNC_WINDOW = int(setting("SYNC_WINDOW_WEEKS", 1))
    GPS_CONFIG = GpsBonusConfig(
        enabled=setting("GPS_EXTRA_ENABLED", 0) == 1,
        radiusMeters=setting("GPS_EXTRA_RADIUS_METERS", 300),
        maxVisits=int(setting("GPS_EXTRA_MAX_VISITS", 5)),
    )

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

    # ==========================================================================
    # BUILD CANDIDATE LIST FROM POS_MASTER
    # ==========================================================================

    m_headers = [_s(h) for h in pos_master[0]] if pos_master else []

    def midx(name: str) -> int:
        return m_headers.index(name) if name in m_headers else -1

    groups: dict[str, list[POSItem]] = {}

    for i in range(1, len(pos_master)):
        r = pos_master[i]
        if not _at(r, midx("posId")):
            continue
        if _s(_at(r, midx("status"))) != "Active":
            continue

        override_type = norm(_s(_at(r, midx("managerOverrideType")) or ""))
        if override_type == "FORCE_EXCLUDE":
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

        if item.core and core_rule:
            min_gap = core_rule.minGapWeeks if core_rule.minGapWeeks is not None else 2
        else:
            min_gap = STANDARD_GAP
        score, gap_reason = compute_score(item, SCORE_WEIGHTS, min_gap, NEGLECTED_AFTER)
        item.score = score
        item.reason += gap_reason

        groups.setdefault(tech, []).append(item)

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

            used_ids = set(id(p) for p in used)
            available = [p for p in groups[tech] if id(p) not in used_ids]
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
