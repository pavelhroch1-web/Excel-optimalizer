# office-scripts/

Deployment target: Excel's Office Scripts Code Editor (one script = one file you paste in).
Office Scripts do not support module imports across files, so this folder uses a convention:

- `shared/*.ts` is the **dev-source of truth** for logic reused across multiple scripts
  (text normalization, date/holiday math, column lookup, geo distance, shared types). Kept
  here for readability, review, and diffing against V10.5.5 - not deployed directly.
- Each top-level file (`ImportEngine.ts`, `PlanningEngine.ts`, and later `AdvisorEngine.ts`,
  `ComplianceEngine.ts`, `ReportingEngine.ts`) is the **actual deployable script**: it contains
  a copy of only the `shared/` pieces it needs, pasted at the top of `main()`, followed by that
  engine's own logic.
- `shared/core.ts` is the authoritative, **unit-tested** version of the Planning/Compliance
  scoring/selection/status logic (pure functions, no `ExcelScript` dependency). Deployable
  scripts contain a byte-identical copy inlined into `main()`, wrapped in
  `// SYNC-BLOCK-START: <name>` / `// SYNC-BLOCK-END: <name>` comment markers.
- **After any change to `shared/*.ts` or a deployable script's synced block**, run, in order:
  1. `npx ts-node tests/core.test.ts` — logic still correct (requires `ts-node` + `typescript`;
     `npm install -g ts-node typescript` if missing).
  2. `python3 tools/check_sync.py` — synced blocks are still byte-identical to `shared/*.ts`.
     This is not optional style-checking: duplicated helper code has already drifted twice in
     this project (a stale diacritics regex, a missing `norm()` call in an address-dedup key),
     both caught only by accident before this tool existed. Both commands are dev-only tooling;
     neither runs inside Excel.
- Not everything in a deployable script needs to be byte-identical to `shared/`: only the
  selection/scoring **algorithm** is synced. Reason-string presentation (`"PREMIUM | "`,
  `"GPS BONUS | "`, `"NEARBY | "`) is deliberately engine-specific glue code around the synced
  functions, not part of `core.ts` - see the file header comment in `PlanningEngine.ts`.

Why not just accept the duplication risk silently: when you change a shared helper, update it
in `shared/` first, then re-copy it into every deployable file that uses it. This is a manual
sync step, not automatic - flagged here explicitly so it doesn't quietly drift, which is the
main risk of this pattern.

## Deploying ImportEngine.ts

1. Open `workbook/FieldForceOptimizer_V11_scaffold.xlsx` in Excel (desktop or web, saved to
   OneDrive/SharePoint - required for Office Scripts to run).
2. Automate tab -> New Script -> paste the entire contents of `ImportEngine.ts`.
3. Run it against a workbook that has `RAW_DATA` populated (paste your weekly export) and,
   optionally, `POS_STATUS_IMPORT` / `ACTIVITY_PLAN` populated.
4. Check the `POS_MASTER` sheet - it should now have one row per POS from `RAW_DATA`, and the
   console log will report how many rows were upserted vs retained unchanged.

## Weekly deployment order

1. `ImportEngine.ts` — RAW_DATA/POS_STATUS_IMPORT/ACTIVITY_PLAN -> POS_MASTER.
2. `PlanningEngine.ts` — generates/regenerates Draft weeks in MANAGER_PLAN (never touches
   Published/Active/Closed weeks - see PLAN_LIFECYCLE).
3. Review/adjust MANAGER_PLAN manually (POS_MASTER overrides) as needed, re-run step 2.
4. `PublishEngine.ts` — explicit action, once you're ready to lock and send a week's plan.
   Publishes the earliest Draft week into MANAGER_PLAN_PUBLISHED.
5. Send the Published week's rows to technicians (outside this workbook, e.g. export/print).
6. Next cycle: import the new SalesApp export into SALESAPP_IMPORT, run `ComplianceEngine.ts`
   (compares only against MANAGER_PLAN_PUBLISHED, advances plan lifecycle, updates POS_MASTER).
7. `AdvisorEngine.ts` — run any time after Import/Compliance for fresh alerts.
8. `PerformanceEngine.ts` — run any time after Compliance for an updated `TECHNICIAN_PERFORMANCE_LOG`/
   `TECHNICIAN_PERFORMANCE_SUMMARY`/`TECHNICIAN_TOP_ISSUES` (feeds the manager UX layer:
   `TECHNICIAN_SCORECARD`/`PERFORMANCE`/`WEEK_DETAIL`).
9. `ReportingEngine.ts` — run any time for an updated DASHBOARD.

## What's intentionally NOT here yet

Per the bottom-up build order agreed with the product owner: no Planning Engine, Advisor
Engine, Compliance Engine, or Route/Geo Engine yet - only the POS_MASTER foundation and Import
Engine. SalesApp import is deferred within Import Engine's own scope too (see the header
comment in `ImportEngine.ts`) because the SalesApp -> LOS/LOT activity mapping is still an open
business question (`docs/BUSINESS_RULES.md`).
