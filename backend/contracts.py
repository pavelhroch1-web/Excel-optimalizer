"""Backend DTOs — the typed half of the frontend⇄backend contract.

Mirrors web/contracts.js and docs/API_CONTRACT.md. These Pydantic models are the
canonical Python shape of the API payloads; import endpoints build dicts that
conform to `ImportResult` (kept as dicts so the back-compat aliases `counts`/
`detected` ride along for older callers).
"""
from __future__ import annotations

from typing import Any

from pydantic import BaseModel, Field


class ImportResult(BaseModel):
    """Response of every import endpoint. See auto_import._result()."""
    ok: bool = Field(..., description="True only if data actually landed")
    kind: str = Field(..., description="pos_master|salesapp|activity_plan|tourplan|workbook|unknown")
    kindLabel: str = ""
    imported: dict[str, Any] = Field(default_factory=dict, description="table -> rows imported")
    total: int = 0
    warnings: list[str] = Field(default_factory=list)
    error: str | None = None
    file: str | None = None
    recomputed: list[str] = Field(default_factory=list)

    # back-compat aliases some older frontend paths still read
    detected: str | None = None
    counts: dict[str, Any] | None = None
    sheet: str | None = None


def validate_import_result(d: dict) -> bool:
    """Raise if a dict doesn't conform to ImportResult. Used in tests/dev to keep
    endpoints honest against the documented contract."""
    ImportResult(**d)
    return True
