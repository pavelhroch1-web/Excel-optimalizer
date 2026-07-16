"""Import templates — download a ready Excel with the exact columns the importer
expects, fill it in, upload it back. Explicit and predictable: no guessing what
the file is. Column names here MUST match importer._POS_MAP / import_salesapp /
tasks.parse_bulk_excel, so a filled template always imports cleanly.
"""
from __future__ import annotations

import io

import openpyxl

# (column header, example value) — headers are what the importer reads by name.
_TEMPLATES: dict[str, dict] = {
    "pos_master": {
        "title": "POS_MASTER",
        "columns": ["posId", "terminalId", "nazev", "street", "houseNumber", "city",
                    "area", "posArea", "category", "market", "classification",
                    "terminalType", "ppt", "gpsX", "gpsY", "assignedTechnician", "status"],
        "example": ["71001302", "81001302", "Prodejna Praha 1", "Ulice 5", "5", "Praha",
                    "PHA", "RSA", "1GECO", "IDT", "A", "VELKY TERMINAL", 1250.0,
                    50.0803, 14.4300, "Jan Novák", "ACTIVE"],
    },
    "salesapp": {
        "title": "SalesApp",
        "columns": ["UID", "Store UID", "Store", "Store address", "Agency region",
                    "Executor", "Executor UID", "Date", "Started at", "Finished at",
                    "Real duration (h)", "Účel návštevy"],
        "example": [1, "81001302", "Prodejna Praha 1", "Ulice 5, Praha", "Praha",
                    "Jan Novák", 101, "2026-07-01", "2026-07-01 09:00:00",
                    "2026-07-01 09:25:00", 0.417, 1],
    },
    "tasks": {
        "title": "Úkoly",
        "columns": ["POS", "Počet kusů", "Poznámka"],
        "example": ["71001302", 50, "várka A"],
    },
}


def kinds() -> list[str]:
    return list(_TEMPLATES)


def build(kind: str) -> bytes:
    """Return an .xlsx (bytes) template for `kind`, with a header row, one
    example row, and a short instruction row that the importer ignores."""
    spec = _TEMPLATES.get(kind)
    if not spec:
        raise KeyError(kind)
    wb = openpyxl.Workbook()
    ws = wb.active
    ws.title = spec["title"][:31]
    ws.append(spec["columns"])
    ws.append(spec["example"])
    buf = io.BytesIO()
    wb.save(buf)
    buf.seek(0)
    return buf.read()
