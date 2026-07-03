"""
Python analogue of tools/sim/run_e2e.ts: loads a seed JSON (same
{sheetName: [[cell,...],...]} shape produced by tools/sim/xlsx_to_json.py),
runs the Python engine port pipeline (Import -> Planning -> Publish) against
it, and writes the resulting full workbook state back out as JSON in the
same shape - so tools/sim/compare_engines.py can diff it directly against
the real TypeScript engines' output on the identical seed.

Usage: python3 -m desktop_client.engines.run_pipeline <seed.json> [pipeline] [out.json]
  pipeline: comma-separated subset of import,planning,publish (default: all three)
"""
from __future__ import annotations

import datetime
import json
import sys

from . import import_engine, planning_engine, publish_engine
from .mock_workbook import MockWorkbook


def _json_default(v):
    if isinstance(v, (datetime.datetime, datetime.date)):
        return v.isoformat()
    raise TypeError(f"Object of type {type(v)} is not JSON serializable")

ENGINES = {
    "import": import_engine.run,
    "planning": planning_engine.run,
    "publish": publish_engine.run,
}


def run_pipeline(seed: dict, pipeline: list[str]) -> tuple[dict, list[str]]:
    workbook = MockWorkbook(seed)
    log: list[str] = []
    for name in pipeline:
        if name not in ENGINES:
            raise ValueError(f"Unknown engine '{name}' - choose from {list(ENGINES)}")
        message = ENGINES[name](workbook)
        log.append(f"[{name}] {message}")
    return workbook.dump(), log


def main() -> None:
    if len(sys.argv) < 2:
        print("Usage: run_pipeline.py <seed.json> [pipeline] [out.json]")
        sys.exit(1)
    seed_path = sys.argv[1]
    pipeline = sys.argv[2].split(",") if len(sys.argv) > 2 else ["import", "planning", "publish"]
    out_path = sys.argv[3] if len(sys.argv) > 3 else "final_state_py.json"

    with open(seed_path, "r", encoding="utf-8") as f:
        seed = json.load(f)

    final_state, log = run_pipeline(seed, pipeline)

    for line in log:
        print(line)

    with open(out_path, "w", encoding="utf-8") as f:
        json.dump(final_state, f, ensure_ascii=False, default=_json_default)
    print(f"\nFinal state written to {out_path}")
    print("\n--- Row counts per sheet ---")
    for sheet, rows in final_state.items():
        print(f"  {sheet}: {len(rows)} rows")


if __name__ == "__main__":
    main()
