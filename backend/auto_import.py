"""Automatic import - drop an Excel and the system figures out what it is.

Detects the file type from the sheet headers (no manual mapping), imports it
into SQLite, and returns what it did. Supports single-sheet real exports
(SalesApp / POS Master / Activity Plan) and the full scaffold workbook.
After a SalesApp import it recomputes reality metrics + alerts so the platform
stays current on its own.
"""
from __future__ import annotations

import openpyxl

import db
import importer


def _headers(ws) -> set[str]:
    it = ws.iter_rows(values_only=True)
    for row in it:
        if row and any(v not in (None, "") for v in row):
            return {str(v).strip() for v in row if v not in (None, "")}
    return set()


def _classify(ws) -> str | None:
    h = _headers(ws)
    if "UID" in h and ("Store UID" in h or "Store" in h) and "Executor" in h:
        return "salesapp"
    if "posId" in h and "terminalId" in h:
        return "pos_master"
    if "TYPE" in h and "ACTIVITY" in h and "START_WEEK" in h:
        return "activity_plan"
    hu = {str(x).strip().upper() for x in h}
    if ("WEEK" in hu or "TÝDEN" in hu or "TYDEN" in hu or "TOURPLAN" in hu) and \
       ("TECHNICIAN" in hu or "TECHNIK" in hu) and "POS" in hu:
        return "tourplan"
    return None


def detect(path: str) -> dict:
    wb = openpyxl.load_workbook(path, read_only=True, data_only=True)
    try:
        names = set(wb.sheetnames)
        if {"POS_MASTER", "SALESAPP_IMPORT"} & names and "CONTROL" in names:
            return {"type": "workbook", "sheet": None}
        for name in wb.sheetnames:
            t = _classify(wb[name])
            if t:
                return {"type": t, "sheet": name}
        return {"type": "unknown", "sheet": None}
    finally:
        wb.close()


def import_file(path: str, filename: str | None = None) -> dict:
    """Detect + import one file. Returns {detected, counts, recomputed}."""
    db.init_db()
    det = detect(path)
    t = det["type"]
    if t == "workbook":
        counts = importer.import_workbook(path, filename)
        rec = _recompute_after(counts)
        return {"detected": "workbook", "counts": counts, "recomputed": rec}

    if t == "unknown":
        return {"detected": "unknown", "counts": {},
                "error": "Nepodařilo se rozpoznat typ souboru (POS Master / SalesApp / Activity Plan)."}

    wb = openpyxl.load_workbook(path, read_only=True, data_only=True)
    conn = db.connect()
    counts: dict = {}
    try:
        ws = wb[det["sheet"]]
        if t == "salesapp":
            counts["salesapp_visits"] = importer.import_salesapp(conn, ws, filename)
        elif t == "pos_master":
            pos_summary = importer.import_pos_master(conn, ws)
            counts["pos_master"] = pos_summary["total"]
            counts["pos_diff"] = pos_summary
            counts["technicians"] = importer.derive_technicians(conn)
        elif t == "activity_plan":
            counts["campaigns"] = importer.import_activity_plan(conn, ws)
        conn.commit()
    finally:
        conn.close()
        wb.close()

    if t == "tourplan":
        # Ingest as a published plan via its own (transactional) path.
        summary = importer.import_tourplan(path, filename)
        counts["tourplan"] = summary
        rec = _recompute_after(counts)
        return {"detected": t, "sheet": det["sheet"], "counts": counts, "recomputed": rec}

    if t == "salesapp":
        importer.derive_technicians(db.connect())  # refresh roles (own conn)
    importer.sync_rules_from_config()
    rec = _recompute_after(counts)
    return {"detected": t, "sheet": det["sheet"], "counts": counts, "recomputed": rec}


def _recompute_after(counts: dict) -> list[str]:
    """After a sync, auto-compute what depends on the new data (reality metrics
    + alerts). Central place so every import path stays current."""
    done = []
    if counts.get("salesapp_visits"):
        try:
            import alerts
            n = alerts.recompute()
            done.append(f"alerts:{n}")
        except Exception as e:  # noqa: BLE001
            done.append(f"alerts_failed:{e}")
    return done
