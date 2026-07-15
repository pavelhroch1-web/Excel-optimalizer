"""Planner Phase 1 — predictive visit duration (collective, nationwide).

Predicts how long a visit to a given POS typically takes, so the planner can
size the working day realistically (fit more short visits, fewer long ones).

Design (per docs/PLANNER_ARCHITECTURE.md):
  * Collective learning — from the whole country's history, never a single
    technician by default. A hierarchy adds specificity only when there is
    enough data:  national → +category (POS type) → +chain → +region → +technician.
  * Extreme-trimming — durations are clipped to a sane band so data errors
    (0-minute pings, multi-hour artefacts) and outlier behaviour don't distort
    the model. The target is a normal-to-slightly-above-average technician.
  * Quantiles, not the mean — plan on p50 (typical), keep p75 as the buffer;
    duration is right-skewed so the mean would over-fill the day.
  * Shrinkage — each cell is blended toward its parent level, so a thin cell
    (few visits) leans on the broader, more reliable estimate.

Deterministic and transparent; no ML library, no LLM. Recomputable cache in
`duration_model` (rebuilt from history), separate from append-only history.
"""
from __future__ import annotations

import statistics

import db

_CLIP_LO, _CLIP_HI = 1.0, 120.0     # minutes — remove pings and artefacts
_SHRINK_K = 40.0                     # pseudo-count pulling a thin cell to parent
_MIN_N = 5                           # a cell needs at least this many to be used
_LEVELS = ["", "category", "market", "region", "technician"]  # dims added per level


def _load():
    """On-POS durations of real store visits, with their dimensions, trimmed."""
    rows = db.get(
        "SELECT p.category cat, p.market chain, v.region reg, v.technician tech, "
        "(julianday(v.finished_at)-julianday(v.started_at))*1440.0 mins "
        "FROM salesapp_visits v JOIN pos_master p ON p.pos_id=v.pos_id "
        "WHERE v.started_at IS NOT NULL AND v.finished_at IS NOT NULL")
    out = []
    for r in rows:
        m = r["mins"]
        if m is None or m < _CLIP_LO or m > _CLIP_HI:
            continue
        out.append((r["cat"] or "?", r["chain"] or "?", r["reg"] or "?", r["tech"] or "?", m))
    return out


def _key(dims):
    """Join dims dropping trailing blanks (parents omit the finer dims)."""
    return "|".join(str(x) for x in dims)


def _quantile(sorted_vals, q):
    if not sorted_vals:
        return None
    i = min(int(q * len(sorted_vals)), len(sorted_vals) - 1)
    return round(sorted_vals[i], 1)


def rebuild() -> dict:
    """Recompute the whole model from history and store it. Returns a summary."""
    data = _load()
    if not data:
        return {"rebuilt": False, "reason": "no duration data"}
    # gather values per cell at every level
    from collections import defaultdict
    levels = [defaultdict(list) for _ in _LEVELS]
    for cat, chain, reg, tech, m in data:
        dims = [cat, chain, reg, tech]
        levels[0][()].append(m)
        for lvl in range(1, 5):
            levels[lvl][tuple(dims[:lvl])].append(m)

    # national baseline (shrinkage anchor)
    nat = sorted(levels[0][()])
    nat_p50, nat_p75 = _quantile(nat, 0.5), _quantile(nat, 0.75)

    rows = [(0, "", len(nat), nat_p50, nat_p75)]
    shrunk = {(0, ()): (nat_p50, nat_p75)}     # cache parent shrunk estimates
    for lvl in range(1, 5):
        for cell, vals in levels[lvl].items():
            vals = sorted(vals)
            n = len(vals)
            raw50, raw75 = _quantile(vals, 0.5), _quantile(vals, 0.75)
            p_p50, p_p75 = shrunk.get((lvl - 1, cell[:-1]), (nat_p50, nat_p75))
            # empirical-Bayes shrinkage toward the parent level
            s50 = round((n * raw50 + _SHRINK_K * p_p50) / (n + _SHRINK_K), 1)
            s75 = round((n * raw75 + _SHRINK_K * p_p75) / (n + _SHRINK_K), 1)
            shrunk[(lvl, cell)] = (s50, s75)
            rows.append((lvl, _key(cell), n, s50, s75))

    db.run("DELETE FROM duration_model")
    for r in rows:
        db.run("INSERT INTO duration_model(level, ckey, n, p50, p75) VALUES(?,?,?,?,?)", r)
    return {"rebuilt": True, "cells": len(rows), "visits": len(data),
            "nationalP50": nat_p50, "nationalP75": nat_p75}


def _lookup(level, ckey):
    r = db.get("SELECT n, p50, p75 FROM duration_model WHERE level=? AND ckey=?", (level, ckey))
    return r[0] if r else None


def predict_for(category, market, region, technician) -> dict:
    """Most specific cell with enough data, else fall back up the hierarchy."""
    dims = [category or "?", market or "?", region or "?", technician or "?"]
    for lvl in range(4, -1, -1):
        ckey = _key(dims[:lvl]) if lvl else ""
        row = _lookup(lvl, ckey)
        if row and (lvl == 0 or (row["n"] or 0) >= _MIN_N):
            return {"p50": row["p50"], "p75": row["p75"], "n": row["n"],
                    "level": lvl, "levelName": _LEVELS[lvl] or "národní"}
    nat = _lookup(0, "")
    if nat:
        return {"p50": nat["p50"], "p75": nat["p75"], "n": nat["n"], "level": 0, "levelName": "národní"}
    return {"p50": None, "p75": None, "n": 0, "level": None, "levelName": None}


def _region_of(technician):
    if not technician:
        return None
    r = db.get("SELECT region FROM salesapp_visits WHERE technician=? AND region IS NOT NULL "
               "AND region<>'' GROUP BY region ORDER BY COUNT(*) DESC LIMIT 1", (technician,))
    return r[0]["region"] if r else None


def predict(pos_id: str) -> dict:
    """Predicted duration for a specific POS (uses its category / chain / region /
    assigned technician)."""
    p = db.get("SELECT category, market, technician FROM pos_master WHERE pos_id=?", (str(pos_id),))
    if not p:
        return {"pos": str(pos_id), "found": False}
    r = p[0]
    pred = predict_for(r["category"], r["market"], _region_of(r["technician"]), r["technician"])
    pred["pos"] = str(pos_id)
    pred["found"] = True
    return pred


def overview() -> dict:
    """National baseline + per-category typical durations, for the UI / audit."""
    nat = _lookup(0, "")
    cats = db.get("SELECT ckey, n, p50, p75 FROM duration_model WHERE level=1 ORDER BY n DESC LIMIT 20")
    return {"national": {"p50": nat["p50"], "p75": nat["p75"], "n": nat["n"]} if nat else None,
            "byCategory": [{"category": c["ckey"], "n": c["n"], "p50": c["p50"], "p75": c["p75"]} for c in cats],
            "clip": [_CLIP_LO, _CLIP_HI], "shrinkK": _SHRINK_K}
