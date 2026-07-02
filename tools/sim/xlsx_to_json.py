"""
Dumps every sheet of a workbook to JSON (sheet name -> array of arrays),
for the Node.js end-to-end simulation harness (tools/sim/run_e2e.ts) to load
as its mock ExcelScript.Workbook seed state. Dates are serialized as
"YYYY-MM-DD" strings (ExcelScript also returns JS Date objects for date
cells, which the mock reproduces on load).

Usage: python3 tools/sim/xlsx_to_json.py <workbook.xlsx> <output.json>
"""
import sys
import json
import datetime
import openpyxl


def cell_to_json(v):
    if isinstance(v, (datetime.datetime, datetime.date)):
        return {"__date__": v.isoformat()}
    return v


def main(in_path, out_path):
    wb = openpyxl.load_workbook(in_path, data_only=True)
    sheets = {}
    for ws in wb.worksheets:
        rows = []
        for row in ws.iter_rows(values_only=True):
            rows.append([cell_to_json(v) if v is not None else "" for v in row])
        # Trim fully-empty trailing rows (openpyxl sometimes over-reports used range)
        while rows and all(v == "" for v in rows[-1]):
            rows.pop()
        sheets[ws.title] = rows
    with open(out_path, "w", encoding="utf-8") as f:
        json.dump(sheets, f, ensure_ascii=False)
    print(f"Wrote {out_path}: {len(sheets)} sheets")
    for name, rows in sheets.items():
        print(f"  {name}: {len(rows)} rows")


if __name__ == "__main__":
    if len(sys.argv) != 3:
        print(__doc__)
        sys.exit(1)
    main(sys.argv[1], sys.argv[2])
