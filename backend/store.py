"""Versioned store facade - picks the backend by runtime:

  - FFO_LOCAL=1 (desktop app)  -> store_local.py   (SQLite, the product)
  - otherwise (legacy Render)  -> store_gh.py      (GitHub Contents API)

The desktop application is the production direction (see
docs/DESKTOP_ARCHITECTURE.md); the GitHub path is kept only so the existing
cloud deployment doesn't break. Both expose the same interface, so main.py is
unchanged either way.
"""
from __future__ import annotations

import os

if os.environ.get("FFO_LOCAL") == "1":
    from store_local import (  # noqa: F401
        download_draft,
        download_latest_snapshot,
        download_snapshot,
        draft_exists,
        latest_snapshot_repo_path,
        publish_snapshot,
        read_draft_meta,
        read_index,
        save_draft,
        snapshot_temp,
    )
else:
    from store_gh import (  # noqa: F401
        download_draft,
        download_latest_snapshot,
        download_snapshot,
        draft_exists,
        latest_snapshot_repo_path,
        publish_snapshot,
        read_draft_meta,
        read_index,
        save_draft,
        snapshot_temp,
    )
