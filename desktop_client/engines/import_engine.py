"""
Python port of office-scripts/ImportEngine.ts's main(). Line-for-line
translation - see mock_workbook.py's module docstring for why the class-
based Range API is kept instead of a more Pythonic rewrite, and
docs/ARCHITECTURE.md for why this duplication exists at all and how it is
verified (tools/sim/compare_engines.py).
"""
from __future__ import annotations

import datetime

from .core_logic import norm, iso_now, iso_week_number
from .js_compat import at as _at, s as _s, num as _num
from .mock_workbook import MockWorkbook


def _build_header_index(header_row: list) -> list[str]:
    return [norm(str(x)) for x in header_row]


def _exact_col(headers: list[str], name: str) -> int:
    n = norm(name)
    for i, h in enumerate(headers):
        if h == n:
            return i
    return -1


def _col(headers: list[str], name: str) -> int:
    n = norm(name)
    for i, h in enumerate(headers):
        if n in h:
            return i
    return -1


def run(workbook: MockWorkbook) -> str:
    raw_ws = workbook.get_worksheet("RAW_DATA")
    raw = raw_ws.get_used_range().get_values()

    master_ws = workbook.get_worksheet("POS_MASTER")
    master_range = master_ws.get_used_range()
    master_existing = master_range.get_values() if master_range else []

    headers = _build_header_index(raw[1])

    c_pos = _exact_col(headers, "POS")
    c_tech = _col(headers, "TECH")
    c_term = _exact_col(headers, "TYP TERMINALU")
    c_ptt = _col(headers, "PTT")
    c_kateg = _exact_col(headers, "KATEGORIE")
    c_kategorizace = _exact_col(headers, "KATEGORIZACE")
    c_market = _exact_col(headers, "MARKET")
    c_nazev = _exact_col(headers, "NAZEV PROVOZOVNY")
    c_ulice = _exact_col(headers, "ULICE")
    c_cislo = _exact_col(headers, "CISLO POPISNE/ORIENTACNI")
    c_mesto = _exact_col(headers, "MESTO")
    c_oblast = _exact_col(headers, "OBLAST")
    c_area = _exact_col(headers, "POS AREA")
    c_x = _exact_col(headers, "X")
    c_y = _exact_col(headers, "Y")
    c_termid = _exact_col(headers, "CISLO TERMINALU")

    today_week, today_year = iso_week_number(datetime.date.today())

    master_headers: list[str] = [str(h) for h in master_existing[0]] if master_existing else []

    def m_idx(name: str) -> int:
        return master_headers.index(name) if name in master_headers else -1

    existing_by_pos: dict[str, dict] = {}
    for i in range(1, len(master_existing)):
        row = master_existing[i]
        pos_id = _s(_at(row, m_idx("posId")))
        if not pos_id:
            continue
        closed_week_val = _at(row, m_idx("closedSinceWeek"))
        closed_year_val = _at(row, m_idx("closedSinceYear"))
        existing_by_pos[pos_id] = {
            "managerOverrideType": _s(_at(row, m_idx("managerOverrideType")) or ""),
            "managerOverridePriority": _s(_at(row, m_idx("managerOverridePriority")) or ""),
            "managerOverrideTechnician": _s(_at(row, m_idx("managerOverrideTechnician")) or ""),
            "plannerNotes": _s(_at(row, m_idx("plannerNotes")) or ""),
            "closedSinceWeek": closed_week_val if closed_week_val is not None else "",
            "closedSinceYear": closed_year_val if closed_year_val is not None else "",
            "status": _s(_at(row, m_idx("status")) or "Active") or "Active",
        }

    now = iso_now()
    pos_ids_in_raw_data: dict[str, bool] = {}
    out_rows: list[list] = []

    for i in range(2, len(raw)):
        r = raw[i]
        pos_id = _s(_at(r, c_pos))
        if not pos_id:
            continue
        pos_ids_in_raw_data[pos_id] = True

        existing = existing_by_pos.get(pos_id)

        # Present in this week's RAW_DATA -> Active, always (see
        # ImportEngine.ts's "POS ACTIVE/CLOSED STATUS" comment - presence is
        # now the sole source of truth, confirmed by product owner).
        pos_status = "Active"
        closed_since_week = ""
        closed_since_year = ""

        out_rows.append([
            pos_id,
            _s(_at(r, c_termid)),
            _s(_at(r, c_market)),
            _s(_at(r, c_kateg)),
            _s(_at(r, c_term)),
            _s(_at(r, c_kategorizace)),
            _s(_at(r, c_nazev)),
            _s(_at(r, c_oblast)),
            _s(_at(r, c_area)),
            _s(_at(r, c_ulice)),
            _s(_at(r, c_cislo)),
            _s(_at(r, c_mesto)),
            _num(_at(r, c_x)),
            _num(_at(r, c_y)),
            _s(_at(r, c_tech)),
            _num(_at(r, c_ptt)),
            pos_status,
            closed_since_week,
            closed_since_year,
            "", "", "", "",
            "", "", "", "", 0,
            "",
            "", "", "", "",
            existing["managerOverrideType"] if existing else "",
            existing["managerOverridePriority"] if existing else "",
            existing["managerOverrideTechnician"] if existing else "",
            existing["plannerNotes"] if existing else "",
            "" if existing else now,
            now,
        ])

    for pos_id in existing_by_pos.keys():
        if pos_id not in pos_ids_in_raw_data:
            existing = existing_by_pos[pos_id]
            idx = next(
                (i for i, r in enumerate(master_existing) if _s(_at(r, m_idx("posId"))) == pos_id), -1
            )
            if idx >= 0:
                row = list(master_existing[idx])
                row[m_idx("status")] = "Closed"
                if existing["status"] != "Closed":
                    row[m_idx("closedSinceWeek")] = today_week
                    row[m_idx("closedSinceYear")] = today_year
                row[m_idx("updatedAt")] = now
                out_rows.append(row)

    master_header_row = [
        "posId", "terminalId", "market", "category", "terminalType", "classification",
        "nazev", "area", "posArea", "street", "houseNumber", "city", "gpsX", "gpsY",
        "assignedTechnician", "ppt", "status", "closedSinceWeek", "closedSinceYear",
        "currentLosActivity", "currentLotActivity", "targetLosActivity", "targetLotActivity",
        "lastRealVisitDate", "lastRealVisitWeek", "lastPlannedVisitDate",
        "weeksSinceLastVisit", "visitCountThisCampaign", "businessScore",
        "plannerStatus", "assignedWeek", "assignedDay", "gpsGroup",
        "managerOverrideType", "managerOverridePriority", "managerOverrideTechnician",
        "plannerNotes", "importedAt", "updatedAt",
    ]

    master_ws.get_range("A1:AM100000").clear()
    master_ws.get_range_by_indexes(0, 0, 1, len(master_header_row)).set_values([master_header_row])
    if out_rows:
        master_ws.get_range_by_indexes(1, 0, len(out_rows), len(master_header_row)).set_values(out_rows)

    return (
        "Import Engine: "
        f"{len(out_rows)} POS_MASTER rows upserted ({len(pos_ids_in_raw_data)} from RAW_DATA (Active), "
        f"{len(out_rows) - len(pos_ids_in_raw_data)} missing from RAW_DATA this run (set/kept Closed)."
    )
