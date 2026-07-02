# Field Force Optimizer V11 — ARCHITECTURE.md

Status: **draft v1, ready for implementation of mechanisms; see BUSINESS_RULES.md §15 for value
decisions still pending**

## 1. Product framing

Not a Planner. A Decision Support System for Field Force management: recommends, evaluates,
explains — final decisions always made by the manager. Planning is one of five engines, not the
whole product.

## 2. Guiding quality attributes (in priority order)

1. Explainability / auditability — no black-box logic, every recommendation traceable
2. Config-driven extensibility — a new business rule should be a new config row, not new code
3. Long-term maintainability — favour readable, testable logic over clever/opaque algorithms
4. Simplicity of daily use — one weekly ritual, few screens touched regularly
5. Minimal external dependency — no APIs, no data sources beyond the four manual weekly imports

## 3. Platform decision

**Office Scripts (TypeScript) as the primary implementation language, VBA as a narrow, explicitly
bounded escape hatch, Excel Power Query / Power Pivot (Data Model) as the historical/analytical
layer.** No database, no cloud service beyond the existing Microsoft 365 tenant (OneDrive/
SharePoint), no installed application, no external APIs.

Rationale (full reasoning in conversation record; summary here):
- Office Scripts chosen over VBA as default because business logic here (scoring, cadence
  evaluation, compliance comparison) benefits materially from real testability — pure TypeScript
  functions can be unit-tested outside Excel, which VBA cannot do practically. This directly
  serves quality attribute #3.
- VBA reserved for exactly two purposes: (a) a custom modal dialog for manual overrides, if native
  table editing proves uncomfortable in practice, and (b) annual archive-file rollover + Power
  Pivot Data Model refresh, because Office Scripts has no API for the Data Model.
- Power BI and Power Automate deliberately out of scope for V11 (confirmed) — may be reconsidered
  later for Reporting Engine, not required now.
- Reassessed after every major scope addition (geo-aware weekly composition, Advisor trend
  analysis) — conclusion unchanged each time: nothing added crosses the threshold where Excel
  stops being the right tool. The one scaling risk identified (per-technician portfolio growth)
  is already bounded by the candidate-pool-buffer mechanism (§6), not by portfolio size.

## 4. Macro pipeline

```
IMPORT ENGINE → POS_MASTER → PLANNING ENGINE → COMPLIANCE ENGINE → REPORTING
                     ↕                ↕
                ADVISOR ENGINE ←──────┘   (reads POS_MASTER + COMPLIANCE_LOG + SCORE_LOG,
                                            writes ADVISOR_LOG only — never writes the plan)
```

POS_MASTER is the single persistent source of truth. Imports only update it. Both Planning Engine
and Advisor Engine read it; only Planning Engine (via the Plan lifecycle, §7) produces a plan.

## 5. Engine responsibilities

**Import Engine** — upserts RAW_DATA/PPT/ACTIVITY_PLAN/POS_STATUS/SalesApp into POS_MASTER and
VISIT_HISTORY. Never overwrites manual fields. Idempotent on SalesApp visit UID (safe to
re-import overlapping weeks). Import sources are disposable staging sheets, never read by any
other engine.

**Planning Engine** (internal stages, all portfolio-scoped — one technician's POS at a time, no
cross-technician competition):
```
Campaign Engine   → campaign state per POS (current/target LOS/LOT, combine-opportunity flag)
Business Engine    → Business Score + structured breakdown (SCORE_LOG), pure scoring, no filtering
Candidate Engine    → eligible pool (Filters + Cadence gates), no scoring
Decision Engine      → HARD cadence reservations first, then scored selection within
                       capacity(technician, week), applying Campaign Economics timing
Route/Geo Engine       → candidate-pool buffering + geo clustering shape the week's composition
                         (not just a final tie-break — see BUSINESS_RULES.md §7); sequences days
```
Runs on a rolling horizon (`PLANNING_HORIZON_WEEKS`, default proposed = `CAMPAIGN_LENGTH`): only
the nearest week becomes binding at Publish; the rest is a provisional, fully-recomputed forecast.

**Advisor Engine** — diagnostic only, never plans. Evaluates `ADVISOR_RULES` against current
POS_MASTER state and COMPLIANCE_LOG trends (`TREND_WINDOW_WEEKS`). Runs automatically at every
Refresh, independent of whether Generate Plan is run.

**Compliance Engine** — compares the immutable `plannedVisit` snapshot (taken at Publish) against
`actualVisit` (from SalesApp import) for the closed week. Produces states, writes POS_MASTER
current-compliance fields and COMPLIANCE_LOG (append-only).

**Reporting Engine** — read-only aggregation over POS_MASTER / COMPLIANCE_LOG / SCORE_LOG /
ADVISOR_LOG into Dashboard and periodic reports. Computes nothing new.

## 6. Worked example — why GECO/CORN can safely be HARD guarantees

Technician "Rek Lubomír": 531 POS in portfolio, 288 CORE-ish (category prefix `1`), 11 GECO,
capacity ~40/week. A HARD reservation for 11 GECO POS in the week their deadline falls due is
negligible against 40 slots. The same is not true for CORE (288 POS) — hardcoding a deadline for
all of them would exceed capacity many times over, which is why CORE is proposed as
SOFT_HIGH_WEIGHT rather than HARD (see BUSINESS_RULES.md §3).

## 7. Plan lifecycle (state machine)

```
Draft (freely regenerated) → Review (manual edits, still freely regenerated)
  → Published (plannedVisit snapshot frozen; this is the lock point, not Generate Plan, not a
     day-of-week cutoff) → Active (week in progress; manual amendments allowed, recorded as
     timestamped deltas, not silent rewrites) → Closed (compliance evaluated, permanent record)
```

## 8. POS_MASTER schema

```
Identity:            posId, terminalId
Imported (RAW_DATA):  market, category, terminalType, classification, area, posArea, address,
                       gpsX, gpsY, assignedTechnician, ppt
Status:               status (Active|Closed), closedSinceWeek/Year
Campaign state:        currentLosActivity, currentLotActivity, targetLosActivity,
                       targetLotActivity, campaignGapStatus
Visit facts:           lastRealVisitDate/Week, lastPlannedVisitDate, weeksSinceLastVisit,
                       visitCountThisCampaign
Scoring:               businessScore, scoreBreakdown (→ SCORE_LOG)
Decision metadata:      plannerStatus, assignedWeek/Day, gpsGroup
Manual layer:           managerOverride (force include/exclude/priority/technician), plannerNotes
```

## 9. Configuration taxonomy (six categories — every future rule fits one of these)

```
1. FILTERS         TERMINAL_RULES, CATEGORY_RULES (with explicit default row), MARKET_RULES
2. CADENCE RULES    CADENCE_RULES (unifies CORE/GECO/CORN/Mandatory)
3. PARETO/CLASS      PARETO_GROUPS (defines KA/IDT-above-threshold/Pareto tiers)
4. SCORE PROFILES     SCORE_PROFILES + SEASONAL_STRATEGY (objective switching over time)
5. ADVISOR RULES       ADVISOR_RULES (thresholds, severity, message templates)
6. EXCEPTIONS            POS_MASTER manual fields — always highest priority
```

## 10. Sheet inventory (target: keep the weekly ritual to 3-4 screens)

```
Touched weekly:   IMPORT (staging), POS_MASTER, MANAGER_PLAN + Advisor panel, DASHBOARD
Touched rarely:   CONFIG (multiple tables per sheet, grouped by taxonomy above),
                  CAPACITY_OVERRIDE
System-managed:   VISIT_HISTORY, SCORE_LOG, COMPLIANCE_LOG, ADVISOR_LOG, TECHNICIAN_PLAN (per
                  technician, generated), annual archive files (VISIT_HISTORY_<year>.xlsx, etc.)
```

## 11. Historical/analytical scaling strategy

"Hot" workbook stays bounded (POS_MASTER ~12-20k rows even after years). Append-only logs
(VISIT_HISTORY, SCORE_LOG, COMPLIANCE_LOG) are rolled into annual archive files by the VBA
year-end routine; Power Query combines current + archived files; Power Pivot Data Model powers
Dashboard/Advisor trend queries across years without any single file growing unbounded. This is
what makes the Office Script execution-time limit acceptable long-term — engines only ever operate
on the current-year "hot" window, not full history.

## 12. Migration plan from V10.5.5

### Phase 0 — Code review complete. V10.5.5 → V11 map.

Full script reviewed. Every function classified below. Nothing in "ZAHAZUJE SE" is deleted because
it's disliked — each entry states the reason it's superseded, per the "ask why before removing"
principle.

| V10.5.5 element | Classification | Notes |
|---|---|---|
| `norm()` | **ZŮSTÁVÁ** | Diacritics-safe string matching, used everywhere, no reason to change |
| `setting()` | **ZŮSTÁVÁ** (as CONFIG reader) | Generalize to read all six config categories, not just CONTROL |
| `isoMonday()`, `easter()`, `isHoliday()`, `workDays()` | **ZŮSTÁVÁ** | Exactly the capacity date-engine agreed for V11; move under Planning Engine capacity calc |
| `exactCol()` / `col()` | **ZŮSTÁVÁ** | Dynamic column mapping is a real strength, keep both (exact for stable fields, fuzzy for tolerant ones) |
| `distance()` | **ZŮSTÁVÁ** | Reasonable lat/long-to-km approximation for this latitude band; no need for haversine at this scale |
| `categoryRule()` | **REFAKTORUJE SE** | Logic (table lookup + `1*` → CORE default) moves into Filters/Cadence layer; default becomes an explicit CATEGORY_RULES row instead of hardcoded fallback |
| Score formula (CORE/A/PTT/gap constants) | **REFAKTORUJE SE** | Same relative ordering (CORE > A > PTT > gap), reimplemented as configurable SCORE_PROFILES weights instead of magic constants |
| Gap logic (`PREMIUM_GAP`/`STANDARD_GAP`/`NEGLECTED_AFTER`) | **REFAKTORUJE SE** | Becomes the minimum/recommended/critical period concept in Cadence Rules; campaign-change override behaviour explicitly preserved |
| `mandatoryPodnik()` | **REFAKTORUJE SE** | Becomes one CADENCE_RULES entry (HARD, explicit-list scope); street+city dedup-by-best-PTT preserved as documented behaviour, not silently dropped |
| PREMIUM top-20% (`groups[tech]` relative ranking) | **REFAKTORUJE SE — scope decision pending** | Mechanism kept, but PER_TECHNICIAN vs GLOBAL/PER_REGION scope is an open decision (BUSINESS_RULES.md §4) before finalizing PARETO_GROUPS |
| `campaignChangeSoon()` | **REFAKTORUJE SE** | Kept as the seed of Campaign Economics, extended from single-week reorder to full rolling-horizon combine logic |
| `addNearby()` (GPS EXTRA) | **REFAKTORUJE SE — fix approved** | Core idea (over-capacity nearby bonus) preserved; capacity-overflow defect fixed so selection never exceeds physical day slots (confirmed by product owner) |
| `geoDays()` | **REFAKTORUJE SE** | Becomes the geo-clustering step of Route/Geo Engine; extended to buffer-pool-then-cluster |
| `CANDIDATE_POOL=1.3` (CONTROL setting) | **CORRECTION — dead config, not existing behaviour** | Earlier review incorrectly claimed this setting was already wired into GPS selection. Re-checked: `setting("CANDIDATE_POOL", ...)` is never called anywhere in the script. The buffer-pool-then-cluster mechanism for V11 is therefore **NOVĚ VZNIKÁ**, not a refactor of working code — it reuses the *name* of an unused config field, nothing more |
| `ACTIVITY_PLAN.PRIORITY` / `.OVERRIDE_GAP` columns | **DEAD IN CODE — imported, stored, not yet used** | Not treated as proven existing behaviour (code never read them, so no working mechanism to preserve). Decision: import both into the data model unchanged, but Planning Engine / Business Score must not reference them yet. Reserved as a future, explicitly optional extension point — likely candidates: PRIORITY → campaign weight in SCORE_PROFILES, OVERRIDE_GAP → per-campaign exception to minimum visit period. Not designed further until product owner decides to activate it. |
| `selectWeekPOS()` | **REFAKTORUJE SE** | Splits across Candidate Engine (eligibility) + Decision Engine (selection under capacity) instead of one function doing both |
| `katCols[1]` positional KATEGORIZACE lookup | **ZAHAZUJE SE** | Fragility, not a business rule — replaced with `exactCol("KATEGORIZACE")` |
| Single `main()` doing everything | **ZAHAZUJE SE** | Replaced by the five-engine pipeline (§4/§5); no behaviour lost, only structure |
| GECO / CORN handling | **NOVĚ VZNIKÁ** | Did not exist in V10.5.5 at all |
| Advisor Engine (all alert types) | **NOVĚ VZNIKÁ** | No equivalent in V10.5.5 |
| Compliance Engine (plan vs. SalesApp actuals) | **NOVĚ VZNIKÁ — critical, not cosmetic** | V10.5.5 does not import SalesApp at all. `VISIT_HISTORY` is populated from the script's own generated `output`, i.e. it records what was *planned*, not what actually happened. Gap calculations (`lastVisit`) therefore drift from reality over time with no correction mechanism — Compliance Engine closes a real, currently-missing feedback loop, not just an enhancement |
| POS_MASTER as persistent master record | **NOVĚ VZNIKÁ** | V10.5.5 recomputes everything from RAW_DATA each run; no persistent derived state |
| SEASONAL_STRATEGY / SCORE_PROFILES | **NOVĚ VZNIKÁ** | Score today is one fixed formula, not swappable |
| CAPACITY_OVERRIDE (dynamic capacity) | **NOVĚ VZNIKÁ** | V10.5.5 capacity is purely `workDays() × TARGET_DAY`, no manual override table |
| Plan lifecycle (Draft/Published/Active/Closed) | **NOVĚ VZNIKÁ** | V10.5.5 has no concept of a plan state — it just overwrites OUTPUT_PLAN each run |
| Manual override layer in POS_MASTER | **NOVĚ VZNIKÁ** | No manual-edit concept exists today |

1. **Phase 0** — complete (table above).
2. **Phase 1** — build POS_MASTER + Import Engine standalone, reading the same sources V10.5.5
   already uses, without touching OUTPUT_PLAN generation. Zero risk to production.
3. **Phase 2** — port scoring into Business Engine with configurable weights; run in shadow mode
   against V10.5.5's existing REASON-tag output for comparison.
4. **Phase 3** — swap in Decision/Route Engine for real, run MANAGER_PLAN/TECHNICIAN_PLAN
   side-by-side with legacy OUTPUT_PLAN for one full campaign cycle before sign-off.
5. **Phase 4** — retire legacy generation path.

## 13. Risks carried into implementation

- Several BUSINESS_RULES.md items marked ★ OPEN affect Business Engine scoring output directly
  (GECO/CORE/KA/IDT/Pareto scope and thresholds) — by agreement these are treated as config values
  tuned during implementation, not blockers, but must be set deliberately before go-live, not left
  at placeholder defaults.
- GPS-extra capacity-overflow defect (BUSINESS_RULES.md §15a) — confirmed likely unintended,
  needs explicit sign-off before the fix changes production-visible behaviour (fewer silently
  "lost" POS, but potentially different weekly visit counts than V10.5.5 produced historically).
- PER_TECHNICIAN vs GLOBAL Pareto scope (BUSINESS_RULES.md §15a) is a real behavioural fork, not a
  cosmetic config value — needs a deliberate decision, not a default guess, before Business Engine
  scoring is finalized.
- Manual macro-security policy (VBA) confirmed enabled — no further action needed.
- POS number reuse after closure — not yet confirmed; if numbers are ever recycled, POS_MASTER
  history would silently merge two physical locations. Needs a yes/no answer before Import Engine
  upsert logic is finalized.
- SalesApp export mixes Technik and OZ roles (56 distinct executors seen vs. 27 technicians) —
  Import Engine's visit-history mapping needs an explicit role filter, not yet defined.
- SalesApp → LOS/LOT activity mapping not yet confirmed (purpose columns seen so far describe
  visit stage, not clearly which campaign/product was serviced).

## 13a. Implementation status

- Import Engine (`office-scripts/ImportEngine.ts`) — done, tested against real production data.
- Planning Engine v1 (`office-scripts/PlanningEngine.ts`) — done, tested against real production
  data (see verification notes in the commit history / conversation record). Covers Filters,
  Cadence (CORE + config-driven Mandatory), Pareto (PER_TECHNICIAN), campaign hold-back, GPS
  bonus (corrected spec), capacity (dynamic + override), MANAGER_PLAN output. Simplifications and
  deferred pieces tracked in `docs/BACKLOG.md`, not hidden.
- Compliance Engine v1 (`office-scripts/ComplianceEngine.ts`) — done, tested (unit tests in
  tests/core.test.ts for the pure ISO-week/status logic, plus a simulation against the real
  SalesApp export). Imports SalesApp, appends VISIT_HISTORY_ACTUAL (dedup by UID), matches against
  MANAGER_PLAN by POS+week, writes COMPLIANCE_LOG, updates POS_MASTER's real last-visit fields.
  Per-visit LOS/LOT campaign attribution deliberately NOT implemented - blocked on missing
  structured data in the SalesApp export, see BUSINESS_RULES.md.
- Advisor Engine v1 (`office-scripts/AdvisorEngine.ts`) — done, tested (11 new unit tests +
  simulation against real technician distribution). Three alert types: NEGLECT_RISK (two-tier,
  from POS_MASTER), TECHNICIAN_OVERLOAD and REGIONAL_UNDERPERFORMANCE (from COMPLIANCE_LOG).
  Diagnostic only - never writes to MANAGER_PLAN or POS_MASTER decision fields. All alert
  thresholds are proposed defaults in CONTROL, explicitly flagged as tunable, not confirmed
  business rules - see docs/BUSINESS_RULES.md section 13 and BACKLOG.md.
- Reporting Engine v1 (`office-scripts/ReportingEngine.ts`) — done, tested (3 new unit tests for
  the `latestByKey` dedup helper, which matters because COMPLIANCE_LOG/ADVISOR_LOG are append-
  only and can hold several evaluations of the same subject over time). Writes DASHBOARD: network
  overview, compliance summary, technician KPI, most-recent Advisor alert counts. Computes nothing
  new - pure aggregation over data the other engines already produced.
- Route/Geo Engine refinement — not started, see BACKLOG.md.
- **Plan lifecycle (Draft/Published/Active/Closed)** — done, approved and implemented.
  `office-scripts/PublishEngine.ts` is a new deployable script (the explicit "Publish" action):
  finds the earliest Draft week, snapshots it into MANAGER_PLAN_PUBLISHED (new sheet, append-
  only, immutable), marks it Published in PLAN_LIFECYCLE (new sheet). PlanningEngine.ts now
  never regenerates a locked (Published/Active/Closed) week - existing rows for that week are
  carried through byte-for-byte, and POS already committed to a locked week are excluded from
  that technician's candidate pool for other weeks. ComplianceEngine.ts now reads
  MANAGER_PLAN_PUBLISHED exclusively (never MANAGER_PLAN) and, after each run, advances
  Published->Active->Closed per week using `core.ts`'s `advanceLifecycleStatus` (tested,
  including a monotonic-time edge case found by exhaustive case enumeration during review: once
  a week reaches Active it must never regress to Published).

## 13b. End-to-end verification (Phase 6 follow-up)

Built `tools/sim/` - a harness that compiles and runs the real engine source
files against a mock `ExcelScript.Workbook` seeded from real production data
(`RAW_DATA`: 11,605 POS, 27 technicians), chaining engines exactly as a
manager would in Excel: Import -> Planning -> Publish -> Compliance ->
Advisor -> Reporting. Confirmed:
- The full pipeline runs cleanly end-to-end from a fresh workbook with no
  manual intervention, including graceful no-op handling at every stage
  when upstream data (e.g. SalesApp) isn't present yet.
- Output matches known real V10.5.5 production output for a spot-checked
  row (POS 81902616 / Myslivec Jan / week 31 / PPT 369174.88).
- Multi-run simulation (two sequential Compliance Engine runs, one week
  apart) confirmed Plan Lifecycle correctly advances Published -> Closed
  once all planned visits resolve, and caught a real bug: **AdvisorEngine.ts
  computed technician/region failure rates from undeduplicated
  COMPLIANCE_LOG rows**, which would have silently diluted overload
  detection more with every weekly Compliance Engine run in real operation.
  Fixed (dedupe via `latestByKey` before rate calculation, matching
  `ReportingEngine.ts`'s existing correct pattern) and covered by a
  regression test. This is exactly the class of bug unit tests of
  individually-correct pure functions cannot catch - it only appears when
  multiple runs are chained, which is what `tools/sim/` is for.

## 13c. Workbook UX layer (Phase 7)

`tools/ux_style.py`, applied automatically at the end of `scaffold_workbook.py`.
Pure presentation - no business logic, no data model change, no engine
touched except a verified-safe read-order fix inside the styling script
itself. Delivers:

- **Sheet organization**: reordered and tab-colored by role - Input (blue),
  Config (amber), Core/Output (purple/green), Logs (grey), Dashboard/
  START_HERE first. See `SHEET_GROUPS` in the script.
- **Color coding**: editable cells (cream) vs. system-managed (grey) per
  sheet, with a legend on START_HERE. Import-staging sheets (RAW_DATA,
  POS_STATUS_IMPORT, SALESAPP_IMPORT) get a flat "paste zone" wash; outputs
  and logs get their own distinct washes.
- **Data validation dropdowns** on every YES/NO, enum-like, and override
  field across the config sheets and POS_MASTER's manual override columns.
- **Real cell locking + sheet protection** - but ONLY on sheets no engine
  ever writes to (pure config: CONTROL, *_RULES, CADENCE_RULES,
  PARETO_GROUPS, SCORE_PROFILES, ADVISOR_RULES, CAPACITY_OVERRIDE,
  ACTIVITY_PLAN). Engine-writable sheets (POS_MASTER, MANAGER_PLAN*,
  PLAN_LIFECYCLE, COMPLIANCE_LOG, ADVISOR_LOG, VISIT_HISTORY_ACTUAL,
  DASHBOARD) deliberately get NO real protection, because Excel's Protect
  Sheet blocks Office Scripts' Range.clear()/setValues() unless the script
  explicitly unprotects first, which none of ours do - enabling it there
  would break every engine on first run. This trade-off is stated in the
  legend, not hidden.
- **START_HERE**: installation steps, weekly workflow, sheet map, current
  campaign config snapshot, legend.
- **ACTIVITY_PLAN redesign**: the original A:F data table is left at its
  exact position (ImportEngine.ts still reads it positionally) - new
  columns G onward add a live per-campaign visit-count estimate (Excel
  formula, recalculates instantly on edit, explicitly labeled "orientační"
  since it estimates network capacity during the campaign window, not the
  exact scored/selected POS count) and a Gantt-style timeline heatmap
  (conditional formatting, one column per week, colored when a campaign's
  START_WEEK..END_WEEK covers that week) so overlapping/consecutive
  campaigns are visible at a glance without opening a chart.

Found and fixed one real inefficiency while building this: the timeline
row-count was read from `ws.max_row` after decorative pre-styling had
already inflated it (styling 500 future rows registers them in the sheet),
producing ~12,000 conditional-formatting rules for 2 real campaign rows.
Fixed by computing the real row count from actual column-A content and by
reordering the styling passes; verified down to the expected 48 rules.

Verified with `tools/sim/`: full Import -> Planning -> Publish pipeline
against the styled workbook produces identical row counts to the
unstyled version (11,605 POS, 4,847 visits, 1,215 published) - the UX
pass changes no value an engine depends on.

## 13d. Workbook UX redesign (Phase 8 - "this should feel like an app")

Product owner feedback on Phase 7: technically solid but still read as "a
pretty Excel with scripts," not an application. Rebuilt around one
question - can a regional manager who has never seen this before
understand what to do within 30 seconds?

- **HOME** replaces START_HERE as sheet 1 and the workbook's default
  active sheet: banner, a live "this week" status strip (campaign week,
  POS count, planned-visit count - plain formulas against CONTROL/
  POS_MASTER/MANAGER_PLAN), six numbered workflow cards with one-click
  `HYPERLINK` navigation to the relevant sheet (or a plain "⚙ Automatizace"
  label for the two steps that are a script action, not a sheet - a
  self-linking button there would have been confusing, caught during
  review), a quick-nav button row, and the color legend inline near the
  top - not an appendix.
- **Hid 16 of 24 sheets** (all pure config, logs, and the two internal plan
  sheets) via `sheet_state = "hidden"`. Confirmed hidden sheets remain
  fully readable/writable by Office Scripts (only invisible in the tab
  bar), so no engine behaviour changes. Visible set is now HOME, DASHBOARD,
  TECHNICIAN_PLAN, POS_MASTER, ACTIVITY_PLAN (the 5 daily-use sheets) plus
  RAW_DATA/POS_STATUS_IMPORT/SALESAPP_IMPORT (necessary weekly paste
  targets, can't be hidden and still be pasteable, communicated as
  "utility" on HOME rather than part of the core 5).
- **New TECHNICIAN_PLAN sheet** (explicit product-owner request): a live
  formula view of MANAGER_PLAN's first 12 columns only (WEEK, DATE, DAY,
  TECHNICIAN, POS, KATEGORIE, NAZEV PROVOZOVNY, ULICE, ČÍSLO POPISNÉ/
  ORIENTAČNÍ, MĚSTO, OBLAST, POS AREA - the exact set/labels requested),
  with AutoFilter dropdowns, banded rows, no PPT/REASON/GPS_GROUP detail
  clutter. Pure presentation - no engine change; stays in sync automatically
  whenever Planning Engine regenerates MANAGER_PLAN. Found and documented a
  real limitation while building it: the view uses a static 3000-row
  formula range, which is fine now but will need revisiting once MANAGER_PLAN's
  unbounded growth (see BACKLOG.md) actually reaches that scale.
- **DASHBOARD KPI tiles**: four large numbers (Active POS, Splněno včas,
  Nesplněno, Otevřené alerty) at fixed, pre-styled positions (B3:E3),
  ReportingEngine.ts writes into them directly alongside the existing
  detail tables (now starting at row 5 instead of row 1). Required two
  related engine fixes, both found while building this:
  1. Changed every full-sheet `Range.clear()` (ImportEngine.ts,
     PlanningEngine.ts, ReportingEngine.ts) to
     `clear(ExcelScript.ClearApplyTo.contents)` - a bare `clear()` also
     wipes cell formatting, which would have erased all of this styling on
     the very first engine run after building it.
  2. ReportingEngine.ts's clear range was narrowed to start at row 5
     (previously row 1), since a *contents* clear still removes text
     values, and row 1-3 hold the static title/tile-label text
     `build_dashboard_template` writes once, not per-run engine output.
- POS_MASTER got banded-row conditional formatting (alternating shading)
  for readability over long lists, applied via conditional formatting
  rather than direct cell fill so it survives `clear(contents)` +
  `setValues()` cycles regardless of exact row count each run.

Verified with `tools/sim/`: full pipeline against the redesigned workbook
produces identical results to before (11,605 POS, 4,847 visits, 1,215
published, 17 detail rows + 4 KPI tiles on DASHBOARD) - confirms the UX
pass changes no value any engine depends on, including after the
clear(contents) fix.

## 14. Next step

Implementation begins engine-by-engine per the migration plan (§12), starting with Phase 0 once
the full V10.5.5 script is provided. Config table structures (§9) can be scaffolded in parallel
since they do not depend on the open script review.
