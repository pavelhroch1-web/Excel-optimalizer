"""
Python port of office-scripts/AdvisorEngine.ts's main(). See
import_engine.py's module docstring for the duplication rationale. See
AdvisorEngine.ts's own file header for the full business-rule rationale
(alert types, tunable thresholds, what's deliberately not in this version) -
this file intentionally does not re-explain it, only mirrors the logic.
"""
from __future__ import annotations

import datetime

from .core_logic import (
    ComplianceOutcome,
    NeglectCandidate,
    OpenPlanRow,
    POSCurrentState,
    WeeklyVolume,
    compute_failure_rate_by_group,
    compute_volume_trend,
    find_neglected,
    find_published_plan_drift,
    find_unplanned_active_pos,
    iso_now,
    latest_by_key,
    norm,
)
from .js_compat import at as _at, num as _num
from .mock_workbook import MockWorkbook


class _DedupRow:
    def __init__(self, key, timestamp, week, year, status, tech, posId):
        self.key = key
        self.timestamp = timestamp
        self.week = week
        self.year = year
        self.status = status
        self.tech = tech
        self.posId = posId


def run(workbook: MockWorkbook) -> str:
    def read_table(sheet_name: str) -> list[list]:
        ws = workbook.get_worksheet(sheet_name)
        rng = ws.get_used_range()
        return rng.get_values() if rng else []

    pos_master = read_table("POS_MASTER")
    compliance_log = read_table("COMPLIANCE_LOG")
    control = read_table("CONTROL")

    def setting(name: str, fallback: float) -> float:
        for i in range(1, len(control)):
            if norm(str(control[i][0])) == norm(name):
                try:
                    v = float(control[i][1])
                    return v if v == v else fallback
                except (TypeError, ValueError):
                    return fallback
        return fallback

    neglected_after = setting("NEGLECTED_AFTER_WEEKS", 26)
    neglect_warning_ratio = setting("ADVISOR_NEGLECT_WARNING_RATIO_PERCENT", 80) / 100
    trend_window = setting("ADVISOR_TREND_WINDOW_WEEKS", 4)
    overload_warning_rate = setting("ADVISOR_OVERLOAD_WARNING_RATE_PERCENT", 20) / 100
    overload_critical_rate = setting("ADVISOR_OVERLOAD_CRITICAL_RATE_PERCENT", 35) / 100
    volume_trailing_weeks = int(setting("ADVISOR_VOLUME_TRAILING_WEEKS", 8))
    volume_baseline_weeks = int(setting("ADVISOR_VOLUME_BASELINE_WEEKS", 8))
    volume_threshold_percent = setting("ADVISOR_VOLUME_THRESHOLD_PERCENT", 25)

    if len(pos_master) < 2:
        return "Advisor Engine: POS_MASTER is empty - run Import Engine first."

    # ==========================================================================
    # NEGLECT_RISK
    # ==========================================================================

    m_headers = [str(h) for h in pos_master[0]]

    def midx(name: str) -> int:
        return m_headers.index(name) if name in m_headers else -1

    neglect_candidates: list[NeglectCandidate] = []
    pos_area: dict[str, str] = {}
    for i in range(1, len(pos_master)):
        r = pos_master[i]
        pos_id = str(_at(r, midx("posId")))
        if not pos_id or str(_at(r, midx("status"))) != "Active":
            continue
        weeks_since_raw = _at(r, midx("weeksSinceLastVisit"))
        weeks_since = None if weeks_since_raw in ("", None) else float(weeks_since_raw)
        neglect_candidates.append(NeglectCandidate(posId=pos_id, weeksSinceLastVisit=weeks_since))
        pos_area[pos_id] = str(_at(r, midx("area")))

    critical_neglect = set(find_neglected(neglect_candidates, neglected_after))
    warning_neglect = set(find_neglected(neglect_candidates, round(neglected_after * neglect_warning_ratio)))

    now = iso_now()
    alert_rows: list[list] = []

    for pos_id in critical_neglect:
        alert_rows.append(["NEGLECT_RISK", "CRITICAL", "POS", pos_id,
                            f"POS {pos_id} nebylo navstiveno {neglected_after:g}+ tydnu.", now])
    for pos_id in warning_neglect:
        if pos_id not in critical_neglect:
            alert_rows.append(["NEGLECT_RISK", "WARNING", "POS", pos_id,
                                f"POS {pos_id} se blizi hranici {neglected_after:g} tydnu bez navstevy.", now])

    # ==========================================================================
    # TECHNICIAN_OVERLOAD / REGIONAL_UNDERPERFORMANCE
    # ==========================================================================

    if len(compliance_log) >= 2:
        c_headers = [str(h) for h in compliance_log[0]]

        def cidx(name: str) -> int:
            return c_headers.index(name) if name in c_headers else -1

        raw_rows: list[_DedupRow] = []
        for i in range(1, len(compliance_log)):
            row = compliance_log[i]
            pos_id = str(_at(row, cidx("posId")))
            week = int(_num(_at(row, cidx("plannedWeek"))))
            year = int(_num(_at(row, cidx("plannedYear"))))
            raw_rows.append(_DedupRow(
                key=f"{pos_id}|{week}|{year}",
                timestamp=str(_at(row, cidx("evaluatedAt"))),
                week=week, year=year,
                status=str(_at(row, cidx("status"))),
                tech=str(_at(row, cidx("technician"))),
                posId=pos_id,
            ))
        deduped_rows = latest_by_key(raw_rows)

        latest_week = 0
        latest_year = 0
        for r in deduped_rows:
            if r.year > latest_year or (r.year == latest_year and r.week > latest_week):
                latest_week = r.week
                latest_year = r.year

        tech_rows: list[ComplianceOutcome] = []
        region_rows: list[ComplianceOutcome] = []
        for r in deduped_rows:
            within_window = latest_week - r.week + (latest_year - r.year) * 52 < trend_window
            if not within_window:
                continue
            tech_rows.append(ComplianceOutcome(group=r.tech, status=r.status))
            region_rows.append(ComplianceOutcome(group=pos_area.get(r.posId, ""), status=r.status))

        tech_rates = compute_failure_rate_by_group(tech_rows, ["Nesplneno"])
        region_rates = compute_failure_rate_by_group(region_rows, ["Nesplneno"])

        for t in tech_rates:
            msg = f"Technik {t.group}: {t.failed}/{t.total} planovanych navstev nesplneno za posledni {trend_window:g} tydny."
            if t.rate >= overload_critical_rate:
                alert_rows.append(["TECHNICIAN_OVERLOAD", "CRITICAL", "TECHNICIAN", t.group, msg, now])
            elif t.rate >= overload_warning_rate:
                alert_rows.append(["TECHNICIAN_OVERLOAD", "WARNING", "TECHNICIAN", t.group, msg, now])
        for r in region_rates:
            if not r.group:
                continue
            msg = f"Region {r.group}: {r.failed}/{r.total} planovanych navstev nesplneno za posledni {trend_window:g} tydny."
            if r.rate >= overload_critical_rate:
                alert_rows.append(["REGIONAL_UNDERPERFORMANCE", "CRITICAL", "REGION", r.group, msg, now])
            elif r.rate >= overload_warning_rate:
                alert_rows.append(["REGIONAL_UNDERPERFORMANCE", "WARNING", "REGION", r.group, msg, now])

    # ==========================================================================
    # VOLUME_TREND_SIGNAL
    # ==========================================================================

    visit_history_actual = read_table("VISIT_HISTORY_ACTUAL")
    if len(visit_history_actual) >= 2:
        v_headers = [str(h) for h in visit_history_actual[0]]

        def vidx(name: str) -> int:
            return v_headers.index(name) if name in v_headers else -1

        counts_by_week: dict[str, WeeklyVolume] = {}
        for i in range(1, len(visit_history_actual)):
            r = visit_history_actual[i]
            week = int(_num(_at(r, vidx("week"))))
            year = int(_num(_at(r, vidx("year"))))
            if not week or not year:
                continue
            key = f"{year}|{week}"
            if key not in counts_by_week:
                counts_by_week[key] = WeeklyVolume(week=week, year=year, count=0)
            counts_by_week[key].count += 1

        signal = compute_volume_trend(
            list(counts_by_week.values()), volume_trailing_weeks, volume_baseline_weeks, volume_threshold_percent
        )
        if signal and signal.significant:
            direction = "vyssi" if signal.ratioPercent > 100 else "nizsi"
            alert_rows.append([
                "VOLUME_TREND_SIGNAL", "INFO", "NETWORK", "ALL",
                f"Objem realizovanych navstev za posledni {volume_trailing_weeks:g} tydny je "
                f"{abs(round(signal.ratioPercent - 100))}% {direction} nez v predchozich {volume_baseline_weeks:g} tydnech "
                f"({round(signal.trailingAvg * 10) / 10} vs {round(signal.baselineAvg * 10) / 10} navstev/tyden v prumeru). "
                "Informativni signal, zadna akce neni automaticky navrzena.",
                now,
            ])

    # ==========================================================================
    # PUBLISHED PLAN DRIFT + UNPLANNED ACTIVE POS
    # ==========================================================================

    manager_plan_published = read_table("MANAGER_PLAN_PUBLISHED")
    plan_lifecycle_for_drift = read_table("PLAN_LIFECYCLE")
    if len(manager_plan_published) >= 2:
        open_weeks: set[str] = set()
        if len(plan_lifecycle_for_drift) >= 2:
            pl_headers = [str(h) for h in plan_lifecycle_for_drift[0]]

            def pl_idx(name: str) -> int:
                return pl_headers.index(name) if name in pl_headers else -1

            for i in range(1, len(plan_lifecycle_for_drift)):
                row = plan_lifecycle_for_drift[i]
                status = str(_at(row, pl_idx("status")))
                if status in ("Published", "Active"):
                    open_weeks.add(f"{_at(row, pl_idx('year'))}|{_at(row, pl_idx('week'))}")

        mp_headers = [str(h) for h in manager_plan_published[0]]

        def mp_idx(name: str) -> int:
            return mp_headers.index(name) if name in mp_headers else -1

        c_week3 = mp_idx("WEEK")
        c_pos3 = mp_idx("POS")
        c_tech3 = mp_idx("TECHNICIAN")

        open_plan_rows: list[OpenPlanRow] = []
        ever_planned_pos_ids: set[str] = set()
        for i in range(1, len(manager_plan_published)):
            row = manager_plan_published[i]
            pos_id = str(_at(row, c_pos3))
            week = str(_at(row, c_week3))
            if not pos_id:
                continue
            ever_planned_pos_ids.add(pos_id)
            is_open = any(key.endswith(f"|{week}") for key in open_weeks)
            if is_open:
                open_plan_rows.append(OpenPlanRow(posId=pos_id, plannedTechnician=str(_at(row, c_tech3))))

        pos_state: dict[str, POSCurrentState] = {}
        active_pos_ids: list[str] = []
        for i in range(1, len(pos_master)):
            pos_id = str(pos_master[i][midx("posId")])
            if not pos_id:
                continue
            status = str(_at(pos_master[i], midx("status")))
            pos_state[pos_id] = POSCurrentState(status=status, assignedTechnician=str(_at(pos_master[i], midx("assignedTechnician"))))
            if status == "Active":
                active_pos_ids.append(pos_id)

        drift_alerts = find_published_plan_drift(open_plan_rows, pos_state)
        for d in drift_alerts:
            if d.type == "CLOSED_POS_IN_PLAN":
                alert_rows.append([
                    "CLOSED_POS_IN_PLAN", "WARNING", "POS", d.posId,
                    f"POS {d.posId} je v aktualne otevrenem publikovanem planu (technik {d.plannedTechnician}), "
                    "ale v POS_MASTER je nyni veden jako Closed.",
                    now,
                ])
            else:
                alert_rows.append([
                    "TECHNICIAN_REASSIGNED", "WARNING", "POS", d.posId,
                    f"POS {d.posId} byl publikovan pro technika {d.plannedTechnician}, "
                    f"ale POS_MASTER nyni uvadi jineho technika ({d.currentTechnician}).",
                    now,
                ])

        for pos_id in find_unplanned_active_pos(active_pos_ids, ever_planned_pos_ids):
            alert_rows.append([
                "UNPLANNED_ACTIVE_POS", "INFO", "POS", pos_id,
                f"POS {pos_id} je Active v POS_MASTER, ale nebyl zatim soucasti zadneho publikovaneho planu.",
                now,
            ])

    # ==========================================================================
    # WRITE ADVISOR_LOG
    # ==========================================================================

    advisor_ws = workbook.get_worksheet("ADVISOR_LOG")
    existing = advisor_ws.get_used_range()
    start_row = existing.get_row_count() if existing else 1
    if alert_rows:
        advisor_ws.get_range_by_indexes(start_row, 0, len(alert_rows), 6).set_values(alert_rows)

    def count(type_name: str) -> int:
        return sum(1 for r in alert_rows if r[0] == type_name)

    return (
        f"Advisor Engine: {len(alert_rows)} alerts written "
        f"({count('NEGLECT_RISK')} neglect risk, {count('TECHNICIAN_OVERLOAD')} technician overload, "
        f"{count('REGIONAL_UNDERPERFORMANCE')} regional underperformance, {count('VOLUME_TREND_SIGNAL')} volume trend signal, "
        f"{count('CLOSED_POS_IN_PLAN')} closed POS in plan, {count('TECHNICIAN_REASSIGNED')} technician reassigned, "
        f"{count('UNPLANNED_ACTIVE_POS')} unplanned active POS)."
    )
