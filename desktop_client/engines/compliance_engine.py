"""
Python port of office-scripts/ComplianceEngine.ts's main(). See
import_engine.py's module docstring for the duplication rationale. See
ComplianceEngine.ts's own file header for the full business-rule rationale
(terminal->POS resolution, campaign-purpose filter, Pending/Nesplneno
semantics etc.) - this file intentionally does not re-explain it, only
mirrors the logic.
"""
from __future__ import annotations

import datetime

from .core_logic import (
    ActualWeek,
    advance_lifecycle_status,
    determine_compliance_status,
    iso_now,
    iso_week_number,
    norm,
    weeks_between,
)
from .dates_logic import iso_monday
from .js_compat import at as _at, num as _num
from .mock_workbook import MockWorkbook


def _to_date(v) -> datetime.date | None:
    if isinstance(v, datetime.datetime):
        return v.date()
    if isinstance(v, datetime.date):
        return v
    try:
        return datetime.date.fromisoformat(str(v)[:10])
    except ValueError:
        return None


def run(workbook: MockWorkbook) -> str:
    def read_table(sheet_name: str) -> list[list]:
        ws = workbook.get_worksheet(sheet_name)
        rng = ws.get_used_range()
        return rng.get_values() if rng else []

    sales_app = read_table("SALESAPP_IMPORT")
    manager_plan_published = read_table("MANAGER_PLAN_PUBLISHED")
    control = read_table("CONTROL")
    visit_history_actual = read_table("VISIT_HISTORY_ACTUAL")
    pos_master = read_table("POS_MASTER")
    plan_lifecycle = read_table("PLAN_LIFECYCLE")

    def setting(name: str, fallback: float) -> float:
        for i in range(1, len(control)):
            if norm(str(control[i][0])) == norm(name):
                try:
                    return float(control[i][1])
                except (TypeError, ValueError):
                    return fallback
        return fallback

    late_cutoff = int(setting("COMPLIANCE_LATE_CUTOFF_WEEKS", 1))
    control_year = int(setting("YEAR", datetime.date.today().year))

    if len(sales_app) < 2:
        return "Compliance Engine: SALESAPP_IMPORT is empty, nothing to do."
    if len(manager_plan_published) < 2:
        return "Compliance Engine: MANAGER_PLAN_PUBLISHED is empty - run Planning Engine then Publish Engine first."

    # ==========================================================================
    # PARSE SALESAPP_IMPORT -> new realized visits (dedup by UID)
    # ==========================================================================

    sa_headers = [norm(str(h)) for h in sales_app[0]]

    def sa_idx(name: str) -> int:
        n = norm(name)
        return sa_headers.index(n) if n in sa_headers else -1

    c_uid = sa_idx("UID")
    c_date = sa_idx("DATE")
    c_state = sa_idx("STATE")
    c_store_uid = sa_idx("STORE UID")
    c_executor = sa_idx("EXECUTOR")
    # "Real duration (h)" (product owner, 2026-07-09) - see
    # office-scripts/ComplianceEngine.ts's identical comment.
    c_duration = sa_idx("REAL DURATION (H)")

    def no_space(v: str) -> str:
        return "".join(v.split())

    c_campaign_purpose = -1
    for i, h in enumerate(sa_headers):
        if "MCHD" in no_space(h) and "NABEHKAMPANE" in no_space(h):
            c_campaign_purpose = i
            break

    pm_headers_for_terminal_map = [str(h) for h in pos_master[0]] if pos_master else []

    def pm_idx_for_terminal_map(name: str) -> int:
        return pm_headers_for_terminal_map.index(name) if name in pm_headers_for_terminal_map else -1

    c_terminal_id = pm_idx_for_terminal_map("terminalId")
    c_pos_id_in_master = pm_idx_for_terminal_map("posId")
    terminal_id_to_pos_id: dict[str, str] = {}
    for i in range(1, len(pos_master)):
        tid = str(pos_master[i][c_terminal_id])
        if tid:
            terminal_id_to_pos_id[tid] = str(pos_master[i][c_pos_id_in_master])

    other_visit_log = read_table("OTHER_VISIT_LOG")
    known_uids: set[str] = {str(row[6]) for row in visit_history_actual[1:]}
    known_other_uids: set[str] = {str(row[5]) for row in other_visit_log[1:]}

    new_visits: list[dict] = []
    other_visits: list[dict] = []
    latest_week = 0
    latest_year = 0

    for i in range(1, len(sales_app)):
        row = sales_app[i]
        uid = str(_at(row, c_uid))
        if not uid or uid in known_uids or uid in known_other_uids:
            continue
        state = norm(str(_at(row, c_state)))
        if state not in ("COMPLETED", "FINALIZED"):
            continue
        date_val = _at(row, c_date)
        date = _to_date(date_val)
        if date is None:
            continue
        week, year = iso_week_number(date)
        if year > latest_year or (year == latest_year and week > latest_week):
            latest_week = week
            latest_year = year
        resolved_pos_id = terminal_id_to_pos_id.get(str(_at(row, c_store_uid)))
        duration_raw = _num(_at(row, c_duration)) if c_duration >= 0 else float("nan")
        duration_hours = duration_raw if duration_raw == duration_raw and duration_raw > 0 else None
        if c_campaign_purpose == -1 or norm(str(_at(row, c_campaign_purpose))) != "ANO":
            if resolved_pos_id:
                other_visits.append({
                    "posId": resolved_pos_id, "date": date, "week": week, "year": year,
                    "executor": str(_at(row, c_executor)), "uid": uid, "durationHours": duration_hours,
                })
            continue
        if not resolved_pos_id:
            continue
        new_visits.append({
            "posId": resolved_pos_id, "date": date, "week": week, "year": year,
            "executor": str(_at(row, c_executor)), "state": state, "uid": uid, "durationHours": duration_hours,
        })

    if latest_week == 0 and len(visit_history_actual) < 2:
        return "Compliance Engine: no realized visits found in SALESAPP_IMPORT (all rows already imported, or none Completed/Finalized)."
    if latest_week == 0:
        for i in range(1, len(visit_history_actual)):
            w = int(_num(visit_history_actual[i][2]))
            y = int(_num(visit_history_actual[i][3]))
            if y > latest_year or (y == latest_year and w > latest_week):
                latest_week = w
                latest_year = y

    # ==========================================================================
    # APPEND VISIT_HISTORY_ACTUAL
    # ==========================================================================

    history_ws = workbook.get_worksheet("VISIT_HISTORY_ACTUAL")
    if new_visits:
        rows = [
            [v["posId"], v["date"].isoformat()[:10], v["week"], v["year"], v["executor"], v["state"], v["uid"],
             v["durationHours"] if v["durationHours"] is not None else ""]
            for v in new_visits
        ]
        start_row = len(visit_history_actual) if len(visit_history_actual) > 0 else 1
        history_ws.get_range_by_indexes(start_row, 0, len(rows), 8).set_values(rows)

    other_visit_ws = workbook.get_worksheet("OTHER_VISIT_LOG")
    if other_visits:
        rows = [
            [v["posId"], v["date"].isoformat()[:10], v["week"], v["year"], v["executor"], v["uid"],
             v["durationHours"] if v["durationHours"] is not None else ""]
            for v in other_visits
        ]
        start_row = len(other_visit_log) if len(other_visit_log) > 0 else 1
        other_visit_ws.get_range_by_indexes(start_row, 0, len(rows), 7).set_values(rows)

    actual_by_pos: dict[str, list[dict]] = {}
    for i in range(1, len(visit_history_actual)):
        row = visit_history_actual[i]
        pos = str(row[0])
        duration_raw = _num(_at(row, 7))
        actual_by_pos.setdefault(pos, []).append({
            "week": int(_num(row[2])), "year": int(_num(row[3])), "date": str(row[1]),
            "durationHours": duration_raw if duration_raw == duration_raw and duration_raw > 0 else None,
        })
    for v in new_visits:
        actual_by_pos.setdefault(v["posId"], []).append(
            {"week": v["week"], "year": v["year"], "date": v["date"].isoformat()[:10], "durationHours": v["durationHours"]}
        )

    # ==========================================================================
    # MATCH MANAGER_PLAN_PUBLISHED -> COMPLIANCE_LOG
    # ==========================================================================

    mp_headers = [str(h) for h in manager_plan_published[0]]

    def mp_idx(name: str) -> int:
        return mp_headers.index(name) if name in mp_headers else -1

    c_week = mp_idx("WEEK")
    c_date2 = mp_idx("DATE")
    c_pos2 = mp_idx("POS")
    c_tech2 = mp_idx("TECHNICIAN")

    planned_set: dict[str, dict] = {}
    for i in range(1, len(manager_plan_published)):
        row = manager_plan_published[i]
        pos_id = str(_at(row, c_pos2))
        raw_week = int(_num(_at(row, c_week)))
        date_val = _at(row, c_date2)
        date = _to_date(date_val) if isinstance(date_val, (datetime.date, datetime.datetime)) else None
        if not pos_id or not raw_week or date is None:
            continue
        week, year = iso_week_number(date)
        planned_set[f"{pos_id}|{week}|{year}"] = {
            "posId": pos_id, "week": week, "year": year, "rawWeek": raw_week, "tech": str(_at(row, c_tech2)),
        }

    compliance_rows: list[list] = []
    now = iso_now()
    pending_by_raw_week: dict[str, bool] = {}

    for key, planned in planned_set.items():
        actuals = [ActualWeek(a["week"], a["year"]) for a in actual_by_pos.get(planned["posId"], [])]
        status = determine_compliance_status(planned["week"], planned["year"], actuals, late_cutoff, latest_week, latest_year)
        matched = next(
            (a for a in actual_by_pos.get(planned["posId"], []) if a["week"] == planned["week"] and a["year"] == planned["year"]),
            None,
        )
        compliance_rows.append([
            planned["posId"], planned["tech"], planned["week"], planned["year"], status,
            matched["date"] if matched else "", matched["week"] if matched else "", now,
            (matched["durationHours"] if matched and matched["durationHours"] is not None else ""),
        ])
        raw_key = f"{control_year}|{planned['rawWeek']}"
        if status == "Pending":
            pending_by_raw_week[raw_key] = True
        elif raw_key not in pending_by_raw_week:
            pending_by_raw_week[raw_key] = False

    for pos_id, actuals in actual_by_pos.items():
        for a in actuals:
            key = f"{pos_id}|{a['week']}|{a['year']}"
            if key not in planned_set:
                compliance_rows.append([
                    pos_id, "", a["week"], a["year"], "Navic_evidovano", a["date"], a["week"], now,
                    a["durationHours"] if a["durationHours"] is not None else "",
                ])

    compliance_ws = workbook.get_worksheet("COMPLIANCE_LOG")
    existing_compliance = compliance_ws.get_used_range()
    compliance_start_row = existing_compliance.get_row_count() if existing_compliance else 1
    if compliance_rows:
        compliance_ws.get_range_by_indexes(compliance_start_row, 0, len(compliance_rows), 9).set_values(compliance_rows)

    # ==========================================================================
    # ADVANCE PLAN LIFECYCLE (Published -> Active -> Closed)
    # ==========================================================================

    if len(plan_lifecycle) >= 2:
        pl_headers = [str(h) for h in plan_lifecycle[0]]

        def pl_idx(name: str) -> int:
            return pl_headers.index(name) if name in pl_headers else -1

        today = datetime.date.today()
        for i in range(1, len(plan_lifecycle)):
            row = plan_lifecycle[i]
            year = int(_num(_at(row, pl_idx("year"))))
            week = int(_num(_at(row, pl_idx("week"))))
            current = str(_at(row, pl_idx("status")))
            key = f"{year}|{week}"
            if key not in pending_by_raw_week:
                continue
            monday_has_passed = iso_monday(year, week) <= today
            next_status = advance_lifecycle_status(current, monday_has_passed, pending_by_raw_week[key])
            if next_status != current:
                workbook.get_worksheet("PLAN_LIFECYCLE").get_range_by_indexes(i, 2, 1, 1).set_value(next_status)
                if next_status == "Closed":
                    workbook.get_worksheet("PLAN_LIFECYCLE").get_range_by_indexes(i, 4, 1, 1).set_value(now)

    # ==========================================================================
    # UPDATE POS_MASTER last-visit fields
    # ==========================================================================

    m_headers = [str(h) for h in pos_master[0]] if pos_master else []

    def midx(name: str) -> int:
        return m_headers.index(name) if name in m_headers else -1

    updated = 0
    for i in range(1, len(pos_master)):
        pos_id = str(pos_master[i][midx("posId")])
        actuals = actual_by_pos.get(pos_id)
        if not actuals:
            continue
        latest = actuals[0]
        for a in actuals[1:]:
            if weeks_between(latest["week"], latest["year"], a["week"], a["year"]) > 0:
                latest = a
        weeks_since = weeks_between(latest["week"], latest["year"], latest_week, latest_year)
        row_index = i
        workbook.get_worksheet("POS_MASTER").get_range_by_indexes(row_index, midx("lastRealVisitDate"), 1, 1).set_value(latest["date"])
        workbook.get_worksheet("POS_MASTER").get_range_by_indexes(row_index, midx("lastRealVisitWeek"), 1, 1).set_value(latest["week"])
        workbook.get_worksheet("POS_MASTER").get_range_by_indexes(row_index, midx("weeksSinceLastVisit"), 1, 1).set_value(weeks_since)
        updated += 1

    extra_count = sum(1 for r in compliance_rows if r[4] == "Navic_evidovano")
    return (
        f"Compliance Engine: {len(new_visits)} new realized visits imported, "
        f"{len(compliance_rows)} compliance rows written ({extra_count} extra), "
        f"{len(other_visits)} other-purpose visits logged to OTHER_VISIT_LOG, "
        f"{updated} POS_MASTER rows updated with real last-visit data. Reference 'now' = week "
        f"{latest_week}/{latest_year}."
    )
