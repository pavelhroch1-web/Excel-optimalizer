"""Pre-import validation — the guard that stops "false success".

Before we claim a file was imported, we check that it structurally CAN be:
the right sheet is present, the required columns exist (after the same alias
resolution the importer uses), and there is at least one data row under the
header. If not, we say exactly what is wrong — a named missing column or an
empty sheet — instead of reporting a cheerful zero-row success.

Everything here is read-only. It never writes to the datastore.
"""
from __future__ import annotations

import openpyxl

import importer

# Required (hard) + recommended (soft) columns per kind, expressed in the
# NORMALIZED keys the importer resolves raw/aliased headers to. A missing hard
# column means the import cannot work → error. A missing soft column still
# imports but loses a feature → warning.
_KIND_LABEL = {"pos_master": "POS Master", "salesapp": "SalesApp export",
               "activity_plan": "Activity Plan", "tourplan": "TourPlan",
               "workbook": "kompletní workbook"}

_REQUIRED = {
    "pos_master": {
        "hard": [("posId", "číslo POS")],
        # PPT is the backbone of POS Master (revenue potential); dedup + the
        # whole ranking depend on it. The client's header may read PTT or PPT.
        "soft": [("ppt", "PPT / PTT (potenciál)"), ("nazev", "název provozovny"),
                 ("street", "ulice"), ("city", "město")],
    },
    "salesapp": {
        "hard": [("UID", "UID návštěvy"), ("Executor", "Executor (technik)")],
        "soft": [("Store UID", "Store UID (napojení na POS)"), ("Date", "datum"),
                 ("Real duration (h)", "reálné trvání")],
    },
    "activity_plan": {"hard": [], "soft": []},   # matrix or table — checked by row count
    "tourplan": {
        "hard": [("POS", "POS"), ("TECHNICIAN", "technik"), ("WEEK", "týden")],
        "soft": [],
    },
}


def _first_sheet(path: str):
    wb = openpyxl.load_workbook(path, read_only=True, data_only=True)
    return wb


def _resolve_headers(ws, kind: str) -> tuple[set[str], int]:
    """Return (resolved header keys, number of data rows). For pos_master we run
    the importer's own alias map so 'ČÍSLO TERMINÁLU'/'PTT' resolve exactly as
    they will at import time. Data-row count is a bounded scan (cheap, honest)."""
    aliases = importer._POS_HEADER_ALIASES if kind == "pos_master" else None
    hidx, it, header = importer._rows(ws, aliases)
    keys = set(hidx.keys())
    # also expose raw upper-cased header names for salesapp/tourplan checks
    raw_upper = {importer._norm_header(h) for h in header if h not in (None, "")}
    # count data rows (cap the scan; we only need "is there ≥1")
    rows = 0
    for _ in it:
        rows += 1
        if rows >= 5:  # enough to prove non-empty without reading 11k rows twice
            break
    return keys, rows, raw_upper


def validate(path: str, kind: str) -> dict:
    """Structural pre-check for a detected/selected import kind.

    Returns {ok, kind, missing:[label...], warnings:[label...], reason|None}.
    ok=False means: do not claim success — tell the user `reason`.
    """
    spec = _REQUIRED.get(kind)
    if spec is None:
        return {"ok": True, "kind": kind, "missing": [], "warnings": [], "reason": None}

    wb = _first_sheet(path)
    try:
        # pick the sheet the importer would use (first non-empty / detected)
        ws = wb[wb.sheetnames[0]]
        keys, data_rows, raw_upper = _resolve_headers(ws, kind)
    finally:
        wb.close()

    def _present(col: str) -> bool:
        return col in keys or importer._norm_header(col) in raw_upper

    missing = [label for col, label in spec["hard"] if not _present(col)]
    warnings = [label for col, label in spec["soft"] if not _present(col)]

    if missing:
        return {"ok": False, "kind": kind, "missing": missing, "warnings": warnings,
                "reason": "Soubor vypadá jako %s, ale chybí povinný sloupec: %s."
                          % (_KIND_LABEL.get(kind, kind), ", ".join(missing))}

    if data_rows == 0:
        return {"ok": False, "kind": kind, "missing": [], "warnings": warnings,
                "reason": "Hlavička sedí, ale pod ní nejsou žádné datové řádky — soubor je prázdný."}

    return {"ok": True, "kind": kind, "missing": [], "warnings": warnings, "reason": None}
