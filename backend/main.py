"""Field Force Optimizer - MVP backend.

A thin HTTP wrapper around the existing, unchanged desktop_client/engines/
Python engines. Owns none of the business logic - every endpoint just
downloads the real .xlsx from GitHub (github_storage.py), runs one or more
already-verified engines against it via xlsx_engine_io.py, and (for
write actions) commits the updated file back to GitHub.

Fáze 0 scope, deliberately narrow (product owner, 2026-07-11: "jediným
cílem: kvalitní Planning Engine + jednoduché webové rozhraní pro jeho
používání... To je vše"): login, current-status, generate a tour plan
(Planning Engine only), view the draft, download MANAGER_PLAN as a
standalone .xlsx. No publish, no Reporting/Performance/Dashboards/admin -
those are later phases, deliberately not started yet.

Also exposes the existing manager rule tables (TERMINAL_RULES,
MARKET_RULES, CATEGORY_RULES, ACTIVITY_PLAN) for editing - product owner,
same day: "nechci, aby se znovu navrhovala business logika Planneru...
chci pouze přenést existující manažerské volby z Excelu do jednoduchého
UI". See backend/rules_io.py - only the same cells a manager already edits
in Excel are ever written; no rule semantics change.
"""
from __future__ import annotations

import io
import os
import sys
import tempfile

import openpyxl
from fastapi import Depends, FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import StreamingResponse
from pydantic import BaseModel

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from desktop_client import xlsx_engine_io  # noqa: E402
from desktop_client.engines import planning_engine  # noqa: E402
from desktop_client.engines.mock_workbook import MockWorkbook  # noqa: E402

import auth  # noqa: E402
from auth import issue_token, require_auth  # noqa: E402
import github_storage  # noqa: E402
import rules_io  # noqa: E402

app = FastAPI(title="Field Force Optimizer API")

_allowed_origins = os.environ.get("ALLOWED_ORIGIN", "*")
app.add_middleware(
    CORSMiddleware,
    allow_origins=[_allowed_origins] if _allowed_origins != "*" else ["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)


class LoginRequest(BaseModel):
    password: str


class GeneratePlanRequest(BaseModel):
    start_week: int
    length: int = 4


class SaveRulesRequest(BaseModel):
    sheet: str
    rows: list[dict]


def _with_local_copy():
    """Downloads the current workbook to a temp file and returns its path.
    Caller is responsible for deleting it."""
    fd, path = tempfile.mkstemp(suffix=".xlsx")
    os.close(fd)
    github_storage.download_workbook(path)
    return path


def _set_control(state: dict, key: str, value) -> None:
    """Finds CONTROL!key (case-insensitive) and overwrites its value, or
    appends a new row if not present - same convention Planning Engine
    itself reads via norm()-matched lookups."""
    control = state["CONTROL"]
    key_norm = key.strip().upper()
    for row in control[1:]:
        if str(row[0]).strip().upper() == key_norm:
            row[1] = value
            return
    control.append([key, value, ""])


@app.post("/api/login")
def login(body: LoginRequest):
    if body.password != auth.APP_PASSWORD:
        raise HTTPException(status_code=401, detail="Nesprávné heslo.")
    return {"token": issue_token()}


@app.get("/api/status", dependencies=[Depends(require_auth)])
def status():
    path = _with_local_copy()
    try:
        wb = openpyxl.load_workbook(path, read_only=True, data_only=True)
        mp = wb["MANAGER_PLAN"]
        pl = wb["PLAN_LIFECYCLE"]

        mp_header = [c.value for c in next(mp.iter_rows(min_row=1, max_row=1))]
        mp_week_idx = mp_header.index("WEEK")
        draft_weeks = sorted({
            int(row[mp_week_idx]) for row in mp.iter_rows(min_row=2, values_only=True)
            if row[mp_week_idx] not in (None, "")
        })

        pl_header = [c.value for c in next(pl.iter_rows(min_row=1, max_row=1))]
        pl_status_idx = pl_header.index("status")
        pl_week_idx = pl_header.index("week")
        published_weeks = [
            int(row[pl_week_idx]) for row in pl.iter_rows(min_row=2, values_only=True)
            if row and row[pl_status_idx] in ("Published", "Active", "Closed")
        ]
        last_published_week = max(published_weeks) if published_weeks else None

        locked = set(published_weeks)
        pending_draft_weeks = sorted(w for w in draft_weeks if w not in locked)

        return {
            "lastPublishedWeek": last_published_week,
            "draftWeeks": pending_draft_weeks,
            "hasDraft": len(pending_draft_weeks) > 0,
        }
    finally:
        os.remove(path)


@app.post("/api/generate-plan", dependencies=[Depends(require_auth)])
def generate_plan(body: GeneratePlanRequest):
    path = _with_local_copy()
    try:
        state = xlsx_engine_io.read_state(path)
        _set_control(state, "CAMPAIGN_START_WEEK", body.start_week)
        _set_control(state, "CAMPAIGN_LENGTH", body.length)

        wb = MockWorkbook(state)
        message = planning_engine.run(wb)
        out = wb.dump()

        xlsx_engine_io.write_state(path, out, {"MANAGER_PLAN"})
        github_storage.upload_workbook(
            path, f"Generovat tour plán: týden {body.start_week}, délka {body.length} [MVP cockpit]"
        )
        return {"message": message}
    finally:
        os.remove(path)


@app.get("/api/plan/draft", dependencies=[Depends(require_auth)])
def plan_draft():
    path = _with_local_copy()
    try:
        wb = openpyxl.load_workbook(path, read_only=True, data_only=True)
        ws = wb["MANAGER_PLAN"]
        header = [c.value for c in next(ws.iter_rows(min_row=1, max_row=1))]
        rows = [
            dict(zip(header, row))
            for row in ws.iter_rows(min_row=2, values_only=True)
            if row[0] not in (None, "")
        ]
        return {"rows": rows}
    finally:
        os.remove(path)


@app.get("/api/download/manager-plan", dependencies=[Depends(require_auth)])
def download_manager_plan():
    path = _with_local_copy()
    try:
        src_wb = openpyxl.load_workbook(path, read_only=True, data_only=True)
        src_ws = src_wb["MANAGER_PLAN"]

        out_wb = openpyxl.Workbook()
        out_ws = out_wb.active
        out_ws.title = "MANAGER_PLAN"
        for row in src_ws.iter_rows(values_only=True):
            out_ws.append(row)

        buf = io.BytesIO()
        out_wb.save(buf)
        buf.seek(0)
        return StreamingResponse(
            buf,
            media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
            headers={"Content-Disposition": "attachment; filename=MANAGER_PLAN.xlsx"},
        )
    finally:
        os.remove(path)


@app.get("/api/rules", dependencies=[Depends(require_auth)])
def get_rules():
    path = _with_local_copy()
    try:
        sheets = rules_io.read_all_rule_sheets(path)
        return {
            "terminal": sheets["TERMINAL_RULES"],
            "market": sheets["MARKET_RULES"],
            "category": sheets["CATEGORY_RULES"],
            "campaigns": sheets["ACTIVITY_PLAN"],
        }
    finally:
        os.remove(path)


@app.post("/api/rules", dependencies=[Depends(require_auth)])
def save_rules(body: SaveRulesRequest):
    if body.sheet not in rules_io.RULE_SHEETS:
        raise HTTPException(status_code=400, detail=f"Neznámá tabulka pravidel: {body.sheet}")
    path = _with_local_copy()
    try:
        rules_io.write_rule_sheet(path, body.sheet, body.rows)
        github_storage.upload_workbook(path, f"Upravit {body.sheet} [MVP cockpit]")
        return {"ok": True}
    finally:
        os.remove(path)
