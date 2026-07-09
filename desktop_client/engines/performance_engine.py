"""
Python port of office-scripts/PerformanceEngine.ts's main(). See
import_engine.py's module docstring for the duplication rationale. See
PerformanceEngine.ts's own file header for the full business-rule rationale
(tracking gate, route-km estimate, flaka riziko, monthKey, etc.) - this file
intentionally does not re-explain it, only mirrors the logic.
"""
from __future__ import annotations

import datetime
import re

from .core_logic import GeoPoint, compute_optimal_route_km, distance_km, iso_now, iso_week_number, latest_by_key, norm
from .dates_logic import iso_monday
from .js_compat import at as _at, num as _num
from .mock_workbook import MockWorkbook


class _ComplianceRow:
    def __init__(self, key, timestamp, posId, technician, week, year, status, matchedActualDate,
                 matchedActualDurationHours=None, matchedActualStartedAt=None, matchedActualFinishedAt=None):
        self.key = key
        self.timestamp = timestamp
        self.posId = posId
        self.technician = technician
        self.week = week
        self.year = year
        self.status = status
        self.matchedActualDate = matchedActualDate
        self.matchedActualDurationHours = matchedActualDurationHours
        self.matchedActualStartedAt = matchedActualStartedAt
        self.matchedActualFinishedAt = matchedActualFinishedAt


class Bucket:
    def __init__(self, technician: str, year: int, week: int):
        self.technician = technician
        self.year = year
        self.week = week
        self.areaCounts: dict[str, int] = {}
        self.strediskoCounts: dict[str, int] = {}
        self.plannedVisits = 0
        self.realizedVisits = 0
        self.splnenoVcas = 0
        self.splnenoPozde = 0
        self.nesplneno = 0
        self.navicEvidovano = 0
        self.otherVisits = 0
        self.visitsByDay = [0, 0, 0, 0, 0]
        self.otherVisitsByDay = [0, 0, 0, 0, 0]
        self.realizedPptSum = 0.0
        self.durationHoursSum = 0.0
        self.durationKnownCount = 0
        # Real work-day span/idle time (product owner, 2026-07-11) - see
        # office-scripts/PerformanceEngine.ts's identical comment.
        self.dayFirstStart: list = [None, None, None, None, None]
        self.dayLastFinish: list = [None, None, None, None, None]
        self.dayActiveHoursSum = [0.0, 0.0, 0.0, 0.0, 0.0]
        self.possByDay: list[list[str]] = [[], [], [], [], []]


def _to_date(v) -> datetime.date | None:
    if isinstance(v, datetime.datetime):
        return v.date()
    if isinstance(v, datetime.date):
        return v
    try:
        return datetime.date.fromisoformat(str(v)[:10])
    except (ValueError, TypeError):
        return None


_CZ_DATE_RE = re.compile(r"^(\d{1,2})\.\s*(\d{1,2})\.\s*(\d{4})$")


def _parse_plan_date(v) -> datetime.date | None:
    """Parses MANAGER_PLAN_PUBLISHED's DATE column specifically - written by
    PlanningEngine.ts as a Czech-locale STRING (toLocaleDateString("cs-CZ"),
    "D. M. YYYY"), not a real Date. _to_date()'s ISO-only fromisoformat()
    fails silently on this format, which zeroed out plannedVisits/region for
    every published plan (found 2026-07-11, same bug/fix as
    ComplianceEngine.ts/compliance_engine.py's _parse_plan_date - see
    docs/BUSINESS_RULES.md section 24)."""
    if isinstance(v, datetime.datetime):
        return v.date()
    if isinstance(v, datetime.date):
        return v
    if isinstance(v, str) and v.strip():
        m = _CZ_DATE_RE.match(v.strip())
        if m:
            day, month, year = int(m.group(1)), int(m.group(2)), int(m.group(3))
            try:
                return datetime.date(year, month, day)
            except ValueError:
                return None
        try:
            return datetime.date.fromisoformat(v.strip()[:10])
        except ValueError:
            return None
    return None


def _parse_cell_datetime(v) -> datetime.datetime | None:
    """Port of office-scripts/PerformanceEngine.ts's parseCellDate() -
    preserves time-of-day, unlike _to_date()."""
    if isinstance(v, datetime.datetime):
        return v
    if isinstance(v, datetime.date):
        return datetime.datetime(v.year, v.month, v.day)
    if not v:
        return None
    try:
        return datetime.datetime.fromisoformat(str(v))
    except ValueError:
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
    # Monitoring efektivity (product owner, 2026-07-09) - see
    # office-scripts/PerformanceEngine.ts's identical comment.
    route_efficiency_warning_percent = setting("ROUTE_EFFICIENCY_WARNING_PERCENT", 125)
    route_efficiency_critical_percent = setting("ROUTE_EFFICIENCY_CRITICAL_PERCENT", 150)
    # "Manazerske" triggery (product owner, 2026-07-09) - see
    # office-scripts/PerformanceEngine.ts's identical comment.
    volume_warning_percent = setting("VOLUME_WARNING_PERCENT", 70)
    volume_critical_percent = setting("VOLUME_CRITICAL_PERCENT", 50)
    ppt_density_warning_percent = setting("PPT_DENSITY_WARNING_PERCENT", 70)
    ppt_density_critical_percent = setting("PPT_DENSITY_CRITICAL_PERCENT", 50)
    duration_warning_percent = setting("DURATION_WARNING_PERCENT", 70)
    duration_critical_percent = setting("DURATION_CRITICAL_PERCENT", 50)
    problem_signal_min_count = setting("PROBLEM_SIGNAL_MIN_COUNT", 2)

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
    # Středisko (RSA/RSC/RSE...) - POS_MASTER.posArea, distinct from the
    # district-name "area" column above (product owner, 2026-07-11: "do
    # filtrů dej podle střediska (typicky to tam máš jako oblast) RSC, RSA
    # apod."). See office-scripts/PerformanceEngine.ts's identical comment.
    pos_stredisko: dict[str, str] = {}
    pos_technician: dict[str, str] = {}
    pos_name: dict[str, str] = {}
    pos_gps: dict[str, tuple[float, float]] = {}
    # PPT lookup (product owner, 2026-07-09) - see
    # office-scripts/PerformanceEngine.ts's identical comment.
    pos_ppt: dict[str, float] = {}
    for i in range(1, len(pos_master)):
        row = pos_master[i]
        pos_id = str(_at(row, pm_idx("posId")))
        if not pos_id:
            continue
        pos_area[pos_id] = str(_at(row, pm_idx("area")) or "")
        pos_stredisko[pos_id] = str(_at(row, pm_idx("posArea")) or "")
        pos_name[pos_id] = str(_at(row, pm_idx("nazev")) or "")
        override = str(_at(row, pm_idx("managerOverrideTechnician")) or "")
        pos_technician[pos_id] = override or str(_at(row, pm_idx("assignedTechnician")) or "")
        pos_ppt[pos_id] = _num(_at(row, pm_idx("ppt"))) or 0
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

    def record_day_timing(bucket: Bucket, day_idx: int, started_at, finished_at, duration_hours) -> None:
        """Port of office-scripts/PerformanceEngine.ts's recordDayTiming()."""
        if started_at and (bucket.dayFirstStart[day_idx] is None or started_at < bucket.dayFirstStart[day_idx]):
            bucket.dayFirstStart[day_idx] = started_at
        if finished_at and (bucket.dayLastFinish[day_idx] is None or finished_at > bucket.dayLastFinish[day_idx]):
            bucket.dayLastFinish[day_idx] = finished_at
        if duration_hours is not None:
            bucket.dayActiveHoursSum[day_idx] += duration_hours

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
        date = _parse_plan_date(date_val)
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
        stredisko = pos_stredisko.get(pos_id, "")
        if stredisko:
            bucket.strediskoCounts[stredisko] = bucket.strediskoCounts.get(stredisko, 0) + 1

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
        duration_raw = _num(_at(row, cl_idx("matchedActualDurationHours")))
        raw_rows.append(_ComplianceRow(
            key=f"{pos_id}|{week}|{year}",
            timestamp=str(_at(row, cl_idx("evaluatedAt"))),
            posId=pos_id,
            technician=str(_at(row, cl_idx("technician")) or ""),
            week=week, year=year,
            status=str(_at(row, cl_idx("status"))),
            matchedActualDate=_to_date(date_val),
            matchedActualDurationHours=duration_raw if duration_raw == duration_raw and duration_raw > 0 else None,
            matchedActualStartedAt=_parse_cell_datetime(_at(row, cl_idx("matchedActualStartedAt"))),
            matchedActualFinishedAt=_parse_cell_datetime(_at(row, cl_idx("matchedActualFinishedAt"))),
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
            bucket.realizedPptSum += pos_ppt.get(r.posId, 0)
        elif r.status == "Splneno_pozde":
            bucket.splnenoPozde += 1
            bucket.realizedVisits += 1
            bucket.realizedPptSum += pos_ppt.get(r.posId, 0)
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
                record_day_timing(
                    bucket, day_index[weekday], r.matchedActualStartedAt, r.matchedActualFinishedAt, r.matchedActualDurationHours
                )
            if r.matchedActualDurationHours is not None:
                bucket.durationHoursSum += r.matchedActualDurationHours
                bucket.durationKnownCount += 1

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
        bucket = bucket_for(tech, year, week)
        bucket.otherVisits += 1
        # Daily breakdown (product owner, 2026-07-09) - see
        # office-scripts/PerformanceEngine.ts's identical comment.
        ov_date = _to_date(_at(row, ov_idx("date")))
        if ov_date is not None:
            weekday = ov_date.weekday()  # Mon=0..Sun=6
            if weekday in day_index:
                bucket.otherVisitsByDay[day_index[weekday]] += 1
                ov_duration_raw = _num(_at(row, ov_idx("durationHours")))
                ov_duration = ov_duration_raw if ov_duration_raw == ov_duration_raw and ov_duration_raw > 0 else None
                record_day_timing(
                    bucket, day_index[weekday],
                    _parse_cell_datetime(_at(row, ov_idx("startedAt"))), _parse_cell_datetime(_at(row, ov_idx("finishedAt"))),
                    ov_duration,
                )

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

    def optimal_route_km_for_day(pos_ids: list[str]) -> float:
        """Monitoring efektivity (product owner, 2026-07-09) - see
        office-scripts/PerformanceEngine.ts's identical comment."""
        points = []
        for pos_id in dict.fromkeys(pos_ids):
            gps = pos_gps.get(pos_id)
            if gps is not None:
                points.append(GeoPoint(gps[0], gps[1]))
        return compute_optimal_route_km(points)

    # ==========================================================================
    # NETWORK PEER AVERAGES (product owner, 2026-07-09) - see
    # office-scripts/PerformanceEngine.ts's identical comment.
    # ==========================================================================

    peer_stats_by_week: dict[str, dict] = {}
    for b in buckets.values():
        week_key = f"{b.year}|{b.week}"
        stats = peer_stats_by_week.setdefault(week_key, {
            "visitsSum": 0, "visitsCount": 0, "pptPerVisitSum": 0.0, "pptPerVisitCount": 0,
            "durationSum": 0.0, "durationCount": 0,
        })
        stats["visitsSum"] += b.realizedVisits
        stats["visitsCount"] += 1
        if b.realizedVisits > 0:
            stats["pptPerVisitSum"] += b.realizedPptSum / b.realizedVisits
            stats["pptPerVisitCount"] += 1
        if b.durationKnownCount > 0:
            stats["durationSum"] += b.durationHoursSum / b.durationKnownCount
            stats["durationCount"] += 1

    def network_avg_visits(year: int, week: int) -> float | None:
        s = peer_stats_by_week.get(f"{year}|{week}")
        return s["visitsSum"] / s["visitsCount"] if s and s["visitsCount"] > 0 else None

    def network_avg_ppt_per_visit(year: int, week: int) -> float | None:
        s = peer_stats_by_week.get(f"{year}|{week}")
        return s["pptPerVisitSum"] / s["pptPerVisitCount"] if s and s["pptPerVisitCount"] > 0 else None

    def network_avg_duration(year: int, week: int) -> float | None:
        s = peer_stats_by_week.get(f"{year}|{week}")
        return s["durationSum"] / s["durationCount"] if s and s["durationCount"] > 0 else None

    def vs_peer_percent(value: float | None, peer_avg: float | None) -> int | None:
        return round((value / peer_avg) * 100) if value is not None and peer_avg is not None and peer_avg > 0 else None

    def low_flag(percent: float | None, warning_percent: float, critical_percent: float) -> str:
        if percent is None:
            return ""
        if percent < critical_percent:
            return "KRITICKÉ"
        if percent < warning_percent:
            return "POZOR"
        return "OK"

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
        top_stredisko = ""
        top_stredisko_count = 0
        for stredisko, cnt in b.strediskoCounts.items():
            if cnt > top_stredisko_count:
                top_stredisko = stredisko
                top_stredisko_count = cnt
        compliance_percent = round((b.realizedVisits / b.plannedVisits) * 1000) / 10 if b.plannedVisits > 0 else 0
        km_by_day = [route_km_for_day(b.technician, b.year, b.week, i, pos_ids) for i, pos_ids in enumerate(b.possByDay)]
        optimal_km_by_day = [optimal_route_km_for_day(pos_ids) for pos_ids in b.possByDay]
        total_actual_km = 0.0
        total_optimal_km = 0.0
        for d in range(5):
            if km_by_day[d] > 0 and optimal_km_by_day[d] > 0:
                total_actual_km += km_by_day[d]
                total_optimal_km += optimal_km_by_day[d]
        total_actual_km = round(total_actual_km * 10) / 10
        total_optimal_km = round(total_optimal_km * 10) / 10
        efficiency_ratio_percent = round((total_actual_km / total_optimal_km) * 100) if total_optimal_km > 0 else None
        km_per_visit = round((total_actual_km / b.realizedVisits) * 10) / 10 if b.realizedVisits > 0 else None
        if efficiency_ratio_percent is None:
            efficiency_flag = ""
        elif efficiency_ratio_percent >= route_efficiency_critical_percent:
            efficiency_flag = "KRITICKÉ"
        elif efficiency_ratio_percent >= route_efficiency_warning_percent:
            efficiency_flag = "POZOR"
        else:
            efficiency_flag = "OK"
        pos_list_by_day = [
            ", ".join(
                pid + (f" - {pos_name[pid]}" if pos_name.get(pid) else "")
                for pid in ordered_pos_for_day(b.technician, b.year, b.week, i, pos_ids)
            )
            for i, pos_ids in enumerate(b.possByDay)
        ]
        month_date = iso_monday(b.year, b.week)
        month_key = month_date.year * 100 + month_date.month

        # "MANAZERSKE" TRIGGERY (product owner, 2026-07-09) - see
        # office-scripts/PerformanceEngine.ts's identical comment.
        ppt_per_visit = round((b.realizedPptSum / b.realizedVisits) * 100) / 100 if b.realizedVisits > 0 else None
        avg_visit_duration_hours = (
            round((b.durationHoursSum / b.durationKnownCount) * 100) / 100 if b.durationKnownCount > 0 else None
        )
        volume_vs_peer_percent = vs_peer_percent(b.realizedVisits, network_avg_visits(b.year, b.week))
        ppt_density_vs_peer_percent = vs_peer_percent(ppt_per_visit, network_avg_ppt_per_visit(b.year, b.week))
        duration_vs_peer_percent = vs_peer_percent(avg_visit_duration_hours, network_avg_duration(b.year, b.week))
        volume_flag = low_flag(volume_vs_peer_percent, volume_warning_percent, volume_critical_percent)
        ppt_density_flag = low_flag(ppt_density_vs_peer_percent, ppt_density_warning_percent, ppt_density_critical_percent)
        duration_flag = low_flag(duration_vs_peer_percent, duration_warning_percent, duration_critical_percent)
        active_signals = sum([
            b.plannedVisits > 0 and compliance_percent < flakani_bad_week_threshold_percent,
            volume_flag in ("POZOR", "KRITICKÉ"),
            ppt_density_flag in ("POZOR", "KRITICKÉ"),
            duration_flag in ("POZOR", "KRITICKÉ"),
            efficiency_flag in ("POZOR", "KRITICKÉ"),
        ])
        combined_risk_flag = "Ano" if active_signals >= problem_signal_min_count else "Ne"

        # Skutečný pracovní den (product owner, 2026-07-11) - see
        # office-scripts/PerformanceEngine.ts's identical comment.
        work_span_hours_by_day: list = []
        idle_hours_by_day: list = []
        for d in range(5):
            start = b.dayFirstStart[d]
            finish = b.dayLastFinish[d]
            if not start or not finish or finish <= start:
                work_span_hours_by_day.append(None)
                idle_hours_by_day.append(None)
                continue
            span = round(((finish - start).total_seconds() / 3600) * 100) / 100
            work_span_hours_by_day.append(span)
            idle_hours_by_day.append(max(0, round((span - b.dayActiveHoursSum[d]) * 100) / 100))

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
            b.otherVisitsByDay[0], b.otherVisitsByDay[1], b.otherVisitsByDay[2], b.otherVisitsByDay[3], b.otherVisitsByDay[4],
            total_actual_km, total_optimal_km, efficiency_ratio_percent if efficiency_ratio_percent is not None else "",
            km_per_visit if km_per_visit is not None else "", efficiency_flag,
            ppt_per_visit if ppt_per_visit is not None else "",
            avg_visit_duration_hours if avg_visit_duration_hours is not None else "",
            volume_vs_peer_percent if volume_vs_peer_percent is not None else "",
            ppt_density_vs_peer_percent if ppt_density_vs_peer_percent is not None else "",
            duration_vs_peer_percent if duration_vs_peer_percent is not None else "",
            volume_flag, ppt_density_flag, duration_flag, active_signals, combined_risk_flag,
            work_span_hours_by_day[0] if work_span_hours_by_day[0] is not None else "",
            work_span_hours_by_day[1] if work_span_hours_by_day[1] is not None else "",
            work_span_hours_by_day[2] if work_span_hours_by_day[2] is not None else "",
            work_span_hours_by_day[3] if work_span_hours_by_day[3] is not None else "",
            work_span_hours_by_day[4] if work_span_hours_by_day[4] is not None else "",
            idle_hours_by_day[0] if idle_hours_by_day[0] is not None else "",
            idle_hours_by_day[1] if idle_hours_by_day[1] is not None else "",
            idle_hours_by_day[2] if idle_hours_by_day[2] is not None else "",
            idle_hours_by_day[3] if idle_hours_by_day[3] is not None else "",
            idle_hours_by_day[4] if idle_hours_by_day[4] is not None else "",
            top_stredisko,
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
        "otherVisitsMon", "otherVisitsTue", "otherVisitsWed", "otherVisitsThu", "otherVisitsFri",
        "totalActualKmWeek", "totalOptimalKmWeek", "efficiencyRatioPercent", "kmPerVisit", "efficiencyFlag",
        "pptPerVisit", "avgVisitDurationHours",
        "volumeVsPeerPercent", "pptDensityVsPeerPercent", "durationVsPeerPercent",
        "volumeFlag", "pptDensityFlag", "durationFlag", "activeSignalCount", "combinedRiskFlag",
        "workSpanHoursMon", "workSpanHoursTue", "workSpanHoursWed", "workSpanHoursThu", "workSpanHoursFri",
        "idleHoursMon", "idleHoursTue", "idleHoursWed", "idleHoursThu", "idleHoursFri",
        # Středisko (RSA/RSC/RSE...) - appended at the end, product owner
        # 2026-07-11, see docs/BUSINESS_RULES.md section 24.
        "stredisko",
    ]
    out_ws = workbook.get_worksheet("TECHNICIAN_PERFORMANCE_LOG")
    out_ws.get_range("A2:BH100000").clear()
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
        top_stredisko = ""
        top_stredisko_count = 0
        for stredisko, cnt in b.strediskoCounts.items():
            if cnt > top_stredisko_count:
                top_stredisko = stredisko
                top_stredisko_count = cnt
        compliance_percent = round((b.realizedVisits / b.plannedVisits) * 1000) / 10 if b.plannedVisits > 0 else 0
        km_by_day_for_summary = [route_km_for_day(b.technician, b.year, b.week, i, pos_ids) for i, pos_ids in enumerate(b.possByDay)]
        max_km_day = max(km_by_day_for_summary)
        optimal_km_by_day_for_summary = [optimal_route_km_for_day(pos_ids) for pos_ids in b.possByDay]
        summary_actual_km = 0.0
        summary_optimal_km = 0.0
        for d in range(5):
            if km_by_day_for_summary[d] > 0 and optimal_km_by_day_for_summary[d] > 0:
                summary_actual_km += km_by_day_for_summary[d]
                summary_optimal_km += optimal_km_by_day_for_summary[d]
        efficiency_ratio_percent_for_summary = (
            round((summary_actual_km / summary_optimal_km) * 100) if summary_optimal_km > 0 else None
        )
        km_per_visit_for_summary = (
            round((summary_actual_km / b.realizedVisits) * 10) / 10 if b.realizedVisits > 0 else None
        )
        # "MANAZERSKE" TRIGGERY, per-week values for later long-run averaging
        # - see office-scripts/PerformanceEngine.ts's identical comment.
        ppt_per_visit_for_summary = b.realizedPptSum / b.realizedVisits if b.realizedVisits > 0 else None
        avg_visit_duration_hours_for_summary = (
            b.durationHoursSum / b.durationKnownCount if b.durationKnownCount > 0 else None
        )
        volume_vs_peer_percent_for_summary = vs_peer_percent(b.realizedVisits, network_avg_visits(b.year, b.week))
        ppt_density_vs_peer_percent_for_summary = vs_peer_percent(
            ppt_per_visit_for_summary, network_avg_ppt_per_visit(b.year, b.week)
        )
        duration_vs_peer_percent_for_summary = vs_peer_percent(
            avg_visit_duration_hours_for_summary, network_avg_duration(b.year, b.week)
        )
        by_tech_weeks.setdefault(b.technician, []).append({
            "year": b.year, "week": b.week, "region": top_area, "stredisko": top_stredisko,
            "plannedVisits": b.plannedVisits, "realizedVisits": b.realizedVisits,
            "splnenoVcas": b.splnenoVcas, "splnenoPozde": b.splnenoPozde,
            "nesplneno": b.nesplneno, "navicEvidovano": b.navicEvidovano,
            "compliancePercent": compliance_percent, "maxKmDay": max_km_day,
            "efficiencyRatioPercent": efficiency_ratio_percent_for_summary, "kmPerVisit": km_per_visit_for_summary,
            "volumeVsPeerPercent": volume_vs_peer_percent_for_summary,
            "pptDensityVsPeerPercent": ppt_density_vs_peer_percent_for_summary,
            "durationVsPeerPercent": duration_vs_peer_percent_for_summary,
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
        weeks_with_ratio = [w for w in weeks if w["efficiencyRatioPercent"] is not None][:flakani_window_weeks]
        long_run_avg_efficiency_ratio = (
            round(sum(w["efficiencyRatioPercent"] for w in weeks_with_ratio) / len(weeks_with_ratio))
            if weeks_with_ratio else None
        )
        if long_run_avg_efficiency_ratio is None:
            efficiency_flag_for_summary = ""
        elif long_run_avg_efficiency_ratio >= route_efficiency_critical_percent:
            efficiency_flag_for_summary = "KRITICKÉ"
        elif long_run_avg_efficiency_ratio >= route_efficiency_warning_percent:
            efficiency_flag_for_summary = "POZOR"
        else:
            efficiency_flag_for_summary = "OK"

        # "MANAZERSKE" TRIGGERY - sustained (long-run) view, see
        # office-scripts/PerformanceEngine.ts's identical comment.
        weeks_with_volume_ratio = [w for w in weeks if w["volumeVsPeerPercent"] is not None][:flakani_window_weeks]
        long_run_avg_volume_vs_peer_percent = (
            round(sum(w["volumeVsPeerPercent"] for w in weeks_with_volume_ratio) / len(weeks_with_volume_ratio))
            if weeks_with_volume_ratio else None
        )
        prior_weeks_for_own_avg = weeks[1:1 + flakani_window_weeks]
        own_avg_visits = (
            sum(w["realizedVisits"] for w in prior_weeks_for_own_avg) / len(prior_weeks_for_own_avg)
            if prior_weeks_for_own_avg else None
        )
        volume_vs_own_avg_percent = vs_peer_percent(latest["realizedVisits"], own_avg_visits)
        if long_run_avg_volume_vs_peer_percent is not None and volume_vs_own_avg_percent is not None:
            volume_flag_percent_for_flag = min(long_run_avg_volume_vs_peer_percent, volume_vs_own_avg_percent)
        else:
            volume_flag_percent_for_flag = long_run_avg_volume_vs_peer_percent
            if volume_flag_percent_for_flag is None:
                volume_flag_percent_for_flag = volume_vs_own_avg_percent
        volume_flag_for_summary = low_flag(volume_flag_percent_for_flag, volume_warning_percent, volume_critical_percent)

        weeks_with_ppt_density_ratio = [w for w in weeks if w["pptDensityVsPeerPercent"] is not None][:flakani_window_weeks]
        long_run_avg_ppt_density_vs_peer_percent = (
            round(sum(w["pptDensityVsPeerPercent"] for w in weeks_with_ppt_density_ratio) / len(weeks_with_ppt_density_ratio))
            if weeks_with_ppt_density_ratio else None
        )
        ppt_density_flag_for_summary = low_flag(
            long_run_avg_ppt_density_vs_peer_percent, ppt_density_warning_percent, ppt_density_critical_percent
        )

        weeks_with_duration_ratio = [w for w in weeks if w["durationVsPeerPercent"] is not None][:flakani_window_weeks]
        long_run_avg_duration_vs_peer_percent = (
            round(sum(w["durationVsPeerPercent"] for w in weeks_with_duration_ratio) / len(weeks_with_duration_ratio))
            if weeks_with_duration_ratio else None
        )
        duration_flag_for_summary = low_flag(
            long_run_avg_duration_vs_peer_percent, duration_warning_percent, duration_critical_percent
        )

        active_signals_for_summary = sum([
            flaka_riziko == "Ano",
            volume_flag_for_summary in ("POZOR", "KRITICKÉ"),
            ppt_density_flag_for_summary in ("POZOR", "KRITICKÉ"),
            duration_flag_for_summary in ("POZOR", "KRITICKÉ"),
            efficiency_flag_for_summary in ("POZOR", "KRITICKÉ"),
        ])
        combined_risk_flag_for_summary = "Ano" if active_signals_for_summary >= problem_signal_min_count else "Ne"

        summary_rows.append([
            tech, latest["region"], latest["year"], latest["week"],
            latest["plannedVisits"], latest["realizedVisits"],
            latest["splnenoVcas"], latest["splnenoPozde"], latest["nesplneno"], latest["navicEvidovano"],
            latest["compliancePercent"], long_run_avg_compliance, trend_delta,
            bad_weeks_in_window, flaka_riziko, latest["maxKmDay"],
            latest["efficiencyRatioPercent"] if latest["efficiencyRatioPercent"] is not None else "",
            latest["kmPerVisit"] if latest["kmPerVisit"] is not None else "",
            long_run_avg_efficiency_ratio if long_run_avg_efficiency_ratio is not None else "",
            efficiency_flag_for_summary,
            volume_vs_own_avg_percent if volume_vs_own_avg_percent is not None else "",
            long_run_avg_volume_vs_peer_percent if long_run_avg_volume_vs_peer_percent is not None else "",
            volume_flag_for_summary,
            long_run_avg_ppt_density_vs_peer_percent if long_run_avg_ppt_density_vs_peer_percent is not None else "",
            ppt_density_flag_for_summary,
            long_run_avg_duration_vs_peer_percent if long_run_avg_duration_vs_peer_percent is not None else "",
            duration_flag_for_summary,
            active_signals_for_summary, combined_risk_flag_for_summary,
            latest["stredisko"],
        ])

    summary_header_row = [
        "technician", "region", "latestYear", "latestWeek",
        "plannedVisits", "realizedVisits", "splnenoVcas", "splnenoPozde", "nesplneno", "navicEvidovano",
        "compliancePercent", "longRunAvgCompliance", "trendDelta",
        "badWeeksInWindow", "flakaRiziko", "maxKmDay",
        "efficiencyRatioPercent", "kmPerVisit", "longRunAvgEfficiencyRatio", "efficiencyFlag",
        "volumeVsOwnAvgPercent", "longRunAvgVolumeVsPeerPercent", "volumeFlag",
        "longRunAvgPptDensityVsPeerPercent", "pptDensityFlag",
        "longRunAvgDurationVsPeerPercent", "durationFlag",
        "activeSignalCount", "combinedRiskFlag",
        # Středisko (RSA/RSC/RSE...) - appended at the end, product owner
        # 2026-07-11, see docs/BUSINESS_RULES.md section 24.
        "stredisko",
    ]
    summary_ws = workbook.get_worksheet("TECHNICIAN_PERFORMANCE_SUMMARY")
    summary_ws.get_range("A2:AD100000").clear()
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
