# Field Force Optimizer — Distribution Client (V1 view/export + V2 local engines)

A small standalone desktop app with two clearly separated capabilities.

FieldForceOptimizer (the Excel workbook + Office Scripts) remains the
authoritative implementation of all planning business logic: cadence,
scoring, compliance, capacity, publication. V1 below never plans, never
optimizes, never reads `SALESAPP_IMPORT` or any import-stage sheet, and
never writes anything back to the source workbook. V2 (further down) is a
deliberate, documented exception - see `docs/ARCHITECTURE.md` section 22.

## V1: view/export (read-only)

It reads the already-published `TECHNICIAN_PLAN` sheet and gives you a
faster way to browse it and export a separate Excel file per technician.

1. Open a workbook — a local file, or a file inside a OneDrive-synced
   folder (from this app's point of view that's just a local file path;
   no OneDrive API, no live connection, no sync).
2. Browse the list of technicians and see the selected technician's
   weekly plan on screen.
3. One click: export a separate `.xlsx` per technician, named
   `<Technik>_<Rok>_W<Tyden>.xlsx` (e.g. `Novák_2026_W31.xlsx`), into a
   folder you pick.

No search/filter/print/history yet (see `docs/BACKLOG.md` for what may be
added later, all still read-only).

## V2: run Import/Planning/Publish locally (writes to the workbook)

The "Lokální spuštění enginů" panel at the top of the app runs a Python
port of the real `ImportEngine.ts`/`PlanningEngine.ts`/`PublishEngine.ts`
(`desktop_client/engines/`) directly against the open `.xlsx` file via
openpyxl - no Microsoft Graph API, no online sync (the project's "no
external API" constraint rules that out), so this is the only way a
desktop app can trigger these steps without opening Excel.

This is a genuine, documented exception to "the app never writes to the
workbook / never contains business logic" - approved explicitly by the
product owner after being shown the alternative (Graph API) and its own
cost. The Python engines are verified equivalent to the real TypeScript
engines on real production data and edge cases via
`tools/sim/compare_engines.py` (see `docs/ARCHITECTURE.md` section 22 for
the full methodology) - but Excel/Office Scripts remain the authoritative,
continuously-used implementation; this is a second, tested implementation
for the desktop app's convenience, not a replacement.

Safety measures built in:
- A timestamped backup of the whole file is made before every write.
- Only `POS_MASTER`/`MANAGER_PLAN`/`MANAGER_PLAN_PUBLISHED`/
  `PLAN_LIFECYCLE` are ever opened for writing - every other sheet
  (including any live formulas, e.g. `TECHNICIAN_PLAN`) is left untouched.
- Each run requires an explicit confirmation dialog naming the exact risk
  (writes to disk outside Excel; close the file in Excel first; formula-
  driven sheets go stale until the file is reopened/saved in real Excel).

Re-run `python3 desktop_client/engines/test_core_logic.py` and
`python3 tools/sim/compare_engines.py <ts_final.json> <py_final.json>`
(see that script's docstring) after any change to `desktop_client/engines/`
or to the corresponding `office-scripts/*.ts` files - this equivalence
check is not yet wired into CI, so keeping it green is a manual discipline.

## Two ways to run this

### A) As a standalone .exe (recommended for everyday use, Windows)

You don't need Python installed to *use* the app this way - only once, to
*build* the .exe:

1. One-time setup on a Windows PC: install Python from
   https://python.org (check "Add python.exe to PATH" during install).
2. Double-click `build_exe.bat` in this folder (or run it from a command
   prompt: `build_exe.bat`). It installs the required libraries
   (`openpyxl`, `ttkbootstrap`, `pyinstaller`) and packages the app.
3. The result is `dist\FieldForceDistributionClient.exe` - a single file.
   Copy it anywhere (Desktop, a shared drive, wherever) and run it with a
   double-click, on that PC or any other Windows PC - Python is no longer
   needed once this file exists.

Re-run `build_exe.bat` any time the `.py` files in this folder are
updated, to rebuild the `.exe` with the changes.

(Built and smoke-tested for Linux in this project's own dev environment to
confirm the packaging step itself - imports, hidden dependencies, the
GUI launching - works correctly; `pyinstaller` produces a native
executable for whichever OS it's run on, so running `build_exe.bat` on
Windows produces a genuine Windows `.exe`, not a Linux binary.)

### B) Running the Python source directly (any OS, for development/testing)

- Python 3.10+
- `openpyxl` and `ttkbootstrap` (`pip install openpyxl ttkbootstrap`)
- Tkinter — ships with the standard Windows/macOS Python installers; on
  Linux it may need a separate OS package (e.g. `sudo apt install
  python3-tk`).

```
pip install openpyxl ttkbootstrap
python3 distribution_client.py
```

## Important: why a freshly-generated workbook shows no technicians

`TECHNICIAN_PLAN`'s cells are live formulas (see
`tools/ux_style.py:build_technician_plan`), not stored values. This app
reads the **cached** value Excel calculated and saved the last time the
workbook was opened — that cache only exists once the workbook has
actually been opened (and saved) in real Excel at least once since the
last change. In the real weekly workflow this is always true: you publish
in Excel, save, then run this app on that saved file. A workbook that was
only ever generated by `tools/scaffold_workbook.py`/openpyxl and never
opened in Excel will show zero technicians — that's expected, not a bug.

## Files

- `plan_export.py` — V1 file-reading/writing logic, no GUI dependency,
  independently unit-tested.
- `test_plan_export.py` — run with `python3 test_plan_export.py`.
- `xlsx_engine_io.py` — V2 bridge between a real `.xlsx` file and the
  engine port (openpyxl read/write, backup-before-write, restricts writes
  to `ENGINE_OUTPUT_SHEETS`).
- `engines/` — the Python port of `core.ts`/`ImportEngine.ts`/
  `PlanningEngine.ts`/`PublishEngine.ts`. See
  `docs/ARCHITECTURE.md` section 22 for what each file does and how it's
  verified.
- `distribution_client.py` — the Tkinter GUI, imports from both
  `plan_export.py` (V1) and `xlsx_engine_io.py`/`engines/` (V2).

## Testing

```
cd desktop_client
python3 test_plan_export.py
python3 engines/test_core_logic.py
```

Full cross-language equivalence check (requires Node/ts-node, real or
seed workbook data):
```
python3 tools/sim/xlsx_to_json.py workbook/FieldForceOptimizer_V11_scaffold.xlsx tools/sim/state0.json
NODE_PATH=$(npm root -g) npx ts-node --transpile-only \
  --compiler-options '{"module":"commonjs","esModuleInterop":true,"moduleResolution":"node","ignoreDeprecations":"6.0"}' \
  tools/sim/run_e2e.ts tools/sim/state0.json "ImportEngine.ts,PlanningEngine.ts,PublishEngine.ts" tools/sim/final_state_ts.json
python3 -m desktop_client.engines.run_pipeline tools/sim/state0.json import,planning,publish tools/sim/final_state_py.json
python3 tools/sim/compare_engines.py tools/sim/final_state_ts.json tools/sim/final_state_py.json
```
