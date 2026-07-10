"""GitHub-backed versioned store (legacy cloud path). Kept for the existing
Render deployment; the desktop app uses store_local.py (SQLite) instead.
store.py picks the backend by FFO_LOCAL. See DESKTOP_ARCHITECTURE.md.

Model (git-like):
  - A SNAPSHOT is the complete, immutable planner state at publish time. The
    latest snapshot is the single source of truth every new run resumes from.
  - The DRAFT is the one mutable working file, rebuilt on each upload from the
    latest snapshot + this run's fresh exports; Publish freezes it into a new
    snapshot. Uploading never touches any snapshot.
"""
from __future__ import annotations

import os
import tempfile

import gh

DRAFT_PATH = "store/draft.xlsx"
DRAFT_META_PATH = "store/draft_meta.json"
SNAPSHOT_DIR = "store/snapshots"
INDEX_PATH = f"{SNAPSHOT_DIR}/index.json"

BOOTSTRAP_WORKBOOK = os.environ.get(
    "WORKBOOK_PATH_IN_REPO", "workbook/FieldForceOptimizer_V11_scaffold.xlsx"
)


def read_index() -> list[dict]:
    return gh.read_json(INDEX_PATH, default=[]) or []


def _next_version_id(index: list[dict]) -> str:
    return f"v{len(index) + 1:04d}"


def latest_snapshot_repo_path() -> str:
    index = read_index()
    if index:
        return f"{SNAPSHOT_DIR}/{index[-1]['id']}.xlsx"
    return BOOTSTRAP_WORKBOOK


def download_latest_snapshot(dest_path: str) -> None:
    gh.download(latest_snapshot_repo_path(), dest_path)


def draft_exists() -> bool:
    return gh.exists(DRAFT_PATH)


def download_draft(dest_path: str) -> None:
    gh.download(DRAFT_PATH, dest_path)


def save_draft(src_path: str, message: str, meta: dict | None = None) -> None:
    gh.upload_file(DRAFT_PATH, src_path, message)
    if meta is not None:
        gh.write_json(DRAFT_META_PATH, meta, f"{message} (meta)")


def read_draft_meta() -> dict:
    return gh.read_json(DRAFT_META_PATH, default={}) or {}


def publish_snapshot(local_snapshot_path: str, meta: dict) -> dict:
    index = read_index()
    version_id = _next_version_id(index)
    snapshot_repo_path = f"{SNAPSHOT_DIR}/{version_id}.xlsx"
    gh.upload_file(snapshot_repo_path, local_snapshot_path,
                   f"Publish snapshot {version_id}: {meta.get('message', '')}")
    record = {"id": version_id, "path": snapshot_repo_path, **meta}
    index.append(record)
    gh.write_json(INDEX_PATH, index, f"Index: publish {version_id}")
    return record


def download_snapshot(version_id: str, dest_path: str) -> None:
    gh.download(f"{SNAPSHOT_DIR}/{version_id}.xlsx", dest_path)


def snapshot_temp(version_id: str | None = None) -> str:
    fd, path = tempfile.mkstemp(suffix=".xlsx")
    os.close(fd)
    if version_id:
        download_snapshot(version_id, path)
    else:
        download_latest_snapshot(path)
    return path
