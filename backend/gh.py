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

import httpx

GITHUB_TOKEN = os.environ["GITHUB_TOKEN"]
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
