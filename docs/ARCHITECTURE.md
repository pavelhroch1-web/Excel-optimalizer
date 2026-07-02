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
- **Plan lifecycle (Draft/Published/Active/Closed)** — not started. Flagged explicitly to the
  product owner as a workflow change (not purely technical), since it would add an explicit
  Publish step to the weekly cycle and change what re-running Planning Engine mid-week does.
  Currently MANAGER_PLAN is simply overwritten on every Planning Engine run, so nothing yet
  guarantees "what was actually sent to technicians" stays fixed once Compliance Engine later
  compares against it - a real gap for multi-week accuracy, not just a nice-to-have. Needs
  product-owner sign-off before building, per the agreed autonomy boundary.

## 14. Next step

Implementation begins engine-by-engine per the migration plan (§12), starting with Phase 0 once
the full V10.5.5 script is provided. Config table structures (§9) can be scaffolded in parallel
since they do not depend on the open script review.
