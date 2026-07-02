"""
Small helpers that reproduce specific JavaScript coercion semantics used
throughout office-scripts/*.ts, so the engine ports in this package can be
close, low-risk translations instead of "what I think the JS meant."
Shared by import_engine.py / planning_engine.py / publish_engine.py.
"""
from __future__ import annotations


def at(row: list, idx: int):
    """Matches JS `row[idx]`: an out-of-range or negative index (e.g. -1 from
    a "column not found" lookup) yields `undefined`, NOT Python's
    negative-index-wraps-to-last-element behaviour."""
    if idx < 0 or idx >= len(row):
        return None
    return row[idx]


def s(v) -> str:
    """Matches JS String(x): null/undefined -> "", everything else -> str()."""
    if v is None:
        return ""
    return str(v)


def js_number(v) -> float:
    """Matches bare JS Number(x) (no `|| 0` fallback): undefined -> NaN,
    "" -> 0, a parseable numeric string/number -> that value, anything
    else non-numeric -> NaN. Callers that need isNaN-style fallback logic
    (e.g. PlanningEngine.ts's setting()) should check `v == v` (False only
    for NaN) themselves, exactly like the original `isNaN(v)` check."""
    if v is None:
        return float("nan")
    if v == "":
        return 0.0
    try:
        return float(v)
    except (TypeError, ValueError):
        return float("nan")


def num(v) -> float:
    """Matches JS Number(x) used as `Number(x) || 0` (the pattern used
    throughout these engines): non-numeric/empty/null all fall back to 0."""
    if v is None or v == "":
        return 0.0
    try:
        f = float(v)
        return f if f == f else 0.0  # f==f is False only for NaN
    except (TypeError, ValueError):
        return 0.0
