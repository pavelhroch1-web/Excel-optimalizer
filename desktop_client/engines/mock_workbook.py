"""
Python analogue of tools/sim/mockWorkbook.ts's minimal ExcelScript.Workbook
mock. Deliberately mirrors the same class shape (getWorksheet/getUsedRange/
getRangeByIndexes/setValues/clear) rather than a more "Pythonic" dict API,
so that porting an Office Script's main() body into a *_engine.py module is
a close, low-risk line-for-line translation instead of a re-design - the
translation itself is the highest-risk part of this port, so it is kept as
mechanical as possible.

Same seed/dump JSON shape as mockWorkbook.ts's constructor/dump(): a plain
{sheetName: [[cell, ...], ...]} dict, row 0 is the header row. This lets
tools/sim/compare_engines.py feed the exact same seed file to both the real
TypeScript engines (via tools/sim/run_e2e.ts) and this Python port.
"""
from __future__ import annotations

import re

CellValue = object  # str | int | float | bool
Row = list


def _parse_a1_start_row(a1: str) -> int:
    m = re.match(r"^[A-Z]+(\d+):", a1)
    if not m:
        raise ValueError(f'mock_workbook: cannot parse A1 range "{a1}"')
    return int(m.group(1)) - 1


class MockRange:
    def __init__(self, ws: "MockWorksheet", start_row: int, start_col: int, data: list[Row]):
        self.ws = ws
        self.start_row = start_row
        self.start_col = start_col
        self.data = data

    def get_values(self) -> list[Row]:
        return [list(r) for r in self.data]

    def get_row_count(self) -> int:
        return len(self.data)

    def set_values(self, values: list[Row]) -> None:
        for i, value_row in enumerate(values):
            target_row = self.start_row + i
            while len(self.ws.data) <= target_row:
                self.ws.data.append([])
            row = self.ws.data[target_row]
            for j, v in enumerate(value_row):
                col = self.start_col + j
                while len(row) <= col:
                    row.append("")
                row[col] = v

    def set_value(self, value) -> None:
        self.set_values([[value]])

    def clear(self, apply_to=None) -> None:
        # Matches mockWorkbook.ts: truncates from start_row onward. No cell-
        # formatting concept in this mock, so "contents" vs "all" are the same.
        del self.ws.data[self.start_row :]


class MockWorksheet:
    def __init__(self, initial: list[Row]):
        self.data: list[Row] = [list(r) for r in initial]

    def get_used_range(self) -> MockRange | None:
        if len(self.data) == 0:
            return None
        return MockRange(self, 0, 0, self.data)

    def get_range(self, a1: str) -> MockRange:
        start_row = _parse_a1_start_row(a1)
        return MockRange(self, start_row, 0, [])

    def get_range_by_indexes(self, start_row: int, start_col: int, num_rows: int, num_cols: int) -> MockRange:
        return MockRange(self, start_row, start_col, [])


class MockWorkbook:
    def __init__(self, seed: dict[str, list[Row]]):
        self.sheets: dict[str, MockWorksheet] = {name: MockWorksheet(rows) for name, rows in seed.items()}

    def get_worksheet(self, name: str) -> MockWorksheet:
        if name not in self.sheets:
            raise KeyError(f'mock_workbook: sheet "{name}" does not exist in the seed state')
        return self.sheets[name]

    def dump(self) -> dict[str, list[Row]]:
        return {name: [list(r) for r in ws.data] for name, ws in self.sheets.items()}
