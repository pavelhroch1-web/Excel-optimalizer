"""Persists the real .xlsx workbook in this GitHub repo via the Contents
API, so the backend needs no paid disk - the free-tier host's filesystem is
treated as scratch space only. Every write becomes a real commit, giving
free versioned backups for free.

Deliberately minimal: two functions, no git CLI, no local clone - just
GET/PUT against api.github.com with a personal access token.
"""
from __future__ import annotations

import base64
import os

import httpx

GITHUB_TOKEN = os.environ["GITHUB_TOKEN"]
GITHUB_REPO = os.environ.get("GITHUB_REPO", "pavelhroch1-web/excel-optimalizer")
WORKBOOK_PATH_IN_REPO = os.environ.get(
    "WORKBOOK_PATH_IN_REPO", "workbook/FieldForceOptimizer_V11_scaffold.xlsx"
)
GITHUB_BRANCH = os.environ.get("GITHUB_BRANCH", "main")

_API_BASE = f"https://api.github.com/repos/{GITHUB_REPO}/contents/{WORKBOOK_PATH_IN_REPO}"
_HEADERS = {
    "Authorization": f"Bearer {GITHUB_TOKEN}",
    "Accept": "application/vnd.github+json",
    "X-GitHub-Api-Version": "2022-11-28",
}


def download_workbook(dest_path: str) -> None:
    """Fetches the current workbook bytes from GitHub and writes them to
    dest_path on the local (ephemeral) filesystem.

    GitHub's Contents API only inlines base64 `content` in its JSON response
    for files <= 1MB - this workbook is ~14MB, so the default JSON response
    silently omits `content` entirely (found 2026-07-11: every endpoint that
    calls this was failing on the real deployment, once past login, because
    of exactly this). For files between 1MB and 100MB, requesting the same
    endpoint with the `raw` media type returns the file's raw bytes directly
    instead of JSON - no separate Git Blobs API call needed."""
    raw_headers = {**_HEADERS, "Accept": "application/vnd.github.raw+json"}
    with httpx.Client(timeout=60) as client:
        resp = client.get(_API_BASE, headers=raw_headers, params={"ref": GITHUB_BRANCH})
        resp.raise_for_status()
        content = resp.content
    with open(dest_path, "wb") as f:
        f.write(content)


def upload_workbook(src_path: str, commit_message: str) -> None:
    """Commits the local file at src_path back to GitHub as the new
    workbook content. Requires the current blob SHA (fetched fresh here to
    avoid a stale-SHA conflict if two requests race)."""
    with httpx.Client(timeout=60) as client:
        resp = client.get(_API_BASE, headers=_HEADERS, params={"ref": GITHUB_BRANCH})
        resp.raise_for_status()
        current_sha = resp.json()["sha"]

        with open(src_path, "rb") as f:
            content_b64 = base64.b64encode(f.read()).decode("ascii")

        put_resp = client.put(
            _API_BASE,
            headers=_HEADERS,
            json={
                "message": commit_message,
                "content": content_b64,
                "sha": current_sha,
                "branch": GITHUB_BRANCH,
            },
        )
        put_resp.raise_for_status()
