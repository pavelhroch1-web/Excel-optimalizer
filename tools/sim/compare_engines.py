"""
Cross-language equivalence check for the Distribution Client's local engine
port (desktop_client/engines/) against the real office-scripts/*.ts engines
- the safety net that replaces tools/check_sync.py's byte-identical-source
guarantee (which only works within TypeScript) now that the same business
logic is deliberately duplicated in Python too. See docs/ARCHITECTURE.md
"Desktop Client local engine execution".

Diffs two final_state.json dumps (same shape as tools/sim/run_e2e.ts /
desktop_client/engines/run_pipeline.py produce) sheet by sheet, ignoring:
  - row order (both engines are expected to produce the same SET of rows,
    not necessarily in the same order - e.g. dict/object iteration order
    over technicians is unspecified even though it happens to match today)
  - a fixed list of timestamp columns that legitimately differ because the
    two runs happen at different wall-clock instants (importedAt/
    updatedAt/publishedAt)
  - cosmetic date-cell representation differences ({"__date__": ...} vs a
    plain ISO string) that come from how each language's JSON dump
    happens to serialize a Date object, not from any business-logic change

Usage: python3 tools/sim/compare_engines.py <ts_final.json> <py_final.json>
Exit code 0 = equivalent, 1 = real differences found (printed to stdout).
"""
from __future__ import annotations

import json
import sys

# Column indices (0-based) known to hold a fresh-per-run timestamp, per sheet.
# Row 0 is the header row in every sheet here, so these indices apply to all
# data rows uniformly.
TIMESTAMP_COLUMNS = {
    "POS_MASTER": {37, 38},  # importedAt, updatedAt
    "MANAGER_PLAN_PUBLISHED": {17},  # publishedAt (appended 18th column, index 17)
    "PLAN_LIFECYCLE": {3},  # publishedAt/updatedAt-equivalent 4th column
}

SHEETS_TO_COMPARE = ["POS_MASTER", "MANAGER_PLAN", "MANAGER_PLAN_PUBLISHED", "PLAN_LIFECYCLE"]


def normalize_cell(v):
    if isinstance(v, dict) and "__date__" in v:
        return str(v["__date__"])[:10]
    if isinstance(v, str) and len(v) >= 10 and v[4] == "-" and v[7] == "-":
        return v[:10]
    if isinstance(v, float) and v.is_integer():
        return int(v)
    return v


def normalize_row(row: list, ignore_cols: set[int]) -> tuple:
    return tuple(
        "" if i in ignore_cols else normalize_cell(v)
        for i, v in enumerate(row)
    )


def compare_sheet(name: str, ts_rows: list, py_rows: list) -> list[str]:
    problems = []
    ignore_cols = TIMESTAMP_COLUMNS.get(name, set())

    ts_header, py_header = ts_rows[0] if ts_rows else [], py_rows[0] if py_rows else []
    if ts_header != py_header:
        problems.append(f"  HEADER MISMATCH: ts={ts_header} py={py_header}")

    ts_data = ts_rows[1:] if ts_rows else []
    py_data = py_rows[1:] if py_rows else []

    if len(ts_data) != len(py_data):
        problems.append(f"  ROW COUNT MISMATCH: ts={len(ts_data)} py={len(py_data)}")

    ts_set = {}
    for row in ts_data:
        key = normalize_row(row, ignore_cols)
        ts_set[key] = ts_set.get(key, 0) + 1
    py_set = {}
    for row in py_data:
        key = normalize_row(row, ignore_cols)
        py_set[key] = py_set.get(key, 0) + 1

    only_in_ts = []
    for key, count in ts_set.items():
        py_count = py_set.get(key, 0)
        if py_count < count:
            only_in_ts.append((key, count - py_count))
    only_in_py = []
    for key, count in py_set.items():
        ts_count = ts_set.get(key, 0)
        if ts_count < count:
            only_in_py.append((key, count - ts_count))

    if only_in_ts:
        problems.append(f"  {len(only_in_ts)} row(s) present in TS output but not Python (showing up to 5):")
        for key, n in only_in_ts[:5]:
            problems.append(f"    x{n}: {key}")
    if only_in_py:
        problems.append(f"  {len(only_in_py)} row(s) present in Python output but not TS (showing up to 5):")
        for key, n in only_in_py[:5]:
            problems.append(f"    x{n}: {key}")

    return problems


def main() -> None:
    if len(sys.argv) != 3:
        print(__doc__)
        sys.exit(1)
    with open(sys.argv[1], "r", encoding="utf-8") as f:
        ts_state = json.load(f)
    with open(sys.argv[2], "r", encoding="utf-8") as f:
        py_state = json.load(f)

    any_problem = False
    for sheet in SHEETS_TO_COMPARE:
        ts_rows = ts_state.get(sheet, [])
        py_rows = py_state.get(sheet, [])
        problems = compare_sheet(sheet, ts_rows, py_rows)
        if problems:
            any_problem = True
            print(f"=== {sheet}: DIFFERENCES FOUND ===")
            for p in problems:
                print(p)
        else:
            print(f"=== {sheet}: OK ({len(ts_rows) - 1 if ts_rows else 0} rows, equivalent) ===")

    if any_problem:
        print("\nRESULT: NOT equivalent - see differences above.")
        sys.exit(1)
    else:
        print("\nRESULT: equivalent (ignoring row order and timestamp columns).")
        sys.exit(0)


if __name__ == "__main__":
    main()
