"""
Python port of office-scripts/PerformanceEngine.ts's main(). See
import_engine.py's module docstring for the duplication rationale. See
PerformanceEngine.ts's own file header for the full business-rule rationale
(tracking gate, route-km estimate, flaka riziko, monthKey, etc.) - this file
intentionally does not re-explain it, only mirrors the logic.
"""
from __future__ import annotations

import datetime

from .core_logic import distance_km, iso_now, iso_week_number, latest_by_key, norm
from .dates_logic import iso_monday
from .js_compat import at as _at, num as _num
from .mock_workbook import MockWorkbook


class _ComplianceRow:
    def __init__(self, key, timestamp, posId, technician, week, year, status, matchedActualDate):
        self.key = key
        self.timestamp = timestamp
        self.posId = posId
        self.technician = technician
        self.week = week
        self.year = year
        self.status = status
        self.matchedActualDate = matchedActualDate


class Bucket:
    def __init__(self, technician: str, year: int, week: int):
        self.technician = technician
        self.year = year
        self.week = week
        self.areaCounts: dict[str, int] = {}
        self.plannedVisits = 0
        self.realizedVisits = 0
        self.splnenoVcas = 0
        self.splnenoPozde = 0
        self.nesplneno = 0
        self.navicEvidovano = 0
        self.otherVisits = 0
        self.visitsByDay = [0, 0, 0, 0, 0]
        self.possByDay: list[list[str]] = [[], [], [], [], []]


def _to_date(v) -> datetime.date | None:
    if isinstance(v, datetime.datetime):
        return v.date()
    if isinstance(v, datetime.date):
        return v
    return None


def run(workbook: MockWorkbook) -> str:
    def read_table(sheet_name: str) -> list[list]:
        ws = workbook.get_worksheet(sheet_name)
        rng = ws.get_used_range()
        return rng.get_values() if rng else []

    pos_master = read_table("POS_MASTER")
    manager_plan_published = read_table("MANAGER_PLAN_PUBLISHED")
    compliance_log = read_table("COMPLIANCE_LOG")
    other_visit_log = read_table("OTHER_VISIT_LOG")
    control = read_table("CONTROL")
    plan_lifecycle = read_table("PLAN_LIFECYCLE")

    def setting(name: str, fallback: float) -> float:
        for i in range(1, len(control)):
            if norm(str(control[i][0])) == norm(name):
                try:
                    v = float(control[i][1])
                    return v if v == v else fallback
                except (TypeError, ValueError):
                    return fallback
        return fallback

    control_year = int(setting("YEAR", datetime.date.today().year))
    flakani_window_weeks = int(setting("FLAKANI_WINDOW_WEEKS", 4))
    flakani_bad_week_threshold_percent = setting("FLAKANI_BAD_WEEK_THRESHOLD_PERCENT", 70)
    flakani_bad_weeks_count = int(setting("FLAKANI_BAD_WEEKS_COUNT", 2))

    # ==========================================================================
    # PLAN_LIFECYCLE -> tracking-started weeks (raw + true-ISO)
    # ==========================================================================

    tracking_started_raw_weeks: dict[str, bool] = {}
    true_iso_tracking_started: dict[str, bool] = {}
    if len(plan_lifecycle) >= 2:
        pl_headers = [str(h) for h in plan_lifecycle[0]]

        def pl_idx(name: str) -> int:
            return pl_headers.index(name) if name in pl_headers else -1

        tracking_col = pl_idx("trackingStartedAt")
        if tracking_col >= 0:
            for i in range(1, len(plan_lifecycle)):
                row = plan_lifecycle[i]
                if str(_at(row, tracking_col) or "") != "":
                    raw_year = int(_num(_at(row, pl_idx("year"))))
                    raw_week = int(_num(_at(row, pl_idx("week"))))
                    tracking_started_raw_weeks[f"{raw_year}|{raw_week}"] = True
                    week, year = iso_week_number(iso_monday(raw_year, raw_week))
                    true_iso_tracking_started[f"{year}|{week}"] = True

    # ==========================================================================
    # POS_MASTER -> posId -> {area, technician, name, gps} lookup
    # ==========================================================================

    pm_headers = [str(h) for h in pos_master[0]] if pos_master else []

    def pm_idx(name: str) -> int:
        return pm_headers.index(name) if name in pm_headers else -1

    pos_area: dict[str, str] = {}
    pos_technician: dict[str, str] = {}
    pos_name: dict[str, str] = {}
    pos_gps: dict[str, tuple[float, float]] = {}
    for i in range(1, len(pos_master)):
        row = pos_master[i]
        pos_id = str(_at(row, pm_idx("posId")))
        if not pos_id:
            continue
        pos_area[pos_id] = str(_at(row, pm_idx("area")) or "")
        pos_name[pos_id] = str(_at(row, pm_idx("nazev")) or "")
        override = str(_at(row, pm_idx("managerOverrideTechnician")) or "")
        pos_technician[pos_id] = override or str(_at(row, pm_idx("assignedTechnician")) or "")
        gps_x = _num(_at(row, pm_idx("gpsX")))
        gps_y = _num(_at(row, pm_idx("gpsY")))
        if gps_x != 0 or gps_y != 0:
            pos_gps[pos_id] = (gps_x, gps_y)

    # ==========================================================================
    # Aggregation buckets
    # ==========================================================================

    buckets: dict[str, Bucket] = {}

    def bucket_for(technician: str, year: int, week: int) -> Bucket:
        key = f"{technician}|{year}|{week}"
        if key not in buckets:
            buckets[key] = Bucket(technician, year, week)
        return buckets[key]

    # ==========================================================================
    # MANAGER_PLAN_PUBLISHED -> plannedVisits + region tally + planned order
    # ==========================================================================

    mp_headers = [str(h) for h in manager_plan_published[0]] if manager_plan_published else []

    def mp_idx(name: str) -> int:
        return mp_headers.index(name) if name in mp_headers else -1

    planned_order_by_tech_date: dict[str, list[str]] = {}
    planned_tech_by_pos_week: dict[str, str] = {}
    for i in range(1, len(manager_plan_published)):
        row = manager_plan_published[i]
        tech = str(_at(row, mp_idx("TECHNICIAN")) or "")
        pos_id = str(_at(row, mp_idx("POS")) or "")
        date_val = _at(row, mp_idx("DATE"))
        date = _to_date(date_val)
        if not tech or date is None:
            continue
        date_key = date.isoformat()[:10]
        order_key = f"{tech}|{date_key}"
        planned_order_by_tech_date.setdefault(order_key, []).append(pos_id)

        week, year = iso_week_number(date)
        planned_tech_by_pos_week[f"{pos_id}|{week}|{year}"] = tech
        raw_week = int(_num(_at(row, mp_idx("WEEK"))))
        tracking_started = tracking_started_raw_weeks.get(f"{control_year}|{raw_week}") is True
        if not tracking_started:
            continue
        bucket = bucket_for(tech, year, week)
        bucket.plannedVisits += 1
        area = pos_area.get(pos_id, "")
        if area:
            bucket.areaCounts[area] = bucket.areaCounts.get(area, 0) + 1

    # ==========================================================================
    # COMPLIANCE_LOG -> dedupe, then aggregate realized/status counts
    # ==========================================================================

    cl_headers = [str(h) for h in compliance_log[0]] if compliance_log else []

    def cl_idx(name: str) -> int:
        return cl_headers.index(name) if name in cl_headers else -1

    raw_rows: list[_ComplianceRow] = []
    for i in range(1, len(compliance_log)):
        row = compliance_log[i]
        pos_id = str(_at(row, cl_idx("posId")))
        week = int(_num(_at(row, cl_idx("plannedWeek"))))
        year = int(_num(_at(row, cl_idx("plannedYear"))))
        if not pos_id or not week or not year:
            continue
        date_val = _at(row, cl_idx("matchedActualDate"))
        raw_rows.append(_ComplianceRow(
            key=f"{pos_id}|{week}|{year}",
            timestamp=str(_at(row, cl_idx("evaluatedAt"))),
            posId=pos_id,
            technician=str(_at(row, cl_idx("technician")) or ""),
            week=week, year=year,
            status=str(_at(row, cl_idx("status"))),
            matchedActualDate=_to_date(date_val),
        ))
    deduped_rows = latest_by_key(raw_rows)

    day_index = {0: 0, 1: 1, 2: 2, 3: 3, 4: 4}  # Python Mon=0..Sun=6, Sat/Sun (5,6) excluded

    nesplneno_by_tech_pos: dict[str, dict] = {}

    for r in deduped_rows:
        if not true_iso_tracking_started.get(f"{r.year}|{r.week}"):
            continue
        tech = r.technician or pos_technician.get(r.posId, "") or ""
        if not tech:
            continue
        bucket = bucket_for(tech, r.year, r.week)
        if r.status == "Splneno_vcas":
            bucket.splnenoVcas += 1
            bucket.realizedVisits += 1
        elif r.status == "Splneno_pozde":
            bucket.splnenoPozde += 1
            bucket.realizedVisits += 1
        elif r.status == "Nesplneno":
            bucket.nesplneno += 1
            key = f"{tech}|{r.posId}"
            if key not in nesplneno_by_tech_pos:
                nesplneno_by_tech_pos[key] = {"technician": tech, "posId": r.posId, "count": 0}
            nesplneno_by_tech_pos[key]["count"] += 1
        elif r.status == "Navic_evidovano":
            bucket.navicEvidovano += 1
        if r.matchedActualDate and r.status in ("Splneno_vcas", "Splneno_pozde"):
            weekday = r.matchedActualDate.weekday()  # Mon=0..Sun=6
            if weekday in day_index:
                bucket.visitsByDay[day_index[weekday]] += 1
                bucket.possByDay[day_index[weekday]].append(r.posId)

    # ==========================================================================
    # OTHER_VISIT_LOG -> otherVisits per (technician, year, week)
    # ==========================================================================

    ov_headers = [str(h) for h in other_visit_log[0]] if other_visit_log else []

    def ov_idx(name: str) -> int:
        return ov_headers.index(name) if name in ov_headers else -1

    for i in range(1, len(other_visit_log)):
        row = other_visit_log[i]
        pos_id = str(_at(row, ov_idx("posId")))
        week = int(_num(_at(row, ov_idx("week"))))
        year = int(_num(_at(row, ov_idx("year"))))
        if not pos_id or not week or not year:
            continue
        if not true_iso_tracking_started.get(f"{year}|{week}"):
            continue
        tech = planned_tech_by_pos_week.get(f"{pos_id}|{week}|{year}") or pos_technician.get(pos_id, "") or ""
        if not tech:
            continue
        bucket_for(tech, year, week).otherVisits += 1

    # ==========================================================================
    # Route-efficiency helpers
    # ==========================================================================

    def ordered_pos_for_day(technician: str, year: int, week: int, day_idx: int, pos_ids: list[str]) -> list[str]:
        monday = iso_monday(year, week)
        visit_date = monday + datetime.timedelta(days=day_idx)
        date_key = visit_date.isoformat()[:10]
        planned_order = planned_order_by_tech_date.get(f"{technician}|{date_key}", [])
        unique = list(dict.fromkeys(pos_ids))

        def sort_key(pos_id: str):
            idx = planned_order.index(pos_id) if pos_id in planned_order else None
            if idx is not None:
                return (0, idx, "")
            return (1, 0, pos_id)

        return sorted(unique, key=sort_key)

    def route_km_for_day(technician: str, year: int, week: int, day_idx: int, pos_ids: list[str]) -> float:
        if len(pos_ids) < 2:
            return 0
        ordered = ordered_pos_for_day(technician, year, week, day_idx, pos_ids)
        total_km = 0.0
        resolved_stops = 0
        prev = None
        for pos_id in ordered:
            gps = pos_gps.get(pos_id)
            if gps is None:
                continue
            resolved_stops += 1
            if prev is not None:
                total_km += distance_km(prev[0], prev[1], gps[0], gps[1])
            prev = gps
        return round(total_km * 10) / 10 if resolved_stops >= 2 else 0

    # ==========================================================================
    # WRITE TECHNICIAN_PERFORMANCE_LOG
    # ==========================================================================

    now = iso_now()
    out_rows: list[list] = []
    for b in buckets.values():
        top_area = ""
        top_area_count = 0
        for area, cnt in b.areaCounts.items():
            if cnt > top_area_count:
                top_area = area
                top_area_count = cnt
        compliance_percent = round((b.realizedVisits / b.plannedVisits) * 1000) / 10 if b.plannedVisits > 0 else 0
        km_by_day = [route_km_for_day(b.technician, b.year, b.week, i, pos_ids) for i, pos_ids in enumerate(b.possByDay)]
        pos_list_by_day = [
            ", ".join(
                pid + (f" - {pos_name[pid]}" if pos_name.get(pid) else "")
                for pid in ordered_pos_for_day(b.technician, b.year, b.week, i, pos_ids)
            )
            for i, pos_ids in enumerate(b.possByDay)
        ]
        month_date = iso_monday(b.year, b.week)
        month_key = month_date.year * 100 + month_date.month
        out_rows.append([
            b.technician, b.year, b.week, top_area,
            b.plannedVisits, b.realizedVisits,
            b.splnenoVcas, b.splnenoPozde, b.nesplneno, b.navicEvidovano,
            compliance_percent,
            b.visitsByDay[0], b.visitsByDay[1], b.visitsByDay[2], b.visitsByDay[3], b.visitsByDay[4],
            now,
            km_by_day[0], km_by_day[1], km_by_day[2], km_by_day[3], km_by_day[4],
            b.otherVisits,
            pos_list_by_day[0], pos_list_by_day[1], pos_list_by_day[2], pos_list_by_day[3], pos_list_by_day[4],
            month_key,
        ])

    header_row = [
        "technician", "year", "week", "region",
        "plannedVisits", "realizedVisits",
        "splnenoVcas", "splnenoPozde", "nesplneno", "navicEvidovano",
        "compliancePercent",
        "visitsMon", "visitsTue", "visitsWed", "visitsThu", "visitsFri",
        "updatedAt",
        "kmMon", "kmTue", "kmWed", "kmThu", "kmFri",
        "otherVisits",
        "posListMon", "posListTue", "posListWed", "posListThu", "posListFri",
        "monthKey",
    ]
    out_ws = workbook.get_worksheet("TECHNICIAN_PERFORMANCE_LOG")
    out_ws.get_range("A2:AC100000").clear()
    out_ws.get_range_by_indexes(0, 0, 1, len(header_row)).set_values([header_row])
    if out_rows:
        out_ws.get_range_by_indexes(1, 0, len(out_rows), len(header_row)).set_values(out_rows)

    # ==========================================================================
    # WRITE TECHNICIAN_PERFORMANCE_SUMMARY
    # ==========================================================================

    by_tech_weeks: dict[str, list[dict]] = {}
    for b in buckets.values():
        top_area = ""
        top_area_count = 0
        for area, cnt in b.areaCounts.items():
            if cnt > top_area_count:
                top_area = area
                top_area_count = cnt
        compliance_percent = round((b.realizedVisits / b.plannedVisits) * 1000) / 10 if b.plannedVisits > 0 else 0
        km_by_day_for_summary = [route_km_for_day(b.technician, b.year, b.week, i, pos_ids) for i, pos_ids in enumerate(b.possByDay)]
        max_km_day = max(km_by_day_for_summary)
        by_tech_weeks.setdefault(b.technician, []).append({
            "year": b.year, "week": b.week, "region": top_area,
            "plannedVisits": b.plannedVisits, "realizedVisits": b.realizedVisits,
            "splnenoVcas": b.splnenoVcas, "splnenoPozde": b.splnenoPozde,
            "nesplneno": b.nesplneno, "navicEvidovano": b.navicEvidovano,
            "compliancePercent": compliance_percent, "maxKmDay": max_km_day,
        })

    summary_rows: list[list] = []
    for tech, weeks in by_tech_weeks.items():
        weeks = sorted(weeks, key=lambda w: w["year"] * 100 + w["week"], reverse=True)
        latest = weeks[0]
        prev = weeks[1] if len(weeks) > 1 else None
        weeks_with_plan = [w for w in weeks if w["plannedVisits"] > 0]
        long_run_avg_compliance = (
            round((sum(w["compliancePercent"] for w in weeks_with_plan) / len(weeks_with_plan)) * 10) / 10
            if weeks_with_plan else 0
        )
        trend_delta = round((latest["compliancePercent"] - prev["compliancePercent"]) * 10) / 10 if prev else ""
        recent_weeks = weeks_with_plan[:flakani_window_weeks]
        bad_weeks_in_window = sum(1 for w in recent_weeks if w["compliancePercent"] < flakani_bad_week_threshold_percent)
        flaka_riziko = "Ano" if bad_weeks_in_window >= flakani_bad_weeks_count else "Ne"
        summary_rows.append([
            tech, latest["region"], latest["year"], latest["week"],
            latest["plannedVisits"], latest["realizedVisits"],
            latest["splnenoVcas"], latest["splnenoPozde"], latest["nesplneno"], latest["navicEvidovano"],
            latest["compliancePercent"], long_run_avg_compliance, trend_delta,
            bad_weeks_in_window, flaka_riziko, latest["maxKmDay"],
        ])

    summary_header_row = [
        "technician", "region", "latestYear", "latestWeek",
        "plannedVisits", "realizedVisits", "splnenoVcas", "splnenoPozde", "nesplneno", "navicEvidovano",
        "compliancePercent", "longRunAvgCompliance", "trendDelta",
        "badWeeksInWindow", "flakaRiziko", "maxKmDay",
    ]
    summary_ws = workbook.get_worksheet("TECHNICIAN_PERFORMANCE_SUMMARY")
    summary_ws.get_range("A2:P100000").clear()
    summary_ws.get_range_by_indexes(0, 0, 1, len(summary_header_row)).set_values([summary_header_row])
    if summary_rows:
        summary_ws.get_range_by_indexes(1, 0, len(summary_rows), len(summary_header_row)).set_values(summary_rows)

    # ==========================================================================
    # WRITE TECHNICIAN_TOP_ISSUES
    # ==========================================================================

    by_tech: dict[str, list[dict]] = {}
    for entry in nesplneno_by_tech_pos.values():
        by_tech.setdefault(entry["technician"], []).append({"posId": entry["posId"], "count": entry["count"]})

    issue_rows: list[list] = []
    for tech, entries in by_tech.items():
        sorted_entries = sorted(entries, key=lambda e: (-e["count"], e["posId"]))
        top5 = sorted_entries[:5]
        for i, entry in enumerate(top5):
            issue_rows.append([
                tech, i + 1, entry["posId"], pos_name.get(entry["posId"], ""), pos_area.get(entry["posId"], ""), entry["count"],
            ])

    issue_header_row = ["technician", "rank", "posId", "posName", "region", "nesplnenoCount"]
    issue_ws = workbook.get_worksheet("TECHNICIAN_TOP_ISSUES")
    issue_ws.get_range("A2:F100000").clear()
    issue_ws.get_range_by_indexes(0, 0, 1, len(issue_header_row)).set_values([issue_header_row])
    if issue_rows:
        issue_ws.get_range_by_indexes(1, 0, len(issue_rows), len(issue_header_row)).set_values(issue_rows)

    return (
        f"Performance Engine: {len(out_rows)} technician/week rows written to TECHNICIAN_PERFORMANCE_LOG "
        f"(from {len(deduped_rows)} deduped compliance evaluations, {max(len(compliance_log) - 1, 0)} raw rows before dedup, "
        f"{len(tracking_started_raw_weeks)} week(s) with tracking started), "
        f"{len(summary_rows)} rows written to TECHNICIAN_PERFORMANCE_SUMMARY, "
        f"{len(issue_rows)} rows written to TECHNICIAN_TOP_ISSUES, "
        f"{max(len(other_visit_log) - 1, 0)} other-purpose visits aggregated from OTHER_VISIT_LOG."
    )
