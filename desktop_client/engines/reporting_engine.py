"""
Python port of office-scripts/ReportingEngine.ts's main(). See
import_engine.py's module docstring for the duplication rationale. See
ReportingEngine.ts's own file header for the full business-rule rationale
(pure aggregation, chart data blocks, POS_MAP_DATA layout) - this file
intentionally does not re-explain it, only mirrors the logic.
"""
from __future__ import annotations

import datetime

from .core_logic import (
    ComplianceOutcome,
    compute_failure_rate_by_group,
    iso_week_number,
    latest_by_key,
    resolve_capacity,
    weeks_between,
)
from .dates_logic import iso_monday, work_days
from .js_compat import at as _at, js_number as _js_number, num as _num
from .mock_workbook import MockWorkbook


class _LatestComplianceRow:
    def __init__(self, key, timestamp, status, technician, posId, plannedWeek, plannedYear):
        self.key = key
        self.timestamp = timestamp
        self.status = status
        self.technician = technician
        self.posId = posId
        self.plannedWeek = plannedWeek
        self.plannedYear = plannedYear


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
    compliance_log = read_table("COMPLIANCE_LOG")
    advisor_log = read_table("ADVISOR_LOG")

    dash_ws = workbook.get_worksheet("DASHBOARD")
    dash_ws.get_range("A5:F2000").clear()

    # Chart-data-block sizes (see "CHART DATA BLOCKS" section below for the
    # full rationale). Cleared HERE, up front, alongside the A5:F2000 clear
    # above - unlike office-scripts/ReportingEngine.ts (where real Excel's
    # per-range .clear(ClearApplyTo.contents) only ever touches the exact
    # cells named, so interleaving clear()s and writes anywhere is safe),
    # this port's MockWorkbook/xlsx_engine_io execution path shares a
    # deliberately simplified MockRange.clear() (see mock_workbook.py) that
    # truncates every row from the clear's start row onward, in ALL columns
    # - correct only when nothing has been written yet at those rows. Doing
    # every clear() before every write (rather than interleaved, as the .ts
    # source does) sidesteps that simplification instead of requiring a
    # column-aware rewrite of the shared mock.
    WEEKLY_CHART_ROWS = 12
    WORKLOAD_CHART_ROWS = 14
    REGIONAL_CHART_ROWS = 12
    dash_ws.get_range_by_indexes(2, 7, WEEKLY_CHART_ROWS, 4).clear()
    dash_ws.get_range_by_indexes(18, 7, WORKLOAD_CHART_ROWS, 4).clear()
    dash_ws.get_range_by_indexes(36, 7, REGIONAL_CHART_ROWS, 2).clear()

    kpi_active_pos = 0
    kpi_splneno_vcas = 0
    kpi_nesplneno = 0
    kpi_open_alerts = 0

    chart_weekly: list[dict] = []
    chart_workload: list[dict] = []
    chart_regional: list[dict] = []

    output: list[list] = []

    def section(title: str) -> None:
        output.append([title, "", "", "", "", ""])

    def row(*cells) -> None:
        cells = list(cells)
        while len(cells) < 6:
            cells.append("")
        output.append(cells)

    def blank() -> None:
        output.append(["", "", "", "", "", ""])

    # ==========================================================================
    # NETWORK OVERVIEW
    # ==========================================================================

    section("NETWORK OVERVIEW")
    if len(pos_master) >= 2:
        m_headers = [str(h) for h in pos_master[0]]

        def midx(name: str) -> int:
            return m_headers.index(name) if name in m_headers else -1

        active = 0
        closed = 0
        by_market: dict[str, int] = {}
        for i in range(1, len(pos_master)):
            r = pos_master[i]
            if not _at(r, midx("posId")):
                continue
            if str(_at(r, midx("status"))) == "Active":
                active += 1
                market = str(_at(r, midx("market")))
                by_market[market] = by_market.get(market, 0) + 1
            else:
                closed += 1
        kpi_active_pos = active
        row("Active POS", active)
        row("Closed POS", closed)
        for market in sorted(by_market.keys()):
            row(f"  {market}", by_market[market])
    else:
        row("(POS_MASTER is empty - run Import Engine first)")
    blank()

    # ==========================================================================
    # COMPLIANCE SUMMARY
    # ==========================================================================

    section("COMPLIANCE SUMMARY (latest known status per planned visit)")
    latest_compliance: list[_LatestComplianceRow] = []
    if len(compliance_log) >= 2:
        c_headers = [str(h) for h in compliance_log[0]]

        def cidx(name: str) -> int:
            return c_headers.index(name) if name in c_headers else -1

        raw: list[_LatestComplianceRow] = []
        for i in range(1, len(compliance_log)):
            r = compliance_log[i]
            if not _at(r, cidx("posId")):
                continue
            pos_id = str(_at(r, cidx("posId")))
            planned_week = _at(r, cidx("plannedWeek"))
            planned_year = _at(r, cidx("plannedYear"))
            raw.append(_LatestComplianceRow(
                key=f"{pos_id}|{planned_week}|{planned_year}",
                timestamp=str(_at(r, cidx("evaluatedAt"))),
                status=str(_at(r, cidx("status"))),
                technician=str(_at(r, cidx("technician"))),
                posId=pos_id,
                plannedWeek=int(_num(planned_week)),
                plannedYear=int(_num(planned_year)),
            ))
        latest_compliance = latest_by_key(raw)
        counts: dict[str, int] = {}
        for c in latest_compliance:
            counts[c.status] = counts.get(c.status, 0) + 1
        for status in ("Splneno_vcas", "Splneno_pozde", "Nesplneno", "Pending", "Navic_evidovano"):
            row(status, counts.get(status, 0))
        kpi_splneno_vcas = counts.get("Splneno_vcas", 0)
        kpi_nesplneno = counts.get("Nesplneno", 0)
    else:
        row("(COMPLIANCE_LOG is empty - run Compliance Engine after a SalesApp import)")
    blank()

    # ==========================================================================
    # TECHNICIAN KPI
    # ==========================================================================

    section("TECHNICIAN KPI (completion rate excludes Pending - not yet due)")
    row("Technician", "Splneno_vcas", "Splneno_pozde", "Nesplneno", "Completion %")
    if latest_compliance:
        by_tech: dict[str, dict[str, int]] = {}
        for c in latest_compliance:
            if not c.technician:
                continue
            t = by_tech.setdefault(c.technician, {"vcas": 0, "pozde": 0, "nesplneno": 0})
            if c.status == "Splneno_vcas":
                t["vcas"] += 1
            if c.status == "Splneno_pozde":
                t["pozde"] += 1
            if c.status == "Nesplneno":
                t["nesplneno"] += 1
        for tech in sorted(by_tech.keys()):
            t = by_tech[tech]
            denom = t["vcas"] + t["pozde"] + t["nesplneno"]
            rate = round(((t["vcas"] + t["pozde"]) / denom) * 1000) / 10 if denom > 0 else 0
            row(tech, t["vcas"], t["pozde"], t["nesplneno"], rate)
    blank()

    # ==========================================================================
    # REGIONAL OVERVIEW
    # ==========================================================================

    section("REGIONAL OVERVIEW (completion rate by market)")
    row("Market", "Total evaluated", "Nesplneno", "Completion %")
    if len(pos_master) >= 2 and latest_compliance:
        m_headers = [str(h) for h in pos_master[0]]

        def midx(name: str) -> int:
            return m_headers.index(name) if name in m_headers else -1

        market_by_pos: dict[str, str] = {}
        for i in range(1, len(pos_master)):
            r = pos_master[i]
            if _at(r, midx("posId")):
                market_by_pos[str(_at(r, midx("posId")))] = str(_at(r, midx("market")))
        regional_outcomes = [
            ComplianceOutcome(group=market_by_pos.get(c.posId, ""), status=c.status)
            for c in latest_compliance if c.status != "Pending"
        ]
        regional_rates = compute_failure_rate_by_group(regional_outcomes, ["Nesplneno"])
        for r in sorted(regional_rates, key=lambda x: x.group):
            completion_percent = round((1 - r.rate) * 1000) / 10
            row(r.group, r.total, r.failed, completion_percent)
            chart_regional.append({"market": r.group, "completionPercent": completion_percent})
    blank()

    # ==========================================================================
    # WEEKLY TREND
    # ==========================================================================

    section("WEEKLY TREND (podle plánovaného týdne kampaně, ne kalendářního)")
    row("Week", "Splneno_vcas", "Splneno_pozde", "Nesplneno", "Completion %")
    if latest_compliance:
        by_week: dict[str, dict] = {}
        for c in latest_compliance:
            if c.status == "Pending":
                continue
            key = f"{c.plannedYear}|{c.plannedWeek}"
            w = by_week.setdefault(key, {"week": c.plannedWeek, "year": c.plannedYear, "vcas": 0, "pozde": 0, "nesplneno": 0})
            if c.status == "Splneno_vcas":
                w["vcas"] += 1
            if c.status == "Splneno_pozde":
                w["pozde"] += 1
            if c.status == "Nesplneno":
                w["nesplneno"] += 1
        week_keys = sorted(by_week.keys(), key=lambda k: (by_week[k]["year"], by_week[k]["week"]))
        for key in week_keys:
            w = by_week[key]
            denom = w["vcas"] + w["pozde"] + w["nesplneno"]
            rate = round(((w["vcas"] + w["pozde"]) / denom) * 1000) / 10 if denom > 0 else 0
            row(f"{w['year']} / {w['week']}", w["vcas"], w["pozde"], w["nesplneno"], rate)
            chart_weekly.append({"label": f"{w['year']}/{w['week']}", "vcas": w["vcas"], "pozde": w["pozde"], "nesplneno": w["nesplneno"]})
    blank()

    # ==========================================================================
    # TECHNICIAN WORKLOAD
    # ==========================================================================

    section("TECHNICIAN WORKLOAD (nejnovější kalendářní týden v MANAGER_PLAN)")
    row("Technician", "Planned visits", "Capacity", "Utilization %")
    manager_plan = read_table("MANAGER_PLAN")
    capacity_override_rows = read_table("CAPACITY_OVERRIDE")
    control_rows = read_table("CONTROL")
    if len(manager_plan) >= 2:
        control_map: dict[str, str] = {}
        for i in range(1, len(control_rows)):
            if control_rows[i][0]:
                control_map[str(control_rows[i][0])] = str(control_rows[i][1])

        raw_target_day = control_map.get("TARGET_VISITS_DAY", "")
        target_visits_day = _js_number(raw_target_day) if raw_target_day != "" else 8
        raw_target_week = control_map.get("TARGET_VISITS_WEEK", "")
        target_visits_week_raw = _js_number(raw_target_week) if raw_target_week != "" else float("nan")
        target_visits_week = None if target_visits_week_raw != target_visits_week_raw else target_visits_week_raw

        capacity_override_map: dict[str, float] = {}
        for i in range(1, len(capacity_override_rows)):
            r = capacity_override_rows[i]
            if r[0]:
                capacity_override_map[f"{r[0]}|{r[1]}|{r[2]}"] = float(r[3])

        latest_week = {"week": 0, "year": 0}
        visits_by_tech_week: dict[str, dict] = {}
        for i in range(1, len(manager_plan)):
            r = manager_plan[i]
            date_val = r[1] if len(r) > 1 else None
            tech = str(r[3]) if len(r) > 3 and r[3] else ""
            date = _to_date(date_val)
            if not tech or date is None:
                continue
            week, year = iso_week_number(date)
            if year > latest_week["year"] or (year == latest_week["year"] and week > latest_week["week"]):
                latest_week = {"week": week, "year": year}
            key = f"{tech}|{year}|{week}"
            v = visits_by_tech_week.setdefault(key, {"week": week, "year": year, "tech": tech, "count": 0})
            v["count"] += 1

        if latest_week["week"] > 0:
            days = len(work_days(latest_week["year"], latest_week["week"]))
            for key in sorted(visits_by_tech_week.keys()):
                v = visits_by_tech_week[key]
                if v["week"] != latest_week["week"] or v["year"] != latest_week["year"]:
                    continue
                capacity = resolve_capacity(capacity_override_map, v["tech"], v["year"], v["week"], days, target_visits_day, target_visits_week)
                utilization = round((v["count"] / capacity) * 1000) / 10 if capacity > 0 else 0
                row(v["tech"], v["count"], capacity, utilization)
                chart_workload.append({"tech": v["tech"], "planned": v["count"], "capacity": capacity, "utilization": utilization})
    else:
        row("(MANAGER_PLAN is empty - run Planning Engine first)")
    blank()

    # ==========================================================================
    # PLANNING READINESS
    # ==========================================================================

    section("PLANNING READINESS (signály, ne doporučení)")
    plan_lifecycle = read_table("PLAN_LIFECYCLE")
    if len(plan_lifecycle) >= 2 or len(manager_plan) >= 2:
        control_map_for_year: dict[str, str] = {}
        for i in range(1, len(control_rows)):
            if control_rows[i][0]:
                control_map_for_year[str(control_rows[i][0])] = str(control_rows[i][1])
        project_year = int(float(control_map_for_year["YEAR"])) if "YEAR" in control_map_for_year else datetime.date.today().year

        latest_committed = {"week": 0, "year": 0}
        for i in range(1, len(plan_lifecycle)):
            r = plan_lifecycle[i]
            status = str(r[2]) if len(r) > 2 else ""
            if status not in ("Published", "Active"):
                continue
            week = int(_num(r[1])) if len(r) > 1 else 0
            year = int(_num(r[0])) if len(r) > 0 else 0
            if year > latest_committed["year"] or (year == latest_committed["year"] and week > latest_committed["week"]):
                latest_committed = {"week": week, "year": year}

        latest_draft = {"week": 0, "year": project_year}
        for i in range(1, len(manager_plan)):
            week_val = manager_plan[i][0] if len(manager_plan[i]) > 0 else None
            week_num = _num(week_val)
            if week_num == week_num and week_num > latest_draft["week"]:
                latest_draft = {"week": int(week_num), "year": project_year}

        if latest_committed["week"] > 0:
            end_of_week = iso_monday(latest_committed["year"], latest_committed["week"]) + datetime.timedelta(days=6)
            today = datetime.date.today()
            days_remaining = (end_of_week - today).days
            row("Poslední publikovaný/aktivní týden", f"{latest_committed['year']} / {latest_committed['week']}")
            row("Konec tohoto týdne", end_of_week.isoformat())
            row("Dní do konce publikovaného plánu", days_remaining)
        else:
            row("(zatím žádný publikovaný týden - spusť Publish Engine)")

        if latest_draft["week"] > 0:
            row("Poslední naplánovaný (Draft) týden", f"{latest_draft['year']} / {latest_draft['week']}")
            if latest_committed["week"] > 0:
                row(
                    "Draft runway (kolik týdnů dopředu je Draft nad rámec publikovaného)",
                    weeks_between(latest_committed["week"], latest_committed["year"], latest_draft["week"], latest_draft["year"]),
                )
    else:
        row("(PLAN_LIFECYCLE i MANAGER_PLAN jsou prázdné - spusť Planning Engine)")
    blank()

    # ==========================================================================
    # ADVISOR SUMMARY
    # ==========================================================================

    section("ADVISOR ALERTS (most recent Advisor Engine run)")
    if len(advisor_log) >= 2:
        a_headers = [str(h) for h in advisor_log[0]]

        def aidx(name: str) -> int:
            return a_headers.index(name) if name in a_headers else -1

        latest_run = ""
        for i in range(1, len(advisor_log)):
            ts = str(advisor_log[i][aidx("evaluatedAt")])
            if ts > latest_run:
                latest_run = ts
        counts: dict[str, int] = {}
        for i in range(1, len(advisor_log)):
            r = advisor_log[i]
            if str(_at(r, aidx("evaluatedAt"))) != latest_run:
                continue
            key = f"{_at(r, aidx('type'))} ({_at(r, aidx('severity'))})"
            counts[key] = counts.get(key, 0) + 1
        if not counts:
            row("(no alerts in the most recent run)")
        for key in sorted(counts.keys()):
            row(key, counts[key])
            kpi_open_alerts += counts[key]
    else:
        row("(no alerts on record - run Advisor Engine if you have not yet)")

    # ==========================================================================
    # WRITE DASHBOARD
    # ==========================================================================

    dash_ws.get_range_by_indexes(2, 1, 1, 4).set_values([[kpi_active_pos, kpi_splneno_vcas, kpi_nesplneno, kpi_open_alerts]])
    if output:
        dash_ws.get_range_by_indexes(4, 0, len(output), 6).set_values(output)

    # ==========================================================================
    # CHART DATA BLOCKS
    # ==========================================================================

    weekly_chart_rows = chart_weekly[-WEEKLY_CHART_ROWS:]
    if weekly_chart_rows:
        dash_ws.get_range_by_indexes(2, 7, len(weekly_chart_rows), 4).set_values(
            [[w["label"], w["vcas"], w["pozde"], w["nesplneno"]] for w in weekly_chart_rows]
        )

    workload_chart_rows = chart_workload[:WORKLOAD_CHART_ROWS]
    if workload_chart_rows:
        dash_ws.get_range_by_indexes(18, 7, len(workload_chart_rows), 4).set_values(
            [[w["tech"], w["planned"], w["capacity"], w["utilization"]] for w in workload_chart_rows]
        )

    regional_chart_rows = chart_regional[:REGIONAL_CHART_ROWS]
    if regional_chart_rows:
        dash_ws.get_range_by_indexes(36, 7, len(regional_chart_rows), 2).set_values(
            [[r["market"], r["completionPercent"]] for r in regional_chart_rows]
        )

    # ==========================================================================
    # WRITE POS_MAP_DATA
    # ==========================================================================

    MAX_MAP_TECHS = 40
    MAX_POS_PER_TECH = 700

    m_headers_for_map = [str(h) for h in pos_master[0]] if pos_master else []

    def midx_for_map(name: str) -> int:
        return m_headers_for_map.index(name) if name in m_headers_for_map else -1

    pos_by_tech_for_map: dict[str, list[tuple[float, float]]] = {}
    for i in range(1, len(pos_master)):
        r = pos_master[i]
        if str(_at(r, midx_for_map("status"))) != "Active":
            continue
        lat = _num(_at(r, midx_for_map("gpsX")))
        lon = _num(_at(r, midx_for_map("gpsY")))
        if lat == 0 and lon == 0:
            continue
        override = str(_at(r, midx_for_map("managerOverrideTechnician")) or "")
        tech = override or str(_at(r, midx_for_map("assignedTechnician")) or "")
        if not tech:
            continue
        pos_by_tech_for_map.setdefault(tech, []).append((lon, lat))

    all_map_techs = sorted(pos_by_tech_for_map.keys())
    map_techs = all_map_techs[:MAX_MAP_TECHS]

    map_ws = workbook.get_worksheet("POS_MAP_DATA")
    map_ws.get_range_by_indexes(0, 0, 1 + MAX_POS_PER_TECH, MAX_MAP_TECHS * 2).clear()
    for slot, tech in enumerate(map_techs):
        points = pos_by_tech_for_map[tech][:MAX_POS_PER_TECH]
        map_ws.get_range_by_indexes(0, slot * 2, 1, 1).set_value(tech)
        if points:
            map_ws.get_range_by_indexes(1, slot * 2, len(points), 2).set_values([[p[0], p[1]] for p in points])

    beyond_cap = (
        f", {len(all_map_techs) - MAX_MAP_TECHS} technician(s) beyond the {MAX_MAP_TECHS}-slot cap not shown"
        if len(all_map_techs) > MAX_MAP_TECHS else ""
    )
    return (
        f"Reporting Engine: dashboard refreshed, {len(output)} detail rows + 4 KPI tiles + 3 chart data blocks written. "
        f"POS_MAP_DATA refreshed ({len(map_techs)} technician territories{beyond_cap})."
    )
