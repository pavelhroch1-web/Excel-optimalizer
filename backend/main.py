"""Field Force Optimizer - production live-planner backend.

A thin HTTP layer over the UNCHANGED desktop_client/engines/. It owns no
business logic; every endpoint assembles state and calls an already-verified
engine (Import / Compliance / Planning / Publish), then persists via the
versioned store (store.py + gh.py).

The git-like model (confirmed with the product owner):
  - The latest published SNAPSHOT is the single source of truth.
  - Uploading fresh exports only ever builds a new DRAFT (snapshot + this
    run's exports, Import+Compliance). It never touches a published plan.
  - Generate runs Planning on the draft; the manager can edit it manually.
  - Publish freezes the draft's lowest draft-week into a NEW immutable
    snapshot with an audit record; all later runs resume from it.

The seven live features, one endpoint group each:
  1 upload      POST /api/draft/upload         (multipart exports)
  2 generate    POST /api/draft/generate       {start_week, length}
  3 candidates  GET  /api/draft/candidates     ?week=&technician=
  4 edit        POST /api/draft/remove-pos | add-pos | change-technician
  5 publish     POST /api/publish              {message}
  6 history     GET  /api/versions
  7 download    GET  /api/versions/{id}/manager-plan  (+ /api/draft/download)
"""
from __future__ import annotations

import hashlib
import io
import os
import sys
import tempfile

import openpyxl
from fastapi import Depends, FastAPI, File, HTTPException, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import StreamingResponse
from pydantic import BaseModel

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import auth  # noqa: E402
from auth import issue_token, require_auth  # noqa: E402
import brain as brain_mod  # noqa: E402
import gh  # noqa: E402
import candidates as candidates_mod  # noqa: E402
import decision as decision_mod  # noqa: E402
import pipeline  # noqa: E402
import plan_io  # noqa: E402
import rules_io  # noqa: E402
import state_xlsx  # noqa: E402
import store  # noqa: E402

ENGINE_VERSION = os.environ.get("ENGINE_VERSION", "FFO-V11")
LOCAL_MODE = os.environ.get("FFO_LOCAL") == "1"

app = FastAPI(title="Field Force Optimizer API")

if LOCAL_MODE:
    import db  # noqa: E402

    @app.on_event("startup")
    def _init_local_db() -> None:
        db.init_db()

_allowed_origins = os.environ.get("ALLOWED_ORIGIN", "*")
app.add_middleware(
    CORSMiddleware,
    allow_origins=[_allowed_origins] if _allowed_origins != "*" else ["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)


class LoginRequest(BaseModel):
    password: str


class GenerateRequest(BaseModel):
    start_week: int
    length: int = 1
    mode: str = "vyvazeny"
    visits_per_tech_week: float | None = None


class PreflightRequest(BaseModel):
    start_week: int
    length: int = 5
    mode: str = "vyvazeny"
    visits_per_tech_week: float | None = None
    tech_count_override: int | None = None


class PublishRequest(BaseModel):
    message: str = ""


class RemovePosRequest(BaseModel):
    week: int
    pos_id: str
    technician: str


class ChangeTechnicianRequest(BaseModel):
    week: int
    pos_id: str
    old_technician: str
    new_technician: str


class AddPosRequest(BaseModel):
    week: int
    day: str
    technician: str
    pos_id: str


class SaveRulesRequest(BaseModel):
    sheet: str
    rows: list[dict]


class CloudGenerateRequest(BaseModel):
    start_week: int
    length: int = 5
    visits_per_tech: int = 40


# --------------------------------------------------------------------------
# helpers
# --------------------------------------------------------------------------

def _tmp(suffix=".xlsx") -> str:
    fd, path = tempfile.mkstemp(suffix=suffix)
    os.close(fd)
    return path


def _now_iso() -> str:
    import datetime
    return datetime.datetime.now(datetime.timezone.utc).isoformat()


def _require_draft_path() -> str:
    """Downloads the current draft to a temp file, or 409 if none exists."""
    if not store.draft_exists():
        raise HTTPException(status_code=409, detail="Zatím není žádný Draft. Nejdřív nahraj exporty.")
    path = _tmp()
    store.download_draft(path)
    return path


async def _save_upload(upload: UploadFile) -> tuple[str, dict]:
    """Persists an UploadFile to a temp path; returns (path, provenance)."""
    data = await upload.read()
    path = _tmp(suffix=os.path.splitext(upload.filename or "")[1] or ".xlsx")
    with open(path, "wb") as f:
        f.write(data)
    return path, {
        "filename": upload.filename,
        "bytes": len(data),
        "sha256": hashlib.sha256(data).hexdigest(),
    }


def _stream_sheet(workbook_path: str, sheet_name: str, download_name: str) -> StreamingResponse:
    src_wb = openpyxl.load_workbook(workbook_path, read_only=True, data_only=True)
    try:
        if sheet_name not in src_wb.sheetnames:
            raise HTTPException(status_code=404, detail=f"List {sheet_name} chybí.")
        src_ws = src_wb[sheet_name]
        out_wb = openpyxl.Workbook()
        out_ws = out_wb.active
        out_ws.title = sheet_name[:31]
        for row in src_ws.iter_rows(values_only=True):
            out_ws.append(row)
    finally:
        src_wb.close()
    buf = io.BytesIO()
    out_wb.save(buf)
    buf.seek(0)
    return StreamingResponse(
        buf,
        media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        headers={"Content-Disposition": f"attachment; filename={download_name}"},
    )


# --------------------------------------------------------------------------
# health / auth / status
# --------------------------------------------------------------------------

@app.get("/api/health")
def health():
    """Unauthenticated deployment self-check: confirms the latest snapshot
    (source of truth) downloads and parses. No business data exposed."""
    path = None
    try:
        path = store.snapshot_temp()
        size = os.path.getsize(path)
        wb = openpyxl.load_workbook(path, read_only=True, data_only=True)
        pos_rows = wb["POS_MASTER"].max_row - 1
        wb.close()
        return {
            "ok": True,
            "workbookBytes": size,
            "posMasterRows": pos_rows,
            "publishedVersions": len(store.read_index()),
            "hasDraft": store.draft_exists(),
        }
    except Exception as e:  # noqa: BLE001 - surface reason for ops
        return {"ok": False, "error": f"{type(e).__name__}: {e}"}
    finally:
        if path and os.path.exists(path):
            os.remove(path)


@app.post("/api/login")
def login(body: LoginRequest):
    # Local desktop app: single user on localhost, any/no password is fine.
    if not LOCAL_MODE and body.password != auth.APP_PASSWORD:
        raise HTTPException(status_code=401, detail="Nesprávné heslo.")
    return {"token": issue_token()}


@app.get("/api/status", dependencies=[Depends(require_auth)])
def status():
    """Current planner state: last published week, published-version count,
    and whether a draft is waiting."""
    index = store.read_index()
    published_weeks = [w for rec in index for w in rec.get("publishedWeeks", [])]
    return {
        "lastPublishedWeek": max(published_weeks) if published_weeks else None,
        "publishedVersions": len(index),
        "hasDraft": store.draft_exists(),
        "draftMeta": store.read_draft_meta() if store.draft_exists() else None,
    }


# --------------------------------------------------------------------------
# 1) upload -> build Draft (resume from latest snapshot + fresh exports)
# --------------------------------------------------------------------------

@app.post("/api/draft/upload", dependencies=[Depends(require_auth)])
async def draft_upload(
    pos_export: UploadFile | None = File(default=None),
    salesapp: list[UploadFile] = File(default=[]),
):
    """Builds a fresh Draft: resume from the latest published snapshot, fold
    in this run's exports, run Import + Compliance. Does NOT plan yet and does
    NOT touch any published plan.

    The POS export is OPTIONAL: a normal weekly run only brings a fresh
    SalesApp export (where technicians actually went). When no POS export is
    uploaded, the network (RAW_DATA) is taken from the latest snapshot - the
    POS structure changes rarely."""
    seed = None
    pos_path = None
    sa_paths: list[str] = []
    draft_path = None
    try:
        if pos_export is not None and (pos_export.filename or ""):
            pos_path, pos_meta = await _save_upload(pos_export)
            raw = pipeline.read_export_rows(pos_path)
        else:
            pos_meta = None
            raw = None  # pipeline falls back to the snapshot's RAW_DATA

        sa_exports = []
        sa_meta = []
        for f in salesapp:
            p, m = await _save_upload(f)
            sa_paths.append(p)
            sa_exports.append(pipeline.read_export_rows(p))
            sa_meta.append(m)

        if raw is None and not sa_exports:
            raise HTTPException(status_code=400, detail="Nahraj aspoň jeden soubor (SalesApp a/nebo POS export).")

        seed = store.snapshot_temp()  # latest snapshot (or bootstrap)
        result = pipeline.build_upload_draft(raw, sa_exports, seed_workbook=seed)

        draft_path = _tmp()
        state_xlsx.save_state(result["state"], draft_path)
        meta = {
            "uploadedAt": _now_iso(),
            "posExport": pos_meta,
            "salesAppExports": sa_meta,
            "resumedFrom": store.latest_snapshot_repo_path(),
            "engineVersion": ENGINE_VERSION,
        }
        store.save_draft(draft_path, "Upload: novy Draft z cerstvych exportu", meta=meta)

        return {"messages": result["messages"], "summary": pipeline._summarize(result["state"], 0, 0)}
    finally:
        for p in [seed, pos_path, draft_path, *sa_paths]:
            if p and os.path.exists(p):
                os.remove(p)


# --------------------------------------------------------------------------
# 2) generate -> run Planning on the Draft
# --------------------------------------------------------------------------

@app.post("/api/draft/generate", dependencies=[Depends(require_auth)])
def draft_generate(body: GenerateRequest):
    path = _require_draft_path()
    try:
        state = state_xlsx.load_state(path)
        if LOCAL_MODE:
            # Desktop: the Planning Engine reads its config from the DB
            # (business_rules + settings) via db_state; algorithm unchanged.
            import db_state
            db_state.configure(state, body.mode, body.start_week, body.length,
                               body.visits_per_tech_week)
        else:
            # Field Brain: a strategy mode + capacity only change goals/weights
            # via config; the Planning Engine algorithm is unchanged.
            brain_mod.apply_mode(state, body.mode)
            brain_mod.apply_capacity(state, body.visits_per_tech_week)
        messages = pipeline.run_planning(state, body.start_week, body.length)
        state_xlsx.save_state(state, path)
        store.save_draft(path, f"Generovat tour plan: tyden {body.start_week}, delka {body.length}, rezim {body.mode}")
        return {"messages": messages, "summary": pipeline._summarize(state, body.start_week, body.length)}
    finally:
        os.remove(path)


@app.post("/api/draft/preflight", dependencies=[Depends(require_auth)])
def draft_preflight(body: PreflightRequest):
    """Field Brain pre-flight: simulate the horizon under a strategy mode +
    capacity and COMPUTE the business scorecard (CORE / cadence / neglect /
    campaign coverage / capacity) + a managerial recommendation BEFORE
    generating. Read-only."""
    path = _require_draft_path()
    try:
        return brain_mod.preflight(
            path, body.start_week, body.length, body.mode,
            body.visits_per_tech_week, body.tech_count_override,
        )
    finally:
        os.remove(path)


@app.get("/api/strategy-modes", dependencies=[Depends(require_auth)])
def strategy_modes():
    modes = [{"id": k, "label": v["label"], "desc": v["desc"]}
             for k, v in brain_mod.STRATEGY_MODES.items()]
    if LOCAL_MODE:
        modes.append({"id": "cela_sit", "label": "Celá síť",
                      "desc": "Sweep celé sítě podle zanedbanosti a skóre (kampaně vypnuté)."})
    return {"modes": modes}


# --------------------------------------------------------------------------
# 3) candidates (read-only; real Planning Engine with observability)
# --------------------------------------------------------------------------

@app.get("/api/draft/candidates", dependencies=[Depends(require_auth)])
def draft_candidates(week: int, technician: str | None = None):
    path = _require_draft_path()
    try:
        return candidates_mod.list_candidates(path, week, technician)
    finally:
        os.remove(path)


@app.get("/api/draft/pos/{pos_id}", dependencies=[Depends(require_auth)])
def draft_pos_detail(pos_id: str, week: int):
    """Full read-only diagnostic for one POS for the given week - the same
    data and score the Planning Engine used, plus why it is / is not a
    candidate, plus the Decision Support recommendation. No new logic; runs
    the same engine."""
    path = _require_draft_path()
    try:
        return candidates_mod.pos_detail(path, pos_id, week)
    finally:
        os.remove(path)


@app.get("/api/draft/what-if", dependencies=[Depends(require_auth)])
def draft_what_if(week: int):
    """Decision Support 'Co kdyby...': impact of manager levers on the
    candidate pool for `week`, derived from ONE engine run's own capture -
    interpretation/simulation only, the Planning Engine is unchanged."""
    path = _require_draft_path()
    try:
        return decision_mod.what_if(path, week)
    finally:
        os.remove(path)


# --------------------------------------------------------------------------
# 4) view + manual edits (Draft weeks only; locked weeks are protected)
# --------------------------------------------------------------------------

@app.get("/api/draft", dependencies=[Depends(require_auth)])
def draft_view():
    path = _require_draft_path()
    try:
        return {"rows": plan_io.read_enriched_draft(path)}
    finally:
        os.remove(path)


@app.post("/api/draft/remove-pos", dependencies=[Depends(require_auth)])
def draft_remove_pos(body: RemovePosRequest):
    path = _require_draft_path()
    try:
        try:
            removed = plan_io.remove_pos(path, body.week, body.pos_id, body.technician)
        except ValueError as e:
            raise HTTPException(status_code=400, detail=str(e))
        if removed == 0:
            raise HTTPException(status_code=404, detail="POS v navrhu nenalezen.")
        store.save_draft(path, f"Odebrat POS {body.pos_id} z navrhu")
        return {"removed": removed}
    finally:
        os.remove(path)


@app.post("/api/draft/change-technician", dependencies=[Depends(require_auth)])
def draft_change_technician(body: ChangeTechnicianRequest):
    path = _require_draft_path()
    try:
        try:
            changed = plan_io.change_technician(
                path, body.week, body.pos_id, body.old_technician, body.new_technician
            )
        except ValueError as e:
            raise HTTPException(status_code=400, detail=str(e))
        if changed == 0:
            raise HTTPException(status_code=404, detail="POS v navrhu nenalezen.")
        store.save_draft(path, f"Presunout POS {body.pos_id} na {body.new_technician}")
        return {"changed": changed}
    finally:
        os.remove(path)


@app.post("/api/draft/add-pos", dependencies=[Depends(require_auth)])
def draft_add_pos(body: AddPosRequest):
    path = _require_draft_path()
    try:
        try:
            new_row = plan_io.add_pos(path, body.week, body.day, body.technician, body.pos_id)
        except ValueError as e:
            raise HTTPException(status_code=400, detail=str(e))
        store.save_draft(path, f"Pridat POS {body.pos_id} do navrhu")
        return {"row": new_row}
    finally:
        os.remove(path)


# --------------------------------------------------------------------------
# 5) publish -> freeze the Draft's lowest week into an immutable snapshot
# --------------------------------------------------------------------------

@app.post("/api/publish", dependencies=[Depends(require_auth)])
def publish(body: PublishRequest):
    path = _require_draft_path()
    snap_path = None
    try:
        state = state_xlsx.load_state(path)
        result = pipeline.run_publish(state)
        if not result["publishedWeeks"]:
            raise HTTPException(status_code=400, detail=result["message"])

        snap_path = _tmp()
        state_xlsx.save_state(state, snap_path)

        draft_meta = store.read_draft_meta()
        meta = {
            "publishedAt": _now_iso(),
            "publishedWeeks": result["publishedWeeks"],
            "message": body.message,
            "engineVersion": ENGINE_VERSION,
            "sourceExports": {
                "posExport": draft_meta.get("posExport"),
                "salesAppExports": draft_meta.get("salesAppExports"),
            },
            "resumedFrom": draft_meta.get("resumedFrom"),
        }
        record = store.publish_snapshot(snap_path, meta)

        # The draft now reflects the published (locked) state, so the manager
        # sees the freshly-locked week; the next upload resumes from the new
        # snapshot regardless.
        store.save_draft(path, f"Draft po publikaci {record['id']}")
        return {"published": record, "engineMessage": result["message"]}
    finally:
        for p in (path, snap_path):
            if p and os.path.exists(p):
                os.remove(p)


# --------------------------------------------------------------------------
# 6) history of published versions
# --------------------------------------------------------------------------

@app.get("/api/versions", dependencies=[Depends(require_auth)])
def versions():
    return {"versions": list(reversed(store.read_index()))}


# --------------------------------------------------------------------------
# 7) download a published (or the draft) MANAGER_PLAN
# --------------------------------------------------------------------------

@app.get("/api/versions/{version_id}/manager-plan", dependencies=[Depends(require_auth)])
def download_published_plan(version_id: str):
    path = _tmp()
    try:
        try:
            store.download_snapshot(version_id, path)
        except Exception:
            raise HTTPException(status_code=404, detail=f"Verze {version_id} nenalezena.")
        return _stream_sheet(path, "MANAGER_PLAN_PUBLISHED", f"MANAGER_PLAN_{version_id}.xlsx")
    finally:
        if os.path.exists(path):
            os.remove(path)


@app.get("/api/draft/download", dependencies=[Depends(require_auth)])
def download_draft_plan():
    path = _require_draft_path()
    try:
        return _stream_sheet(path, "MANAGER_PLAN", "MANAGER_PLAN_draft.xlsx")
    finally:
        os.remove(path)


# --------------------------------------------------------------------------
# Cloud generate: run the heavy multi-week plan on a GitHub Actions runner
# (~7 GB RAM) instead of this 512 MB host. The backend only orchestrates:
# it dispatches the workflow, reports the run status, and streams the Excel
# the runner committed into output/. One button on the web, no OOM here.
# --------------------------------------------------------------------------

WORKFLOW_FILE = "generate-tourplan.yml"
ARTIFACT_NAME = "tour-plan"


@app.post("/api/cloud/generate", dependencies=[Depends(require_auth)])
def cloud_generate(body: CloudGenerateRequest):
    """Trigger the GitHub Actions workflow that builds the plan and uploads
    the Excel as an artifact. Returns immediately; poll /status."""
    try:
        gh.dispatch_workflow(WORKFLOW_FILE, {
            "start_week": body.start_week,
            "length": body.length,
            "visits_per_tech": body.visits_per_tech,
        })
    except Exception as e:
        raise HTTPException(status_code=502,
                            detail=f"Nepodařilo se spustit GitHub workflow: {e}")
    return {"ok": True, "start_week": body.start_week}


@app.get("/api/cloud/status", dependencies=[Depends(require_auth)])
def cloud_status():
    """Status of the newest workflow run + whether its Excel artifact is ready."""
    run = gh.latest_run(WORKFLOW_FILE)
    ready = False
    if run and run["status"] == "completed" and run["conclusion"] == "success":
        ready = gh.run_artifact(run["id"], ARTIFACT_NAME) is not None
    return {"run": run, "ready": ready}


@app.get("/api/cloud/download", dependencies=[Depends(require_auth)])
def cloud_download(start_week: int = 0):
    """Stream the Excel from the newest successful run's artifact."""
    run = gh.latest_run(WORKFLOW_FILE)
    art = gh.run_artifact(run["id"], ARTIFACT_NAME) if run else None
    if not art:
        raise HTTPException(status_code=404,
                            detail="Plán ještě není hotový nebo artifact vypršel.")
    try:
        data = gh.download_artifact_xlsx(art["id"])
    except Exception as e:
        raise HTTPException(status_code=502, detail=f"Stažení artifactu selhalo: {e}")
    name = f"TOUR_PLAN_tydny_{start_week}.xlsx" if start_week else "TOUR_PLAN.xlsx"
    return StreamingResponse(
        io.BytesIO(data),
        media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        headers={"Content-Disposition": f"attachment; filename={name}"},
    )


# --------------------------------------------------------------------------
# rules (config) - read/edit the manager rule tables on the current draft
# (or the latest snapshot if no draft yet). Not one of the 7 core features,
# kept working so rule tweaks are possible without opening Excel.
# --------------------------------------------------------------------------

def _rules_source_path() -> str:
    path = _tmp()
    if store.draft_exists():
        store.download_draft(path)
    else:
        store.download_latest_snapshot(path)
    return path


@app.get("/api/rules", dependencies=[Depends(require_auth)])
def get_rules():
    path = _rules_source_path()
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
        raise HTTPException(status_code=400, detail=f"Neznama tabulka pravidel: {body.sheet}")
    if not store.draft_exists():
        raise HTTPException(status_code=409, detail="Pravidla lze upravit az po nahrani exportu (v Draftu).")
    path = _tmp()
    store.download_draft(path)
    try:
        rules_io.write_rule_sheet(path, body.sheet, body.rows)
        store.save_draft(path, f"Upravit {body.sheet}")
        return {"ok": True}
    finally:
        os.remove(path)


# --------------------------------------------------------------------------
# Local desktop app: serve the existing frontend from THIS server so the UI
# and API share one origin (127.0.0.1). config.js is generated to point the
# frontend at the same origin; everything else in web/ is served as-is. The
# GitHub Pages copy of web/ is untouched - this only runs when FFO_LOCAL=1.
# --------------------------------------------------------------------------

if LOCAL_MODE:
    # ----------------------------------------------------------------------
    # Import (Excel -> SQLite): the only way real data enters the datastore.
    # POS Master, SalesApp history, Activity Plan, config in one workbook.
    # ----------------------------------------------------------------------
    import importer  # noqa: E402

    _DATA_TABLES = ["pos_master", "salesapp_visits", "campaigns", "technicians",
                    "closed_pos", "snapshots", "published_plans", "draft_plans",
                    "route_metrics"]

    @app.post("/api/import/workbook", dependencies=[Depends(require_auth)])
    async def import_workbook_ep(workbook: UploadFile = File(...)):
        path, prov = await _save_upload(workbook)
        try:
            counts = importer.import_workbook(path, prov["filename"])
            return {"ok": True, "counts": counts, "file": prov}
        finally:
            os.remove(path)

    @app.get("/api/data/summary", dependencies=[Depends(require_auth)])
    def data_summary():
        return {t: db.get(f"SELECT COUNT(*) AS c FROM {t}")[0]["c"] for t in _DATA_TABLES}

    import pos_insights  # noqa: E402

    @app.get("/api/pos/{pos_id}/visits", dependencies=[Depends(require_auth)])
    def pos_visits(pos_id: str):
        """Informational: who visited this POS (technician vs OZ), when, what."""
        return pos_insights.pos_visit_summary(pos_id)

    # Business Rules: planning logic as data (toggle / edit params, no code).
    import business_rules as _rules  # noqa: E402

    class RuleUpdate(BaseModel):
        enabled: bool | None = None
        params: dict | None = None
        scope: str = "global"
        scope_value: str | None = None

    @app.get("/api/rules/business", dependencies=[Depends(require_auth)])
    def get_business_rules():
        return {"rules": _rules.list_rules(), "effective": _rules.effective()}

    @app.put("/api/rules/business/{code}", dependencies=[Depends(require_auth)])
    def update_business_rule(code: str, body: RuleUpdate):
        if body.enabled is not None:
            _rules.set_enabled(code, body.enabled, body.scope, body.scope_value)
        if body.params is not None:
            _rules.set_params(code, body.params, body.scope, body.scope_value)
        return {"ok": True, "effective": _rules.effective()}

    # Settings platform: configure planner/optimization/dashboard/report/map/
    # scoring from the app. Definitions drive a generic admin UI; values override.
    import settings as _settings  # noqa: E402

    class SettingUpdate(BaseModel):
        value: object
        scope: str = "global"
        scope_value: str | None = None

    class ViewUpsert(BaseModel):
        definition: object
        is_default: bool = False

    @app.get("/api/settings/definitions", dependencies=[Depends(require_auth)])
    def settings_definitions(namespace: str | None = None):
        return {"definitions": _settings.definitions(namespace)}

    @app.get("/api/settings/{namespace}", dependencies=[Depends(require_auth)])
    def settings_get(namespace: str):
        return {"namespace": namespace, "values": _settings.effective(namespace),
                "definitions": _settings.definitions(namespace)}

    @app.put("/api/settings/{namespace}/{key}", dependencies=[Depends(require_auth)])
    def settings_put(namespace: str, key: str, body: SettingUpdate):
        _settings.set_value(namespace, key, body.value, body.scope, body.scope_value)
        return {"ok": True, "values": _settings.effective(namespace)}

    @app.get("/api/views/{namespace}", dependencies=[Depends(require_auth)])
    def views_list(namespace: str):
        return {"views": _settings.list_views(namespace)}

    @app.put("/api/views/{namespace}/{name}", dependencies=[Depends(require_auth)])
    def views_put(namespace: str, name: str, body: ViewUpsert):
        _settings.save_view(namespace, name, body.definition, body.is_default)
        return {"ok": True, "views": _settings.list_views(namespace)}

    @app.delete("/api/views/{namespace}/{name}", dependencies=[Depends(require_auth)])
    def views_delete(namespace: str, name: str):
        _settings.delete_view(namespace, name)
        return {"ok": True}

    import sys as _sys

    from fastapi.responses import HTMLResponse, Response
    from fastapi.staticfiles import StaticFiles

    def _web_dir() -> str:
        for base in (getattr(_sys, "_MEIPASS", None),
                     os.path.dirname(os.path.dirname(os.path.abspath(__file__)))):
            if base:
                d = os.path.join(base, "web")
                if os.path.isdir(d):
                    return d
        return os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "web")

    _WEB = _web_dir()

    @app.get("/config.js")
    def _local_config():
        # Same-origin API + a marker so the frontend can auto-login locally.
        return Response('window.FFO_API_BASE="";window.FFO_LOCAL=true;',
                        media_type="application/javascript")

    @app.get("/", response_class=HTMLResponse)
    def _index():
        with open(os.path.join(_WEB, "index.html"), encoding="utf-8") as f:
            return f.read()

    # Everything else (app.js, styles.css, ...) straight from web/.
    app.mount("/", StaticFiles(directory=_WEB), name="web")
