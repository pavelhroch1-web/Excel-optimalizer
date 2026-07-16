"""Generalized GitHub Contents-API file client - download/upload/exists for
ANY path in the repo (github_storage.py is the single-workbook special
case). Every write is a real commit, so the version store gets free,
audited, durable history without any extra infrastructure.

Carries the >1MB fix: the Contents API only inlines base64 `content` for
files <= 1MB, so downloads use the raw media type (bytes up to 100MB).
"""
from __future__ import annotations

import base64
import json
import os

try:
    import httpx  # only for the cloud GitHub path; local desktop never uses it
except ModuleNotFoundError:  # not bundled in the portable .exe (not needed there)
    httpx = None

# Optional: only needed for the (cloud) GitHub Actions path. The local
# desktop app never uses gh.py, so importing it must not require a token.
GITHUB_TOKEN = os.environ.get("GITHUB_TOKEN", "")
GITHUB_REPO = os.environ.get("GITHUB_REPO", "pavelhroch1-web/excel-optimalizer")
GITHUB_BRANCH = os.environ.get("GITHUB_BRANCH", "main")

_HEADERS = {
    "Authorization": f"Bearer {GITHUB_TOKEN}",
    "Accept": "application/vnd.github+json",
    "X-GitHub-Api-Version": "2022-11-28",
}


def _url(path_in_repo: str) -> str:
    return f"https://api.github.com/repos/{GITHUB_REPO}/contents/{path_in_repo}"


def exists(path_in_repo: str) -> bool:
    with httpx.Client(timeout=60) as client:
        resp = client.get(_url(path_in_repo), headers=_HEADERS, params={"ref": GITHUB_BRANCH})
    return resp.status_code == 200


def download(path_in_repo: str, dest_path: str) -> None:
    raw_headers = {**_HEADERS, "Accept": "application/vnd.github.raw+json"}
    with httpx.Client(timeout=120) as client:
        resp = client.get(_url(path_in_repo), headers=raw_headers, params={"ref": GITHUB_BRANCH})
        resp.raise_for_status()
        content = resp.content
    with open(dest_path, "wb") as f:
        f.write(content)


def _current_sha(client: httpx.Client, path_in_repo: str) -> str | None:
    resp = client.get(_url(path_in_repo), headers=_HEADERS, params={"ref": GITHUB_BRANCH})
    if resp.status_code == 404:
        return None
    resp.raise_for_status()
    return resp.json()["sha"]


def upload_bytes(path_in_repo: str, data: bytes, commit_message: str) -> None:
    with httpx.Client(timeout=120) as client:
        sha = _current_sha(client, path_in_repo)
        body = {
            "message": commit_message,
            "content": base64.b64encode(data).decode("ascii"),
            "branch": GITHUB_BRANCH,
        }
        if sha:
            body["sha"] = sha
        resp = client.put(_url(path_in_repo), headers=_HEADERS, json=body)
        resp.raise_for_status()


def upload_file(path_in_repo: str, src_path: str, commit_message: str) -> None:
    with open(src_path, "rb") as f:
        upload_bytes(path_in_repo, f.read(), commit_message)


def read_json(path_in_repo: str, default=None):
    raw_headers = {**_HEADERS, "Accept": "application/vnd.github.raw+json"}
    with httpx.Client(timeout=60) as client:
        resp = client.get(_url(path_in_repo), headers=raw_headers, params={"ref": GITHUB_BRANCH})
    if resp.status_code == 404:
        return default
    resp.raise_for_status()
    return json.loads(resp.content)


def write_json(path_in_repo: str, obj, commit_message: str) -> None:
    upload_bytes(path_in_repo, json.dumps(obj, ensure_ascii=False, indent=2).encode("utf-8"), commit_message)


# ---------------------------------------------------------------------------
# GitHub Actions: run the heavy tour-plan generation on a runner (~7 GB RAM),
# so the 512 MB free host never has to. The backend only orchestrates - it
# dispatches the workflow and reports status; the runner does the compute and
# commits the Excel back into the repo (output/), which we then serve.
# ---------------------------------------------------------------------------

def _actions_url(suffix: str) -> str:
    return f"https://api.github.com/repos/{GITHUB_REPO}/actions/{suffix}"


def dispatch_workflow(workflow_file: str, inputs: dict, ref: str = GITHUB_BRANCH) -> None:
    """Trigger a workflow_dispatch run. `inputs` values are sent as strings
    (GitHub requires string inputs). Needs a token with Actions: write."""
    body = {"ref": ref, "inputs": {k: str(v) for k, v in inputs.items()}}
    with httpx.Client(timeout=60) as client:
        resp = client.post(_actions_url(f"workflows/{workflow_file}/dispatches"),
                           headers=_HEADERS, json=body)
    resp.raise_for_status()


def latest_run(workflow_file: str) -> dict | None:
    """Newest run of the workflow (any event), with status/conclusion/url."""
    with httpx.Client(timeout=60) as client:
        resp = client.get(_actions_url(f"workflows/{workflow_file}/runs"),
                          headers=_HEADERS, params={"per_page": 1})
    resp.raise_for_status()
    runs = resp.json().get("workflow_runs", [])
    if not runs:
        return None
    r = runs[0]
    return {
        "id": r["id"],
        "status": r["status"],            # queued | in_progress | completed
        "conclusion": r["conclusion"],    # success | failure | cancelled | None
        "html_url": r["html_url"],
        "created_at": r["created_at"],
        "run_number": r["run_number"],
    }


def run_artifact(run_id: int, name: str) -> dict | None:
    """The named artifact of a run (or None). Avoids needing repo write access
    - the runner uploads the Excel as an artifact, which we then download."""
    with httpx.Client(timeout=60) as client:
        resp = client.get(_actions_url(f"runs/{run_id}/artifacts"), headers=_HEADERS)
    resp.raise_for_status()
    for a in resp.json().get("artifacts", []):
        if a["name"] == name and not a.get("expired"):
            return {"id": a["id"], "size": a["size_in_bytes"]}
    return None


def download_artifact_xlsx(artifact_id: int) -> bytes:
    """Download an artifact zip and return the first .xlsx inside it."""
    import io
    import zipfile
    url = _actions_url(f"artifacts/{artifact_id}/zip")
    with httpx.Client(timeout=120, follow_redirects=True) as client:
        resp = client.get(url, headers=_HEADERS)
        resp.raise_for_status()
        data = resp.content
    with zipfile.ZipFile(io.BytesIO(data)) as zf:
        for n in zf.namelist():
            if n.lower().endswith(".xlsx"):
                return zf.read(n)
    raise ValueError("Artifact neobsahuje .xlsx soubor.")
