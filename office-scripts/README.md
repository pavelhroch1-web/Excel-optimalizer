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
- `shared/core.ts` is the authoritative, **unit-tested** version of Planning Engine's scoring/
  selection logic (pure functions, no `ExcelScript` dependency). `PlanningEngine.ts` contains a
  synced copy inlined into `main()`. Run `npx ts-node tests/core.test.ts` (requires `ts-node` +
  `typescript` available - `npm install -g ts-node typescript` if you don't have them) after any
  change to `core.ts`, and re-sync `PlanningEngine.ts` by hand before deploying. This is dev-only
  tooling; it does not run inside Excel.

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

## What's intentionally NOT here yet

Per the bottom-up build order agreed with the product owner: no Planning Engine, Advisor
Engine, Compliance Engine, or Route/Geo Engine yet - only the POS_MASTER foundation and Import
Engine. SalesApp import is deferred within Import Engine's own scope too (see the header
comment in `ImportEngine.ts`) because the SalesApp -> LOS/LOT activity mapping is still an open
business question (`docs/BUSINESS_RULES.md`).
