"""The versioned production store: the current Draft + the immutable history
of published Snapshots, all in GitHub (via gh.py).

Model (confirmed by the product owner, the git-like one):
  - A SNAPSHOT is the complete, immutable planner state at publish time
    (a full workbook: config + accumulated state + the published plan). The
    latest snapshot is the single source of truth every new run resumes from.
  - The DRAFT is the one mutable working file. Every upload rebuilds it from
    the latest snapshot + this run's fresh exports; edits mutate it; Publish
    freezes it into a new snapshot. Uploading never touches any snapshot.

Layout in the repo:
  store/draft.xlsx                  the current working Draft (mutable)
  store/draft_meta.json             draft provenance (uploaded files, when)
  store/snapshots/index.json        the published-version history (audit)
  store/snapshots/<id>.xlsx         one immutable published snapshot each

Bootstrap: before the first publish, the "latest snapshot" is the proven
scaffold workbook already in the repo - a one-time seed from the current
Excel. After the first publish, snapshots are the only source of truth.
"""
from __future__ import annotations

import os
import tempfile

import gh

DRAFT_PATH = "store/draft.xlsx"
DRAFT_META_PATH = "store/draft_meta.json"
SNAPSHOT_DIR = "store/snapshots"
INDEX_PATH = f"{SNAPSHOT_DIR}/index.json"

# One-time bootstrap seed (the current Excel already committed in the repo).
BOOTSTRAP_WORKBOOK = os.environ.get(
    "WORKBOOK_PATH_IN_REPO", "workbook/FieldForceOptimizer_V11_scaffold.xlsx"
)


# ---- version index (published history) ------------------------------------

def read_index() -> list[dict]:
    """The published-version history, newest last. [] before the first
    publish."""
    return gh.read_json(INDEX_PATH, default=[]) or []


def _next_version_id(index: list[dict]) -> str:
    return f"v{len(index) + 1:04d}"


# ---- latest snapshot (source of truth) ------------------------------------

def latest_snapshot_repo_path() -> str:
    """Repo path of the snapshot every new run resumes from: the newest
    published snapshot, or the bootstrap workbook if nothing is published
    yet."""
    index = read_index()
    if index:
        return f"{SNAPSHOT_DIR}/{index[-1]['id']}.xlsx"
    return BOOTSTRAP_WORKBOOK


def download_latest_snapshot(dest_path: str) -> None:
    gh.download(latest_snapshot_repo_path(), dest_path)


# ---- draft ----------------------------------------------------------------

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


# ---- publish (freeze draft -> immutable snapshot) -------------------------

def publish_snapshot(local_snapshot_path: str, meta: dict) -> dict:
    """Commits `local_snapshot_path` as a new immutable snapshot and appends
    an audit record to the index. Returns the index record.

    `meta` carries the audit fields (publishedWeek, publishedBy, message,
    sourceFiles, engineVersion, ...); this adds the id and stored path."""
    index = read_index()
    version_id = _next_version_id(index)
    snapshot_repo_path = f"{SNAPSHOT_DIR}/{version_id}.xlsx"

    gh.upload_file(snapshot_repo_path, local_snapshot_path, f"Publish snapshot {version_id}: {meta.get('message', '')}")

    record = {"id": version_id, "path": snapshot_repo_path, **meta}
    index.append(record)
    gh.write_json(INDEX_PATH, index, f"Index: publish {version_id}")
    return record


def download_snapshot(version_id: str, dest_path: str) -> None:
    gh.download(f"{SNAPSHOT_DIR}/{version_id}.xlsx", dest_path)


def snapshot_temp(version_id: str | None = None) -> str:
    """Downloads a snapshot (or the latest) to a temp file and returns its
    path. Caller deletes it."""
    fd, path = tempfile.mkstemp(suffix=".xlsx")
    os.close(fd)
    if version_id:
        download_snapshot(version_id, path)
    else:
        download_latest_snapshot(path)
    return path
