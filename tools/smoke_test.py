#!/usr/bin/env python3
"""End-to-end smoke test: boot the real app and hit the key endpoints.

Catches the class of breakage unit tests can't — an endpoint that 500s, a route
that vanished, a response whose shape the frontend relies on. Boots uvicorn on a
throwaway copy of the seed DB, asserts each critical endpoint returns 200 with
the fields the UI reads, and checks the import "no false success" guard for real.

Runs with only stdlib + requests (both present in the build image), so it can
gate the GitHub Action before it spends minutes packaging a broken .exe.

Usage:  python tools/smoke_test.py [--port 8899]
Exit 0 = all green, 1 = a failure.
"""
from __future__ import annotations

import argparse
import io
import os
import shutil
import subprocess
import sys
import tempfile
import time
import zipfile

import requests

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

_passed = 0
_failed = 0


def check(name, cond, detail=""):
    global _passed, _failed
    if cond:
        _passed += 1
        print(f"  ok   {name}")
    else:
        _failed += 1
        print(f"  FAIL {name}   {detail}")


def _minimal_xlsx(headers, rows):
    """Build a tiny .xlsx in memory without openpyxl (keep deps minimal)."""
    def cell(v):
        return f'<c t="inlineStr"><is><t>{v}</t></is></c>'
    def row(vals):
        return "<row>" + "".join(cell(v) for v in vals) + "</row>"
    sheet = ('<?xml version="1.0"?><worksheet xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main">'
             "<sheetData>" + row(headers) + "".join(row(r) for r in rows) + "</sheetData></worksheet>")
    wbxml = ('<?xml version="1.0"?><workbook xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main" '
             'xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships">'
             '<sheets><sheet name="Sheet1" sheetId="1" r:id="rId1"/></sheets></workbook>')
    rels = ('<?xml version="1.0"?><Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
            '<Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/worksheet" Target="worksheets/sheet1.xml"/></Relationships>')
    ct = ('<?xml version="1.0"?><Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">'
          '<Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/>'
          '<Default Extension="xml" ContentType="application/xml"/>'
          '<Override PartName="/xl/workbook.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet.main+xml"/>'
          '<Override PartName="/xl/worksheets/sheet1.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.worksheet+xml"/></Types>')
    root_rels = ('<?xml version="1.0"?><Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
                 '<Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/officeDocument" Target="xl/workbook.xml"/></Relationships>')
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as z:
        z.writestr("[Content_Types].xml", ct)
        z.writestr("_rels/.rels", root_rels)
        z.writestr("xl/workbook.xml", wbxml)
        z.writestr("xl/_rels/workbook.xml.rels", rels)
        z.writestr("xl/worksheets/sheet1.xml", sheet)
    return buf.getvalue()


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--port", type=int, default=8899)
    args = ap.parse_args()
    base = f"http://127.0.0.1:{args.port}"

    work = tempfile.mkdtemp(prefix="ffo_smoke_")
    seed = os.path.join(REPO, "seed", "fieldforce.db")
    db_path = os.path.join(work, "fieldforce.db")
    if os.path.exists(seed):
        shutil.copy(seed, db_path)

    env = dict(os.environ, FFO_LOCAL="1", FFO_DB_PATH=db_path)
    proc = subprocess.Popen(
        [sys.executable, "-m", "uvicorn", "main:app", "--app-dir",
         os.path.join(REPO, "backend"), "--host", "127.0.0.1", "--port", str(args.port)],
        env=env, stdout=subprocess.PIPE, stderr=subprocess.STDOUT)
    try:
        # wait for boot (up to ~30s)
        up = False
        for _ in range(60):
            try:
                if requests.get(base + "/api/data/summary", timeout=2).status_code == 200:
                    up = True
                    break
            except requests.RequestException:
                pass
            if proc.poll() is not None:
                out = proc.stdout.read().decode(errors="replace") if proc.stdout else ""
                print("server died on boot:\n" + out[-2000:])
                return 1
            time.sleep(0.5)
        check("server boots + /api/data/summary 200", up)
        if not up:
            return 1

        def get(path):
            return requests.get(base + path, timeout=15)

        r = get("/api/data/summary")
        check("data/summary has pos_master count", r.ok and (r.json().get("pos_master", 0) > 0))

        r = get("/api/pos/list?limit=5")
        j = r.json() if r.ok else {}
        check("pos/list returns paginated pos[]", r.ok and isinstance(j.get("pos"), list) and j.get("total", 0) > 0)

        r = get("/api/technicians")
        check("technicians list ok", r.ok and isinstance(r.json().get("technicians"), list))

        r = get("/api/settings/anomaly")
        check("settings/anomaly present", r.ok and "short_visit_max_min" in (r.json().get("values") or {}))

        for path, key in [("/api/insights?days_back=120", None),
                          ("/api/analytics/team?days_back=120", None),
                          ("/api/gis/network?days_back=120", "counts"),
                          ("/api/pos/duplicates?limit=5", "groups"),
                          ("/api/data/quality", "checks"),
                          ("/api/model", "sections"),
                          ("/api/insights/health?days_back=90", "technicians")]:
            r = get(path)
            ok = r.status_code == 200 and (key is None or key in r.json())
            check(f"GET {path.split('?')[0]} 200", ok, f"status {r.status_code}")

        # a technician with data -> hotspots must carry the shortVisits section
        techs = get("/api/technicians").json().get("technicians", [])
        name = next((t["name"] for t in techs if t.get("role") == "TECHNIK"), None)
        if name:
            r = get(f"/api/technician/{requests.utils.quote(name)}/hotspots?days_back=120")
            check("technician hotspots has shortVisits key",
                  r.ok and "shortVisits" in r.json(), f"status {r.status_code}")

        # import guard: a POS file missing the POS-id column must NOT report success
        bad = _minimal_xlsx(["NAZEV PROVOZOVNY", "PPT"], [["Test", "5000"]])
        r = requests.post(base + "/api/import/pos_master",
                          files={"file": ("bad.xlsx", bad,
                                          "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")},
                          timeout=30)
        jr = r.json() if r.ok else {}
        check("import guard rejects missing-column file (ok:false)",
              r.status_code == 200 and jr.get("ok") is False and jr.get("total") == 0,
              f"resp {jr}")

        print(f"\n{_passed} passed, {_failed} failed")
        return 1 if _failed else 0
    finally:
        proc.terminate()
        try:
            proc.wait(timeout=5)
        except subprocess.TimeoutExpired:
            proc.kill()


if __name__ == "__main__":
    sys.exit(main())
