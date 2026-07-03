# Field Force Optimizer V11 — DATA_MODEL.md

Authoritative schema reference. Kept in sync with `office-scripts/shared/types.ts` and
`workbook/FieldForceOptimizer_V11_scaffold.xlsx`.

## POS_MASTER (the single persistent source of truth)

One row per POS, forever — never deleted, only marked Closed.

| Field | Source | Written by |
|---|---|---|
| posId, terminalId | RAW_DATA | Import Engine |
| market, category, terminalType, classification, nazev, area, posArea, street, houseNumber, city, gpsX, gpsY, assignedTechnician, ppt | RAW_DATA | Import Engine (overwritten wholesale each run) |
| status, closedSinceWeek, closedSinceYear | RAW_DATA presence/absence | Import Engine — present in this week's RAW_DATA = Active; missing = Closed (product owner, 2026-07-03, replaced the earlier POS_STATUS_IMPORT-driven rule; confirmed the weekly export always contains the full POS universe) |
| currentLosActivity, currentLotActivity, targetLosActivity, targetLotActivity | ACTIVITY_PLAN + VISIT_HISTORY | Planning Engine (not built yet) |
| lastRealVisitDate/Week, lastPlannedVisitDate, weeksSinceLastVisit, visitCountThisCampaign | VISIT_HISTORY | Compliance Engine (not built yet) |
| businessScore | CADENCE_RULES + SCORE_PROFILES + PARETO_GROUPS | Business Engine (not built yet) |
| plannerStatus, assignedWeek, assignedDay, gpsGroup | — | Decision/Route Engine (not built yet) |
| managerOverrideType, managerOverridePriority, managerOverrideTechnician, plannerNotes | manual, in Excel | **never** written by any engine — always preserved by Import Engine on upsert |
| importedAt, updatedAt | — | Import Engine |

`classification` = KATEGORIZACE (A/B/P). `area` = OBLAST (region name). `posArea` = POS AREA
(sales-area code, e.g. RSA) — these are two distinct RAW_DATA columns, not the same field.

## Config tables (six categories — see ARCHITECTURE.md §9)

**MARKET_RULES / TERMINAL_RULES / CATEGORY_RULES** — Filters. `CATEGORY_RULES` now has an
explicit `*` → `NORMAL` default row (was an implicit code fallback in V10.5.5, confirmed and
made visible per Phase 0 review).

**CADENCE_RULES** — unifies CORE / Mandatory / GECO / CORN.
`ruleId, scope, matchValue, minGapWeeks, maxIntervalWeeks, intervalType (RECURRING |
ONCE_PER_CAMPAIGN), guaranteeType (HARD | SOFT_HIGH_WEIGHT), dedupBy (NONE | ADDRESS),
campaignChangeOverride, priority, active, validFrom, validTo, notes`. GECO/CORN seeded as
`active=NO` placeholders — structure exists, values not yet confirmed (BUSINESS_RULES.md §15).

**PARETO_GROUPS** — `tierId, name, scope (PER_TECHNICIAN | GLOBAL | PER_REGION | PER_MARKET),
boundaryType, boundaryValue, active, notes`. Seeded with `PREMIUM_TOP20` at `PER_TECHNICIAN`
(preserves V10.5.5 behaviour exactly). KA/IDT tiers seeded inactive, pending thresholds.

**SCORE_PROFILES** — `profileId, component, weight, notes`. Seeded `DEFAULT` profile with
V10.5.5's exact magnitudes (CORE +1e8, KATEGORIZACE_A +1e7, PPT ×1, NEGLECTED_BONUS +50000) so
V11's first scoring pass can be diffed against V10.5.5 output before anything is retuned.

**ADVISOR_RULES** — structure only (`ruleId, type, condition, threshold, severity,
messageTemplate, active`), no rows yet — Advisor Engine not built.

**CAPACITY_OVERRIDE** — `technician, year, week, capacity`. Empty; used once Planning Engine's
capacity calculation exists (default = `workDays(week) × TARGET_VISITS_DAY`, overridable per
row here, no reason field, per product-owner decision).

## Import staging sheets (disposable — never read by any engine except Import Engine)

`RAW_DATA`, `ACTIVITY_PLAN` (unchanged, including the currently-unused `PRIORITY`/`OVERRIDE_GAP`
columns — imported and parsed by Import Engine, intentionally not referenced by any
scoring/filtering logic yet). `POS_STATUS_IMPORT` (`POS, ACTIVE`) still exists in the workbook but
is **no longer read by Import Engine** since 2026-07-03 — RAW_DATA presence/absence replaced it as
the sole Active/Closed signal (see POS_MASTER table above).

## Not yet in the data model

SalesApp import / real VISIT_HISTORY (actual visits, as opposed to V10.5.5's self-referential
planned-output log) — deferred until the SalesApp → LOS/LOT activity mapping is resolved
(BUSINESS_RULES.md).
