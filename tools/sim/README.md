# End-to-end simulation harness

Runs the REAL compiled engine code (`office-scripts/*.ts`, not a
reimplementation) against a mock `ExcelScript.Workbook` seeded from real
production data. This is the closest verification possible without live
Excel - it exercises the actual deployed code path, including Excel-API-
shaped bugs (range sizing, column indices) that unit tests of `core.ts`'s
pure functions alone cannot catch.

## Usage

```bash
# 1. Seed state from a real workbook (e.g. the scaffold, or a real export
#    you've pasted into RAW_DATA/CONTROL/etc. of a copy of it):
python3 tools/sim/xlsx_to_json.py workbook/FieldForceOptimizer_V11_scaffold.xlsx tools/sim/state0.json

# 2. Run some or all engines in sequence against that state:
NODE_PATH=$(npm root -g) npx ts-node --transpile-only \
  --compiler-options '{"module":"commonjs","esModuleInterop":true,"moduleResolution":"node","ignoreDeprecations":"6.0"}' \
  tools/sim/run_e2e.ts tools/sim/state0.json "ImportEngine.ts,PlanningEngine.ts" tools/sim/state1.json

# Omit the engine-list argument to run the full default pipeline (Import ->
# Planning -> Publish -> Compliance -> Advisor -> Reporting):
NODE_PATH=$(npm root -g) npx ts-node --transpile-only \
  --compiler-options '{"module":"commonjs","esModuleInterop":true,"moduleResolution":"node","ignoreDeprecations":"6.0"}' \
  tools/sim/run_e2e.ts tools/sim/state0.json
```

Output state is written as JSON (sheet name -> array of arrays) to the path
given as the 4th argument (default `tools/sim/final_state.json`), and can be
fed back in as the seed for a further run - this is how a multi-week
scenario is simulated (e.g. run Compliance Engine twice with different
"latest known week" SalesApp data to verify the Plan Lifecycle actually
advances Published -> Active -> Closed correctly across runs, not just
within one).

`tools/sim/*.json` state dumps are gitignored - they are large (several MB,
since RAW_DATA has 11k+ rows) and fully regenerable, not source.

## What this caught

Running the full pipeline twice in sequence (simulating two weekly
Compliance Engine runs) surfaced a real correctness bug: `AdvisorEngine.ts`
computed technician/region failure rates from raw `COMPLIANCE_LOG` rows
without deduplicating to the latest evaluation per visit first. Since
`COMPLIANCE_LOG` is intentionally append-only (Compliance Engine re-
evaluates every published visit on every run), this diluted the failure
rate more with every weekly run - the opposite of what an overload alert
should do over time. Unit tests of the pure functions didn't catch it
because each function was individually correct; the bug was in how
`AdvisorEngine.ts` wired them together across multiple runs, which only a
multi-run end-to-end scenario exercises. Fixed by deduplicating with
`latestByKey` before computing rates, matching the pattern `ReportingEngine.ts`
already used correctly. See `docs/ARCHITECTURE.md` Phase 6 notes and the
regression test in `tests/core.test.ts`.
