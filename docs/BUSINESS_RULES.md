# Field Force Optimizer V11 — BUSINESS_RULES.md

Status: **draft v1, pending sign-off on items marked ★ OPEN**
Scope: business logic only. No implementation detail here — see `ARCHITECTURE.md`.

## 0. Governing principles

- Human in control. Planning Engine and Advisor Engine only recommend. Manual decisions in
  POS_MASTER always win and are never overwritten by import or by any engine.
- Every recommendation must be explainable: traceable to named rules with stored structured
  reasoning (SCORE_LOG / ADVISOR_LOG), never a black box.
- Business value is the primary objective. Geography (GPS) shapes *how* a qualified set of visits
  is composed into a week, never *which* POS qualify in the first place.
- Config over code. Any rule below implemented as a config table row must stay a config row —
  changing it must never require touching engine code.
- Preserve V10.5.5 behaviour unless a documented business reason justifies a change.
  **Full V10.5.5 script reviewed (Phase 0 complete) — findings folded into the rule cards below.
  One likely defect and one fragility found; both flagged for confirmation, not silently fixed.**

## 1. Rule card format

```
RULE: <name>
CONDITION: <when it applies>
ACTION: <what happens>
PRIORITY / CONFLICT: <what wins when it collides with another rule>
CONFIG SOURCE: <which config table/field drives it>
STATUS: CONFIRMED | ★ OPEN (default proposed, needs sign-off)
```

## 2. Candidate Engine

**RULE: Candidate eligibility**
CONDITION: POS status = Active AND passes Filters (TERMINAL_RULES, CATEGORY_RULES, MARKET_RULES)
ACTION: POS enters the eligible pool for its assigned technician's portfolio
PRIORITY: Filters are gates, evaluated before any scoring — an excluded POS never reaches scoring
CONFIG SOURCE: TERMINAL_RULES, CATEGORY_RULES (with explicit default row — see §9), MARKET_RULES
STATUS: CONFIRMED

**RULE: New POS without history = installed = visited today**
CONDITION: a POS appears in RAW_DATA for the first time (no prior `POS_MASTER` row at all)
ACTION: `ImportEngine.ts` sets `lastRealVisitDate` = import date, `lastRealVisitWeek` = import
week, `weeksSinceLastVisit` = 0 - confirmed by product owner (2026-07-03): "jakmile se POS založí…
oni to tam rovnou instalují, takže ten datum poslední návštěvy je když ho založíš" (installation
counts as the first visit). Superseded the earlier version of this rule (`lastRealVisitDate IS
NULL` for a new POS, left blank for Compliance Engine to fill later) - a brand-new POS is still an
eligible candidate immediately either way, but now starts its cadence clock from install date
instead of appearing to have "no history" (which read as more urgent/neglected than it actually
is). An ALREADY-KNOWN POS keeps whatever `lastRealVisitDate`/`Week`/`weeksSinceLastVisit` it
already has - `ImportEngine.ts` never touches these for existing POS; only `ComplianceEngine.ts`
updates them once real SalesApp visit data exists.
STATUS: CONFIRMED (product owner, 2026-07-03)

**RULE: Closed POS**
CONDITION: `status = Closed` — set SOLELY by absence from the current week's RAW_DATA import
(product owner, 2026-07-03: this REPLACED the earlier POS_STATUS_IMPORT-driven rule; confirmed the
weekly PPT/RAW_DATA export always contains the full universe of POS, so "missing this week"
reliably means closed — POS_STATUS_IMPORT is no longer read for this at all). A POS present again
in a later RAW_DATA import reopens automatically (status -> Active, closedSinceWeek/Year cleared).
ACTION: never a candidate; history preserved forever, closedSinceWeek/Year record the first week
it was found missing (not overwritten on subsequent still-missing runs)
STATUS: CONFIRMED (superseding an earlier CONFIRMED version of this same rule)

## 3. Cadence Rules (unifies CORE / GECO / CORN / Mandatory)

Single mechanism: a group of POS (defined by scope) must be visited within a maximum interval,
with a defined guarantee strength.

```
CADENCE_RULES
  ruleId | scope (category/market/terminal/explicit POS list) | matchValue
  | maxIntervalWeeks | guaranteeType (HARD | SOFT_HIGH_WEIGHT) | priority | active | validFrom | validTo
```

**RULE: CORN**
CONDITION: `market = CORN` (confirmed: 16 POS in production data)
ACTION: guaranteeType = HARD, maxIntervalWeeks = 4
PRIORITY: HARD — reserved capacity, evaluated before scored competition
STATUS: CONFIRMED - ACTIVATED and IMPLEMENTED 2026-07-03. Was marked CONFIRMED here since early in
the project, but two gaps existed until now: (1) `active` was never actually flipped to YES in the
`CADENCE_RULES` config table - a docs/config inconsistency, not a new decision; (2)
`PlanningEngine.ts` had no code path at all for a RECURRING+HARD guarantee (only
ONCE_PER_CAMPAIGN+HARD via `MANDATORY_9PODNIK`, and CORE's SOFT_HIGH_WEIGHT scoring boost existed)
- found when verifying CORN/GECO empirically produced zero change in plan output despite being
"active". Implemented via `core.ts`'s `matchesCadenceRuleScope()`/`isOverdueForCadenceRule()`
(unit-tested) plus a second matching pass in `PlanningEngine.ts` that force-includes an overdue POS
through the same `pickMandatory()`/`selectWeekPOS()` path as `MANDATORY_9PODNIK`, only if no
ONCE_PER_CAMPAIGN rule already claimed it first. KNOWN SIMPLIFICATION: since Planning generates a
whole multi-week Draft horizon in one run and a forced POS is added to that technician's `used` set
for the rest of the run, a POS is force-included at most ONCE per Planning run, not once every time
it crosses `maxIntervalWeeks` within the run - correct as long as `maxIntervalWeeks` (CORN=4,
GECO=5) is `>=` `CAMPAIGN_LENGTH` (4 by default), which holds for both today, but would need
revisiting if either changes. In practice CORN's 16 POS mostly overlap with `MANDATORY_9PODNIK`'s
category match already (found while testing on real data), so this mechanism's CORN impact is
currently small; GECO (category-only, no such overlap) is where it matters.

**RULE: GECO**
CONDITION: `category = 1GECO` (387 POS) - product owner confirmed 2026-07-03, not the broader
`market = KA PARTNERS` (2088 POS) alternative that was also on the table
ACTION: maxIntervalWeeks = 5, guaranteeType = HARD (volume is small relative to weekly capacity,
so a hard reservation cannot meaningfully starve other visits — see worked example in
ARCHITECTURE.md §6)
STATUS: CONFIRMED (product owner, 2026-07-03) - ACTIVATED and IMPLEMENTED, see CORN entry above for
the shared mechanism and its "once per Planning run" caveat. Verified on real data: a 1GECO POS
mocked to 10 weeks since last visit (past the 5-week threshold) was correctly force-included with
reason tag "MANDATORY (GECO)"; most 1GECO POS already score highly enough via CORE's
SOFT_HIGH_WEIGHT boost to be selected anyway (category `1GECO` also matches CORE's `STARTS_1`
categoryPrefix rule), so this mechanism's practical effect is mainly a safety net for GECO POS that
would otherwise lose out on scored competition, not the primary reason most of them get visited.

**RULE: Mandatory**
CONDITION: any condition configured in CADENCE_RULES with guaranteeType = HARD and an explicit
POS list or category/market match (generalizes today's `9PODNIK` hardcoded rule)
ACTION: reserved capacity, takes precedence over normal (non-HARD) candidates when capacity is tight
STATUS: CONFIRMED (mechanism); new mandatory scenarios are added as config rows, no code change

**RULE: CORE**
CONDITION: ★ OPEN — today derived from `category` prefix `1*` via CATEGORY_RULES (`STARTS_1 → CORE`)
ACTION: ★ OPEN — two candidate mechanisms under consideration:
  (a) SOFT_HIGH_WEIGHT in Cadence Rules — CORE POS always score highest among non-HARD candidates,
      no fixed deadline, rotates naturally as older CORE POS accumulate a higher History component
  (b) Cadence rule with its own maxIntervalWeeks (rotation target)
PRIORITY: below HARD guarantees (Mandatory/CORN/GECO), above ordinary scored candidates
STATUS: ★ OPEN — default proposal is (a); CORE volume (288 of 531 POS in one technician's
portfolio) makes a hard per-technician deadline impractical, so SOFT_HIGH_WEIGHT is recommended
unless you have a specific rotation target in mind

**RULE: Tie-break between two HARD cadence rules competing for the same capacity**
CONDITION: two or more HARD rules expire in the same week for the same technician and capacity
is insufficient for both
ACTION: ★ OPEN — no tie-break rule defined yet. Reduced in practice by portfolio-scoped planning
(§6) and rolling horizon (deadline pressure normally resolves by pulling the visit into an earlier
week within the horizon), but a true collision is still possible and needs an explicit answer
STATUS: ★ OPEN

## 4. Pareto / Classification

```
PARETO_GROUPS
  tierId | name | boundaryType (FIXED_VALUE | PERCENTILE) | boundaryValue | scope (GLOBAL | PER_REGION | PER_MARKET)
```

**RULE: KA**
CONDITION: ★ OPEN — proposed `market = KA PARTNERS` (2088 POS); open whether restricted further to
`KATEGORIZACE = A` within it (1992 of 2088)
STATUS: ★ OPEN

**RULE: IDT above PPT threshold**
CONDITION: `market = IDT` AND `ppt` above threshold defined in PARETO_GROUPS
ACTION: same scoring priority as KA (per your instruction: "KA a IDT nad PPT limitem mají stejnou
prioritu")
STATUS: ★ OPEN — `boundaryType` and `scope` not yet defined; IDT is 46% of the entire POS base
(5,342 of 11,605), so this threshold materially shapes the whole plan and should not be guessed

**RULE: Pareto (highest business goal)**
CONDITION: same PARETO_GROUPS mechanism, top tier = "strongest outlets"
ACTION: feeds Business Score as a component and feeds Advisor Engine neglect-risk alerts with a
tighter tolerance than standard POS
STATUS: CONFIRMED for V11 phase 1 — **preserve V10.5.5's actual behaviour: scope = PER_TECHNICIAN**
(relative top 20% within each technician's own portfolio), not global/regional. `scope` is a
config field on PARETO_GROUPS (`PER_TECHNICIAN | GLOBAL | PER_REGION | PER_MARKET`) specifically so
this can change later without code changes — but the field starts set to `PER_TECHNICIAN` and this
is not being reopened as a business decision right now. `boundaryType`/`boundaryValue` for
KA/IDT/global tiers remain ★ OPEN, deferred to when Planning Engine scoring is built.

## 5. Business Value (PPT)

**RULE: PPT is one input among several, never sole driver**
ACTION: PPT feeds Business Score as one weighted component (weight in SCORE_PROFILES), and feeds
PARETO_GROUPS tiering. A high-PPT POS with a low score on other components can still lose to a
lower-PPT POS that is CORE/GECO/CORN/overdue.
STATUS: CONFIRMED (principle) — ★ OPEN: exact meaning/unit of `PPT`/`PTT` field and whether it is
a genuinely separate import or the field already embedded in RAW_DATA (both files reviewed so far
show it embedded, so current working assumption is: **same field, no separate import** — flag if
wrong)

## 6. Campaign Economics (LOS/LOT combination)

**RULE: Combine LOS and LOT into one visit when economically beneficial**
CONDITION: POS is a candidate for both an active/upcoming LOS activity and LOT activity within
`CAMPAIGN_LOOKAHEAD_WEEKS` of each other (default proposed = `CAMPAIGN_LENGTH`, today 4 weeks)
ACTION: Decision Engine prefers delaying the earlier visit to combine, UNLESS a HARD cadence rule,
Mandatory rule, or approaching NEGLECTED_AFTER_WEEKS threshold would be violated by waiting — in
that case business urgency wins over the visit-count saving (per your instruction: "pokud by
čekání poškodilo obchodní cíl, má přednost obchod")
CONFIG SOURCE: `CAMPAIGN_LOOKAHEAD_WEEKS` (new CONTROL parameter)
STATUS: CONFIRMED (mechanism); lookahead length ★ OPEN, default proposed above

**RULE: Campaigns are never hardcoded**
CONDITION: any campaign (Gems, Sportka, Losy, Eurojackpot, seasonal campaigns, future campaigns)
ACTION: Business Engine reads campaign identity, window and PRIORITY purely from ACTIVITY_PLAN;
no campaign name may ever appear in engine code
STATUS: CONFIRMED

## 6a. GPS bonus overflow (corrected spec — deferred implementation)

**RULE: Deliberate small capacity overflow for very close POS**
CONDITION: `GPS_EXTRA_ENABLED = true` AND a non-selected POS lies within `GPS_EXTRA_RADIUS_METERS`
(default proposed 300m) of an already-selected POS for that technician/week
ACTION: Route/Geo Engine may add it even though weekly capacity is already met, up to
`GPS_EXTRA_MAX_VISITS` (default proposed 5) extra visits total for that technician/week. Each such
visit is tagged with reason `GPS BONUS` (distinct from `NEARBY`), so it is visibly a deliberate
business decision, never mistaken for a planning error.
CONFIG SOURCE: `GPS_EXTRA_ENABLED`, `GPS_EXTRA_RADIUS_METERS`, `GPS_EXTRA_MAX_VISITS` (CONTROL)
CORRECTION: earlier turn proposed simply capping selection at capacity to fix the V10.5.5
`addNearby()` defect (BUSINESS_RULES.md §15a). Product owner corrected this: the *original*
uncapped-add intent was right (a 41st visit 120m away is worth doing rather than a whole extra
trip next week) — what must be fixed is that overflow visits should never be silently lost
(the actual V10.5.5 defect), not that overflow should be disallowed. The fix is: bound the overflow
explicitly and configurably, tag it clearly, and make sure every added POS is actually placed in
a day slot (no more silent loss) — not eliminate the overflow behaviour itself.
STATUS: CONFIRMED (spec); **implementation deferred to Route/Geo Engine build (not part of the
bottom-up infrastructure phase)**

## 6b. Plan lifecycle - implemented

**RULE: Draft -> Published -> Active -> Closed**
ACTION:
- Draft: Planning Engine freely regenerates these weeks on every run.
- Published: set only by an explicit manager action (PublishEngine.ts). Snapshots that week's
  MANAGER_PLAN rows into the immutable, append-only MANAGER_PLAN_PUBLISHED. From this point,
  Planning Engine never touches that week's rows again.
- Active: mechanical - the week's Monday has passed (system date, no external calendar).
- Closed: mechanical - no planned visit for that week is still Pending (per Compliance Engine's
  determineComplianceStatus). Closing takes priority over the Published/Active distinction, and
  can happen before the week's Monday if every visit resolves early. Terminal - never reopened.
STATUS: CONFIRMED, implemented (core.ts `advanceLifecycleStatus`, PublishEngine.ts,
ComplianceEngine.ts). Compliance Engine reads MANAGER_PLAN_PUBLISHED exclusively, never the
freely-regenerated MANAGER_PLAN - this was the explicit product-owner requirement for this phase.
Only one week is published per PublishEngine.ts run (the earliest Draft week), matching the real
weekly ritual rather than publishing the whole rolling horizon at once.

## 6c. Geo cluster bonus - implemented

**RULE: Small score nudge toward geographic clustering at selection time**
CONDITION: product owner (2026-07-06), during the manager-analytics review: "chci tourplany, co
davaji smysl z hlediska prinosu i trasy" - reviewed real generated plans and found the daily
route length (POS visited in planned order) had a p90 of ~118km and a worst case of 311km for 9
visits, because `selectWeekPOS()` picks candidates purely by `score` (business value) with zero
geographic awareness - route efficiency only entered the picture afterward, in `geoDays()`'s
per-day nearest-neighbor clustering of whatever was already selected.
ACTION: `computeGeoClusterBonus()` (`office-scripts/shared/core.ts`, ported to
`desktop_client/engines/core_logic.py`) adds a small bonus to a candidate's score equal to the
sum of `CONTROL.GEO_CLUSTER_BONUS_FACTOR` (default 1%) times each OTHER candidate's own base
score within `CONTROL.GEO_CLUSTER_RADIUS_KM` (default 3km) for the same technician, capped at
`CONTROL.GEO_CLUSTER_MAX_BONUS` (default 5000). Applied once per technician's full candidate pool,
after every item's base `computeScore()` is set (so bonuses reflect neighbors' real value, not a
moving target), before Premium/Pareto tiering and selection.
CONFIRMED SCOPE (product owner, 2026-07-06): a small nudge on VALUE-BASED SELECTION, not a
route-first redesign - value stays the primary driver. The bonus cap (5000) is kept well below the
smallest meaningful score tier (`NEGLECTED_BONUS`=50000), so it can only break near-ties among
otherwise-similar candidates, never override being CORE, classification A, or neglected.
VERIFIED (2026-07-06): re-ran Planning Engine against real production POS_MASTER data - p90 daily
route dropped from 117.8km to 113.0km, worst case from 311.3km to 201.4km (-35%), average
essentially unchanged (56.0km to 55.1km) - the bonus mainly reins in extreme outlier scattering,
as intended by "small nudge", not a bulk redesign. Cross-language equivalence
(`tools/sim/compare_engines.py`) confirmed the TypeScript and Python ports produce identical
output on the same real seed.
STATUS: CONFIRMED (product owner, 2026-07-06), implemented and tested.

## 6d. Day-clustering fix (geoDays) - implemented

**RULE: day assignment uses a global capacitated nearest-anchor match, not sequential per-day greedy**
CONDITION: product owner (2026-07-06), asked to think further about tour-plan assembly. Proposed
fix: re-sequence each day's stops into a true nearest-neighbor DRIVING ORDER. Product owner
declined ("ja nevim odkud bude vyjizdet takze nn") - the technician's actual daily departure point
is unknown, so an anchor-relative route sequence isn't reliable. Product owner then specified
priority explicitly: "dulezite je PPT a pak to shlukovani, aby mi tam nelitali jako blbci" - i.e.
value/PPT stays the primary driver (unchanged, see 6c), but day-to-day GEOGRAPHIC GROUPING (which
POS land on the SAME day, not what order within the day) should be tightened so technicians aren't
crossing the territory back and forth across the week.
PROBLEM FOUND: the old `geoDays()` picked each day's anchor sequentially (highest score still
remaining) and filled that day only from whatever was nearest to that ONE anchor, one day at a
time. A day with a small remaining capacity could "steal" nearby points and leave a geographically
tight cluster split across two later, unrelated days - not because of value or distance, but purely
because of which day happened to run its greedy pass first.
ACTION: `geoDays()` (`office-scripts/shared/core.ts`, synced into `PlanningEngine.ts`, ported to
`desktop_client/engines/core_logic.py`) now picks the same day-anchors as before (top-scoring items,
one per day - value/PPT unchanged as the sole driver of WHICH POS become anchors), then assigns
every other candidate via a capacitated nearest-anchor match considered across ALL days at once:
every (point, day-anchor) pair is sorted by distance ascending, and each point is greedily given to
its nearest anchor that still has capacity. This is the standard capacitated nearest-centroid
heuristic - not a mathematically optimal partition, but a point is never stranded on a distant day
just because a closer day filled up first.
VERIFIED (2026-07-06): re-ran Planning Engine against real production POS_MASTER data (540 real
technician/week/day groups) and measured each day-group's internal geographic spread (max pairwise
distance among that day's POS): median 27.9km, p90 72.4km, worst case 128.3km - well below the
whole-day-route-length numbers measured for the geo-cluster-bonus feature alone (6c: p90 113km,
worst 201km; not a directly identical metric, but same real seed, same direction of improvement).
Cross-language (`tools/sim/compare_engines.py`) and unit tests (`tests/core.test.ts`,
`desktop_client/engines/test_core_logic.py`) both updated and passing.
STATUS: CONFIRMED (product owner, 2026-07-06), implemented and tested.

## 7. GPS / Weekly composition

**RULE: GPS shapes the week, not individual tie-breaks**
CONDITION: after Filters + Cadence + Score produce a ranked eligible pool for a technician's
portfolio
ACTION:
  1. take top `capacity × CANDIDATE_POOL_MULTIPLIER` candidates (existing V10.5.5 concept,
     default 1.3, config-driven)
  2. geographically cluster this buffer pool
  3. select the final `capacity` POS balancing business value and cluster compactness, weighted
     by `GEO_COMPACTNESS_WEIGHT`
  4. Route Engine sequences the final selected set into days — never adds/removes POS
PRIORITY: business value always determines the eligible/ranked pool; GPS only ever operates
within it, never expands it beyond what already qualified
CONFIG SOURCE: `CANDIDATE_POOL_MULTIPLIER`, `GEO_COMPACTNESS_WEIGHT` (new CONTROL parameters)
STATUS: CONFIRMED (mechanism); `GEO_COMPACTNESS_WEIGHT` default value ★ OPEN — to be tuned against
a real portfolio scenario before go-live, not guessed now

## 8. Capacity

**RULE: Dynamic technician capacity**
ACTION:
```
capacity(technician, week) =
  IF CAPACITY_OVERRIDE has an entry for (technician, year, week) → use it
  ELSE → workDays(week, year) × TARGET_VISITS_DAY
        (workDays = 5 minus Czech public holidays falling on a weekday that week —
         reuses existing V10.5.5 workDays()/isHoliday() logic)
```
CONFIG SOURCE: `CAPACITY_OVERRIDE` (technician, year, week, capacity — no reason field), public
holiday calculation is pure date logic, no external calendar/API
STATUS: CONFIRMED. Working defaults (pending correction): override is a single weekly total (no
day-level granularity), and may raise or lower capacity in either direction.

**RULE: HARD cadence deadline falling in a low/zero-capacity week**
ACTION: rolling horizon absorbs it into the next week with available capacity; if that would still
breach the interval, Advisor Engine raises a risk alert — it is never silently dropped
STATUS: CONFIRMED

## 9. Filters (config-driven scope)

**RULE: Category default**
CONDITION: a category with no explicit row in CATEGORY_RULES (today: 27% of all POS, e.g.
`4OSTATNI`, fall into this gap)
ACTION: ★ OPEN — CATEGORY_RULES must gain an explicit default/fallback row (`*, EXCLUDE` or
`*, NORMAL`) so behaviour for unmapped categories is visible in config, not implicit in code
STATUS: ★ OPEN — needs your decision on the default value

## 10. Manual overrides

**RULE: Manual overrides always win**
ACTION: force-include, force-exclude, priority change, technician reassignment, notes — never
overwritten by import or by any engine, at any point in the plan lifecycle (including after
Publish — see §11)
CONFIG SOURCE: POS_MASTER manual fields (override, priority delta, technician override, notes)
STATUS: CONFIRMED

**RULE: Force-include bypasses hard filters**
CONDITION: manual force-include on a POS that would otherwise fail Filters (e.g. Closed, EXCLUDE
category)
ACTION: ★ OPEN — proposed: yes, force-include overrides Filters entirely (manual intent is always
the top layer, per §0), but needs your explicit confirmation since it's a safety-relevant default
STATUS: ★ OPEN

**RULE: Priority adjustment scale**
ACTION: ★ OPEN — proposed: qualitative levels (Low / Normal / High / Critical) mapped internally to
score-point deltas, rather than asking you to enter raw point values — easier to use, avoids
needing to know the current weight scale. Needs your confirmation.
STATUS: ★ OPEN

**RULE: Override consequence explanation**
ACTION: when a manual override conflicts with automatic logic, Advisor Engine surfaces a plain-
language note at next refresh (e.g. "this override removes POS X from automatic candidacy because
its category is EXCLUDE")
STATUS: CONFIRMED

## 11. Plan lifecycle

```
Draft → Review (manual adjustments) → Published (locked) → Active (week in progress) → Closed
```

**RULE: Week becomes binding at Publish**
ACTION: `plannedVisit` is snapshotted as immutable at the moment of Publish — this snapshot is
what Compliance Engine later compares against, not a re-derived value
STATUS: CONFIRMED

**RULE: Post-publish manual changes**
ACTION: allowed at any time (manual always wins, §10), but recorded as a visible, timestamped
amendment to the published plan rather than a silent rewrite — preserves auditability of "what was
actually sent to the technician on Monday morning"
STATUS: CONFIRMED

**RULE: Technician day reordering**
CONDITION: within a Published/Active week
ACTION: technician may resequence which day a visit happens; may not move a visit to a different
week, may not add/remove POS from the published set
STATUS: CONFIRMED

## 12. Compliance

**RULE: SalesApp "Store UID" is a terminal number, not a POS number**
CONDITION: matching a SalesApp visit row to a planned POS visit
DISCOVERED/CONFIRMED: 2026-07-03 - a real production test found ZERO direct matches between
`POS_MASTER.posId` and SalesApp's `Store UID` column on real data, despite the code previously
assuming they were the same identifier. Product owner confirmed: SalesApp mislabels this column -
it is actually the TERMINAL number (matches `POS_MASTER.terminalId`, sourced from RAW_DATA's
"ČÍSLO TERMINÁLU"), not the POS/location number. A single physical POS can have 2 terminals, and
plans are made per-POS (location), never per-terminal, so every SalesApp row must be resolved
terminal → POS via `POS_MASTER.terminalId` before matching against `MANAGER_PLAN_PUBLISHED`.
ACTION: `ComplianceEngine.ts` builds a `terminalId → posId` map from `POS_MASTER` and resolves
every SalesApp row through it before compliance matching; a terminal not found in `POS_MASTER` is
skipped (not guessed at). Verified: resolving via `terminalId` matched 73% of rows in a real
SalesApp export (5636/7710), all with plausible name/address correspondence; the remaining 27% are
terminals not present in that particular `POS_MASTER` snapshot (expected, not a bug).
STATUS: CONFIRMED (product owner, 2026-07-03) - corrects a previously-CONFIRMED-but-wrong
assumption ("Store UID = POS number") that would have silently prevented essentially all real
compliance matching from ever succeeding in production.

**RULE: Compliance states**
States: `Splněno_včas`, `Splněno_pozdě`, `Nesplněno`, `Navíc_evidováno`
CONDITION: computed by comparing the Published snapshot of `plannedVisit` against `actualVisit`
from the next SalesApp import
ACTION:
  - visit realized within the same published week → Splněno_včas
  - visit realized in a later week → Splněno_pozdě
  - visit still not realized after ★ OPEN (default proposed: 1 further week) → Nesplněno
  - visit realized that was not in the plan → Navíc_evidováno (neutral, logged only — "není to
    chyba ani bonus")
PRIORITY: technician KPI is based on completion of planned visits, never on total visit count
STATUS: ★ OPEN — exact "late → not-completed" cutoff needs your number; default proposed above

**RULE: Extra visits outside own territory**
CONDITION: technician visits a POS assigned to a different technician's portfolio
ACTION: ★ OPEN — proposed: still logged as Navíc_evidováno on the visiting technician, with a
cross-reference note; does not affect the assigned technician's compliance either way
STATUS: ★ OPEN

**RULE: Pending state (implementation addition, not a business-logic change)**
A planned visit whose deadline hasn't arrived yet is not "Nesplneno" (failed) - it just hasn't
happened yet. `determineComplianceStatus()` (office-scripts/shared/core.ts) returns `Pending` in
that case, only resolving to `Nesplneno` once `COMPLIANCE_LATE_CUTOFF_WEEKS` has actually elapsed
relative to the newest week present in the SalesApp data (a data-driven proxy for "now", since the
workbook has no live clock). This is bookkeeping needed to implement the four named states
correctly, not a fifth business outcome exposed to the manager.
STATUS: CONFIRMED (implementation necessity)

**RULE: "Was this a campaign visit at all" — CONFIRMED and implemented**
A SalesApp row counts as a realized campaign visit for compliance purposes ONLY when its
`Účel návštevy -  Technik - MCHD - Náběh kampaně` column is `Ano`, in addition to `State` being
Completed/Finalized. A Completed/Finalized row for any other visit purpose (restocking, lottery
ticket pickup, etc.) is real but is not evidence a planned campaign visit happened — it is ignored
entirely: not matched to `MANAGER_PLAN_PUBLISHED`, not logged as `Navic_evidovano` either
(explicit product-owner instruction: "ignorovat úplně", not "počítat jako Navic_evidovano").
Implemented in `ComplianceEngine.ts`'s SalesApp-parsing loop (matched by stripping whitespace from
the header name, since the real export's header has an irregular double space). Verified against a
real uploaded SalesApp export (7690 Completed/Finalized rows, 3316 of them `Nabeh kampane = Ano`)
via `tools/sim/run_e2e.ts` — `ComplianceEngine.ts` produced exactly 3316 realized visits, matching
an independent count computed directly from the source file.
STATUS: CONFIRMED (product owner, 2026-07-03)

**RULE: WHICH specific LOS/LOT campaign/product a visit serviced — still BLOCKED on input data**
The rule above confirms only THAT a visit was a campaign visit, not WHICH LOS/LOT it serviced. The
SalesApp export was checked column-by-column (37 columns) for a structured field naming which
LOS/LOT campaign a visit serviced. None exists — campaign names appear only in inconsistent free-
text notes (`OZ - Ostatní (do textu)`, `Technik/OZ - Poznámka`), which cannot be parsed reliably
without guessing. Proposed (not implemented) robust alternative: derive the serviced campaign from
`ACTIVITY_PLAN`'s week-based schedule, crossed with the same `Nabeh kampane` (Ano/Ne) signal —
i.e. "a Nabeh-kampane=Ano visit in week W serviced whatever LOS/LOT was active per ACTIVITY_PLAN in
week W." This is a further business interpretation of ambiguous data, not a technical detail —
needs explicit product-owner sign-off before implementing.
STATUS: ★ OPEN — blocks per-POS LOS/LOT compliance breakdown specifically; does NOT block basic
plan-vs-actual compliance (Splneno/pozde/Nesplneno/Navic), which now correctly uses the
Nabeh-kampane=Ano gating above for POS+week matching.

**RULE: Compliance aggregation**
ACTION: rolled up by week, month, per technician, and network-wide; feeds Advisor Engine trend
detection over a configurable `TREND_WINDOW_WEEKS` (default proposed: 4)
STATUS: CONFIRMED (mechanism), window default ★ OPEN

## 12a. Manager dashboard tracking gate + route efficiency (added 2026-07-06)

**RULE: Explicit "start tracking" step, separate from Publish/Compliance**
CONDITION: a week's plan has been Published (and possibly already evaluated by
`ComplianceEngine.ts`)
ACTION: the week's rows do NOT appear in `TECHNICIAN_PERFORMANCE_LOG` /
`TECHNICIAN_PERFORMANCE_SUMMARY` / `TECHNICIAN_TOP_ISSUES` (and therefore not on
`TECHNICIAN_SCORECARD`/`PERFORMANCE`/`WEEK_DASHBOARD`/`HOME`) until the manager explicitly runs
`StartTrackingEngine.ts`, which stamps `PLAN_LIFECYCLE.trackingStartedAt` for that week.
Publishing and Compliance evaluation are unaffected and keep happening automatically —
`COMPLIANCE_LOG` is populated regardless of tracking status. Only the manager-facing dashboard
aggregation layer (`PerformanceEngine.ts`) is gated.
RATIONALE (product owner, 2026-07-06): "abych ho začal sledovat až řeknu já" — the manager wants
to be the one deciding when a freshly published/evaluated week's numbers start counting toward a
technician's tracked performance, e.g. while still reviewing the plan.
INTERPRETATION NOTE: "sledovat" (track/monitor) was interpreted as "appear on manager dashboards",
not "get evaluated at all" — a genuinely ambiguous phrase; correct if this reading is wrong.
STATUS: CONFIRMED (product owner, 2026-07-06), implemented in `StartTrackingEngine.ts` +
`PerformanceEngine.ts`.

**RULE: Estimated daily route efficiency (km) per technician**
CONDITION: at least 2 GPS-resolvable POS realized (Splneno_vcas/Splneno_pozde) by the same
technician on the same weekday, for a week where tracking has been started
ACTION: `PerformanceEngine.ts` orders that day's realized POS by their position in the
technician's PLANNED visit sequence for that exact date (from `MANAGER_PLAN_PUBLISHED` row
order — itself a product of Planning Engine's GPS clustering), appends any unplanned/"navíc"
POS at the end (sorted by posId for determinism), and sums consecutive `distanceKm()` calls
between them. Written to `TECHNICIAN_PERFORMANCE_LOG.kmMon..kmFri`, displayed on
`TECHNICIAN_SCORECARD` with a semaphore (green/orange/red) against
`CONTROL.ROUTE_KM_WARNING_KM` (default 80) / `CONTROL.ROUTE_KM_CRITICAL_KM` (default 150).
CAVEAT: this is an ESTIMATE, not measured GPS/timestamp tracking — no such data exists anywhere
in this system. It approximates driving distance from planned visit order, not the technician's
actual route. Fewer than 2 GPS-resolvable stops on a day → 0 (not shown as a driving day).
RATIONALE (product owner, 2026-07-06): "klidně číslo kolik najel km třeba mezi těmi pos a
semafor... me zajímá celkový počet návštěv za den" — wants a route-efficiency signal alongside
daily visit counts (the latter already existed via `visitsMon..visitsFri`), retained long-term
the same way `TECHNICIAN_PERFORMANCE_LOG` already is (rebuilt every run from the append-only
`COMPLIANCE_LOG`, never cleared).
STATUS: CONFIRMED intent (product owner, 2026-07-06); thresholds (80/150 km) are a starting
guess, explicitly flagged in `CONTROL` as tunable on real data, not a confirmed business rule.

**RULE: Daily POS list, not just a count**
CONDITION: after seeing the daily km/visit-count breakdown, product owner asked for the actual
POS list per day ("na tady mě to zajímá až na dny, zda jezdil efektivně, kolik jich udělal a
pos"), not just a number.
ACTION: `PerformanceEngine.ts` writes `posListMon..posListFri` to `TECHNICIAN_PERFORMANCE_LOG` -
that day's realized POS as "id - name", comma-separated, in the SAME order used to compute the
km estimate (technician's planned visiting sequence for that date). Displayed on
`TECHNICIAN_SCORECARD` in a new "POS PO DNECH" section, one row per weekday.
STATUS: CONFIRMED (product owner, 2026-07-06); informational only.

**RULE: "Merch" and "Visibility" are the same signal - and "Ostatní" (other) visit count**
CONDITION/DISCOVERED: after reviewing the real SalesApp export together (2026-07-06), product
owner confirmed that "Merch" and "Visibility" - originally proposed as two separate breakdown
columns in `docs/MANAGER_UX_ARCHITECTURE.md` section 1a - are in fact the SAME single signal:
the `Účel návštevy -  Technik - MCHD - Náběh kampaně` column already used as the campaign-visit
gate (section 12 above). There is no separate structured "Visibility" column in the real data.
What the product owner actually wanted in addition: a count of "Ostatní" (other-purpose) visits -
real Completed/Finalized SalesApp visits at a technician's POS whose purpose is NOT the campaign
signal (e.g. restocking, envelopes, lottery ticket downloads) - shown for context, alongside the
campaign-visit numbers, but never counted toward compliance.
ACTION: `ComplianceEngine.ts` now logs these non-campaign-purpose Completed/Finalized rows to a
new append-only `OTHER_VISIT_LOG` sheet (deduplicated by SalesApp UID, same pattern as
`VISIT_HISTORY_ACTUAL`) instead of discarding them entirely. `PerformanceEngine.ts` aggregates
them into a new `otherVisits` column on `TECHNICIAN_PERFORMANCE_LOG` (technician resolved via the
POS's planned technician that week, falling back to `POS_MASTER`'s current assignment - same
pattern as `Navic_evidovano` attribution), gated by the same tracking-started check as everything
else. Displayed on `TECHNICIAN_SCORECARD` next to the route-efficiency table as "Ostatní
návštěvy" - neutral/gray styling, not a KPI to optimize against.
STATUS: CONFIRMED (product owner, 2026-07-06) - does not change compliance classification,
`COMPLIANCE_LOG`, or `PLAN_LIFECYCLE` in any way; purely an additional informational count.

**RULE: "Flaká riziko" - persistent-underperformance flag (technician-level only)**
CONDITION: product owner asked, after reviewing the manager screens, for a direct signal of
"which technician is slacking and which isn't" - explicitly scoped to the technician only, not a
POS-level systemic-vs-personal split (considered and declined: "mě zajímá technik ne POS").
ACTION: `PerformanceEngine.ts` looks at a technician's last `CONTROL.FLAKANI_WINDOW_WEEKS`
(default 4) tracked weeks on `TECHNICIAN_PERFORMANCE_LOG`, counts how many had `compliancePercent`
below `CONTROL.FLAKANI_BAD_WEEK_THRESHOLD_PERCENT` (default 70), and sets `flakaRiziko` = "Ano" on
`TECHNICIAN_PERFORMANCE_SUMMARY` only once at least `CONTROL.FLAKANI_BAD_WEEKS_COUNT` (default 2)
of those were bad - a repeated pattern, not a single bad week (confirmed: "2+ ze 4 posledních
týdnů pod 70 %"). With fewer tracked weeks of history than the bad-weeks-count, the flag can never
fire. Shown on `PERFORMANCE` (network-wide comparison - scan the whole team at once) and as a
badge next to the region line on `TECHNICIAN_SCORECARD`.
STATUS: CONFIRMED (product owner, 2026-07-06); thresholds (4 weeks / 70% / 2 bad weeks) are the
confirmed definition, not a placeholder default like the km thresholds above.

**RULE: Territory map - GPS scatter colored by technician**
CONDITION: product owner asked for a geographic view of technician territories, to visually
verify POS selection/coverage makes sense (the whole reason for this manager-analytics review:
"pro me je nejdulezitejsi aby byly vhodne vybrane ty POS").
ACTION: `ReportingEngine.ts` writes `POS_MAP_DATA` (one X/Y coordinate-pair column per technician,
Active POS with GPS only), rebuilt fresh every run. The `MAP` sheet (`tools/ux_style.py`) plots it
as a flat XY scatter chart, one series per technician, distinctly colored, chart X = longitude,
chart Y = latitude (POS_MASTER's own `gpsX`/`gpsY` columns are latitude/longitude respectively -
swapped for the chart so it reads as a normal north-up map).
CONFIRMED SCOPE (product owner, 2026-07-06): colored by technician (territory view, not by
compliance/neglect status), whole network at once (not filtered to one technician).
CAVEAT: NOT a real street map - this project has no online map service (architecture mandate: no
external APIs, no online sync), so it is a flat-earth GPS scatter, the same approximation already
used by `distanceKm()`. Fixed-size grid (40 technician slots x 700 rows each - real data's largest
territory is ~530 POS as of 2026-07-06); a technician count or single territory size beyond that
cap would silently truncate, logged in `ReportingEngine.ts`'s console output.
STATUS: CONFIRMED (product owner, 2026-07-06).

**RULE: Long-term (monthly) compliance trend**
CONDITION: product owner asked for a longer-term view than the existing 6-week trend chart -
"je pro mě i důležitý dlouhodobý pohled" - vývoj compliance za měsíce/kampaně.
ACTION: `PerformanceEngine.ts` writes `monthKey` (YYYYMM, e.g. 202607) on
`TECHNICIAN_PERFORMANCE_LOG` - the calendar month of each row's ISO week (via `isoMonday()` + JS
Date, not an Excel formula approximation). `TECHNICIAN_SCORECARD` adds a "DLOUHODOBÝ TREND (posl.
12 měsíců)" line chart averaging `compliancePercent` per calendar month, for the selected
technician's last 12 distinct months on record (a technician can have several weeks in the same
month - those are averaged, not double-counted).
STATUS: CONFIRMED (product owner, 2026-07-06).

**RULE: HOME shows "kdo flaká" at a glance**
CONDITION: product owner asked for "přehled o všem" (an overview of everything) directly on HOME,
not something a manager has to navigate to PERFORMANCE to discover.
ACTION: HOME (`tools/ux_style.py`'s `build_home()`) shows a single callout line, right below the
network KPI cards: if any technician has `flakaRiziko="Ano"` (`TECHNICIAN_PERFORMANCE_SUMMARY`),
names them; otherwise a green "no one flagged" message. Also fixed: the MAP screen was missing
from HOME's own quick-navigation row (it was added to the shared nav rail but not here).
STATUS: CONFIRMED (product owner, 2026-07-06).

**RULE: Route efficiency needed a network-wide view, not just per-technician**
CONDITION: found during a final full test pass (2026-07-06): route efficiency (km + semafor)
only ever existed on `TECHNICIAN_SCORECARD` for one technician at a time - `PERFORMANCE` (the
network-wide comparison table) and `HOME` had no route-efficiency signal at all, unlike
compliance and flaká riziko which are both visible network-wide.
ACTION: `PerformanceEngine.ts` adds `maxKmDay` (the worst single day's route-km that week) to
`TECHNICIAN_PERFORMANCE_SUMMARY`. `PERFORMANCE` gets a new "Km/den (nejhorší)" column with the
same green/orange/red semaphore (`CONTROL.ROUTE_KM_WARNING_KM`/`ROUTE_KM_CRITICAL_KM`) already
used on `TECHNICIAN_SCORECARD`. `HOME` gets a matching "KDO JEZDÍ NEEFEKTIVNĚ" callout (same
pattern as "KDO FLAKÁ") naming any technician whose worst day exceeded the CRITICAL threshold.
STATUS: CONFIRMED (found+fixed during final review, 2026-07-06) - consistent with the existing
confirmed route-efficiency thresholds, not a new business rule.

**RULE: First-run instructions must list every deployable engine**
CONDITION: found during a final full test pass: HOME's "PRVNÍ SPUŠTĚNÍ" (first-run) instructions
listed only 5 of the 8 deployable engines, missing `StartTrackingEngine.ts` and
`PerformanceEngine.ts` - a first-time user following those instructions literally would never
discover either step, leaving `TECHNICIAN_PERFORMANCE_LOG` permanently empty.
ACTION: instructions now list all 8 engines in deployment order.
STATUS: bug fix, no behavior change.

## 13. Advisor Engine

Never writes to the plan. Reads POS_MASTER + COMPLIANCE_LOG + SCORE_LOG, writes to ADVISOR_LOG.

```
ADVISOR_RULES
  ruleId | type | condition | threshold | severity | messageTemplate | active
```

Confirmed alert types (all data-driven from mechanisms already defined above, no new logic):
- Neglect risk (`weeksSinceLastVisit` approaching / past threshold)
- Campaign-completion risk (Cadence deadline projected to be missed given remaining horizon
  capacity)
- Combine-visit opportunity (Campaign Economics candidate, §6)
- Technician overload (capacity utilization trend over `TREND_WINDOW_WEEKS`)
- Regional underperformance (compliance trend by region over `TREND_WINDOW_WEEKS`)
- Pre-emptive priority warning (two-tier threshold: warning before critical — ★ OPEN whether both
  tiers are needed or one is enough)
- Override consequence notes (§10)

STATUS: mechanism CONFIRMED; two-tier warning threshold ★ OPEN

**RULE: ACTIVITY_PLAN's LOS/LOT campaigns as a live 2-line chart**
CONDITION: product owner (2026-07-06): "chci aby aktivity plan byl i vizualizovany jako 2 čáry a
dynamicky se měnil podle toho co zadam" - the existing per-row heatmap timeline shows detail but
not an at-a-glance "is LOS/LOT running right now, or is there a gap" view.
ACTION: `redesign_activity_plan()` (`tools/ux_style.py`) adds two hidden rows spanning the same
week-timeline columns the heatmap already uses, computing a 0/1 "is any LOS (or LOT) row active
this week" flag via `SUMPRODUCT` over the live `$A`/`$C`/`$D` columns, then a native line chart
(two series, LOS/LOT, not smoothed - this is an on/off flag, not a curve) plotting them. Fully
formula-driven off the same editable columns as the heatmap, so it updates the moment a row's
TYPE/START_WEEK/END_WEEK is edited - no engine involved.
STATUS: CONFIRMED (product owner, 2026-07-06).

## 14. Seasonal / strategy configuration

```
SCORE_PROFILES        (named sets of component weights, e.g. DEFAULT, COVERAGE_MODE)
SEASONAL_STRATEGY     (strategyId, name, startDate, endDate, activeProfileId, priority)
```

**RULE: Strategy switching is pure configuration**
ACTION: outside any active SEASONAL_STRATEGY window, `DEFAULT` profile applies (business value
maximization). Inside a window (e.g. pre-Christmas), the configured profile applies (e.g. network
breadth over raw value). No code path differs — only the active weight set differs.
STATUS: CONFIRMED

## 15a. Phase 0 findings from V10.5.5 (folded in, treated as config to tune during implementation)

- CORE mechanism confirmed as SOFT_HIGH_WEIGHT in production (score += 100,000,000 for CORE,
  += 10,000,000 for KATEGORIZACE=A, + PTT, + gap adjustment) — matches the recommended V11 default.
- GECO and CORN do not exist anywhere in V10.5.5 — they are new rules for V11, not preserved
  behaviour. No migration risk; still need scope/guarantee-type values (config, not blocking).
- Mandatory 9PODNIK today = one guaranteed slot per 4-week campaign run, deduplicated by
  street+city (best PTT wins per physical location) — a one-time pick, not a recurring interval.
  V11 decision: keep this exact semantic as one CADENCE_RULES entry type, or fold into the general
  recurring-interval model? Config choice, not a blocker.
- Minimum visit period today: CORE = 2 weeks (`PREMIUM_GAP`) with an explicit override when the
  campaign material changed in the meantime ("NEW CAMPAIGN OVERRIDE"); non-CORE = 8 weeks
  (`STANDARD_GAP`), no override; neglect bonus (not enforcement) after 26 weeks. V11's
  minimum/recommended/critical period concept should generalize this, preserving the
  campaign-change override behaviour.
- "Premium"/Pareto top tier today = top 20% **within each technician's own portfolio**
  (relative ranking), not a global PPT threshold or network-wide percentile. This conflicts with
  the GLOBAL/PER_REGION/PER_MARKET scope options discussed for PARETO_GROUPS — a PER_TECHNICIAN
  scope was not previously considered and needs an explicit decision, since it changes plan
  behaviour materially (fairness across portfolios vs. absolute network-wide strength).
- Campaign Economics today = simple reorder (deprioritize top-tier POS when a campaign changes
  within `SYNC_WINDOW_WEEKS`), not full multi-week LOS+LOT combine planning. Treated as the
  starting point to extend, not a finished feature.
- GPS "nearby extra" (`NEARBY_EXTRA=5` within `MAX_DISTANCE_KM`) already implements the
  over-capacity nearby-visit rule from the original requirement ("~40+ visits near a shopping
  centre even for B/C category"). Confirmed working concept to preserve.
- **Likely defect**: `addNearby()` can push the selected set beyond weekly capacity by up to
  `NEARBY_EXTRA`, but `used` tracking marks all selected POS (including the overflow) as consumed
  even when `geoDays()` cannot physically place them into a day slot — silent loss of up to 5
  POS/technician/week within a campaign run (never visited, never logged). Flagged for
  confirmation before fixing in V11 (not fixed yet, per "ask before changing" rule).
- **Fragility**: `KATEGORIZACE` column is located positionally (`katCols[1]`, second header
  containing "KATEG") rather than by exact name — works today because of current column order in
  RAW_DATA, but silently breaks if column order changes. V11 should use exact-name lookup.
- PPT/PTT confirmed as a single field, embedded directly in RAW_DATA (`col("PTT")` fuzzy-matches
  the `PTT` header) — no separate PPT import exists in the current script.

## 15b. Decisions confirmed by product owner (this round)

1. `addNearby()` capacity-overflow defect — confirmed as a real bug, fix approved: GPS-extra
   additions must respect weekly capacity, never silently discard POS.
2. Positional `KATEGORIZACE` column lookup — confirmed to replace with exact-name lookup, to
   minimize sensitivity to future export changes.
3. GECO and CORN — confirmed as purely new V11 mechanisms; no V10.5.5 behaviour to preserve.
4. CORE — confirmed to remain an **evolution** of the existing score-based mechanism (huge
   additive weight, not a hard capacity reservation), reimplemented as a configurable
   SCORE_PROFILES weight rather than a magic constant, not redesigned as a new concept.

## 15c. Corrections to earlier design-session claims (found during deeper re-review)

- The V11 GPS "buffer pool" design (select `capacity × multiplier`, then geo-cluster) was
  earlier justified by claiming V10.5.5 already does this via `CANDIDATE_POOL=1.3`. **That claim
  was wrong** — the setting exists in CONTROL but is never read by any code. The mechanism is a
  new design for V11, not a preserved one; still recommended, but on its own merits.
- `ACTIVITY_PLAN.PRIORITY` was earlier claimed to already work ("campaigns as a general concept
  already function"). It does not — the column is dead in the current code. ★ OPEN: was this an
  intentionally unfinished feature, or should priority/gap-override be designed fresh for V11?
- VISIT_HISTORY does not currently reflect real-world visits at all — it is the script's own
  planned output written back as if it were history, with no SalesApp import feeding it. This
  elevates Compliance Engine from "nice addition" to "closes a real, currently-missing feedback
  loop" — gap-based scoring today silently drifts from reality with no correction.

## 15. Summary of ★ OPEN items blocking full sign-off

1. GECO scope + guarantee type
2. CORE mechanism (SOFT_HIGH_WEIGHT vs. own interval) and its exact condition
3. Tie-break between two competing HARD cadence rules
4. KA scope (whole KA PARTNERS market vs. KATEGORIZACE=A subset)
5. PPT threshold type/scope for IDT and Pareto tiering
6. CATEGORY_RULES default/fallback rule value
7. Force-include vs. hard filters interaction
8. Priority adjustment scale (qualitative vs. points)
9. Compliance late→not-completed cutoff
10. Extra-visit-outside-territory handling
11. Two-tier Advisor warning thresholds
12. Full V10.5.5 script still not supplied (blocks final code-level migration review)

None of these block starting implementation of the *mechanisms* (they are all config-table shaped
decisions). Per agreement, these are now treated as config values to be tuned during
implementation, not gates on starting engine-by-engine build-out.

## 16. Import/Planning decoupling, dynamic week, terminal toggles, CORN/GECO address dedup (2026-07-08)

Product owner requested 5 changes; investigation found most of the underlying mechanisms already
existed (built across earlier sessions) - this section records what was CONFIRMED already working,
what was a genuine gap and got fixed, and what was explicitly decided against, rather than blindly
re-implementing everything requested.

**1. Import/Planning decoupling**: ALREADY TRUE architecturally - `ImportEngine.ts` never reads
SalesApp/writes `weeksSinceLastVisit`/`VISIT_HISTORY_ACTUAL` (only `ComplianceEngine.ts` does), and
no engine ever calls another engine's logic; all 8 are independent scripts run manually one at a
time. Running Import never regenerates or touches `MANAGER_PLAN`. `ComplianceEngine.ts` (not
`ImportEngine.ts`, despite the ask referring to "ImportEngine") is already the "load SalesApp,
update history + weeksSinceLastVisit" engine, and it too never touches Planning. No code change.
Note: there is a legacy `VISIT_HISTORY` sheet (distinct from `VISIT_HISTORY_ACTUAL`) carried over
from V10.5.5 - it is dead data, read by no V11 engine, kept only for reference.

**2. Dynamic week - FIXED**: `CAMPAIGN_START_WEEK`/`YEAR` in `PlanningEngine.ts` now default to
TODAY's real ISO week/year (`isoWeekNumber(new Date())`, added to `PlanningEngine.ts`'s SYNC-BLOCK -
it wasn't there before, only in the compliance/reporting/performance engines) whenever the CONTROL
row is blank. An explicit CONTROL value, if present, still wins - kept as an opt-in escape hatch
(testing, or a deliberate future-dated start), not the default path. To get fully automatic
behavior, clear `CAMPAIGN_START_WEEK` and `YEAR` in `CONTROL`. Verified via `tools/sim/`: with both
blank, Planning Engine picked weeks 28-31 on 2026-07-08 (today's real ISO week 28), TS and Python
ports produce identical output.

**3. User-friendly terminal type toggles**: ALREADY FULLY IMPLEMENTED as asked - `TERMINAL_RULES`
(YES/NO per terminal type: VELKY TERMINAL, SMALL TERMINAL, LI) already exists, already has exactly
this shape, and `PlanningEngine.ts`'s `terminalOK()` already reads it before filtering candidates.
The only real gap was UX: it was hidden in the technical-sheets tab group. FIXED: unhidden (removed
from `ux_style.py`'s `HIDDEN_SHEETS`, `hide_technical_sheets()` now explicitly forces it visible),
given a header-cell guidance comment, and added as a HOME quick-link ("TYPY TERMINÁLŮ"). No new
mechanism was built - the existing YES/NO dropdown-validated table is now just reachable.

**4a. CORN/GECO maxIntervalWeeks/RECURRING**: ALREADY CONFIRMED CORRECT (from an earlier session) -
`CADENCE_RULES`: CORN `maxIntervalWeeks=4, intervalType=RECURRING, guaranteeType=HARD`; GECO
`maxIntervalWeeks=5` (same). No change needed.

**4b. "Náběh kampaně" flexible timing (CORN doesn't have to go in week 1 if capacity is tight, as
long as it doesn't breach the hard limit)**: INVESTIGATED, NOT IMPLEMENTED - found to be a phantom
requirement given the current architecture. A CORN/GECO POS only becomes a forced/mandatory
candidate (`mandatoryRuleId` set) via `isOverdueForCadenceRule()`, which only returns true once
`weeksSinceLastVisit >= maxIntervalWeeks` (or is unknown/null) - i.e. exactly when there is no more
slack left to give. Before that point, it is already just a normal scored candidate, competing on
value like everything else, which already gives it the flexibility to land in whichever week
capacity allows, without any special-casing. Attempting to add an explicit "defer while there's
still slack" mechanism on top of this is provably dead code (verified: a synthetic item with
slack never has `mandatoryRuleId` set in the first place, so the deferral check never fires) -
implemented, tested, found to have zero effect, and reverted rather than shipped as unexplained
complexity. If a future need to actively pre-empt lower-value POS for an *approaching* (not yet
overdue) CORN/GECO deadline arises, that would need a scoring-side change (a bonus as
`weeksSinceLastVisit` approaches the rule's own `maxIntervalWeeks`), not a scheduling-side one -
flagged here as a real option, not built now since it wasn't what was actually asked for.

**4c. Address deduplication - FIXED, two real bugs found via testing**:
- `CADENCE_RULES.dedupBy` for CORN and GECO changed from `NONE` to `ADDRESS` (config-only change,
  `tools/scaffold_workbook.py` and the real workbook's `CADENCE_RULES` sheet) - the existing
  `pickMandatory()` mechanism (already used by `MANDATORY_9PODNIK`) needed no code change to start
  covering CORN/GECO too.
- BUG FOUND: `pickMandatory()`'s dedup key was address-only, not scoped to the matching rule - two
  same-address POS under *different* dedupBy=ADDRESS rules (e.g. one MANDATORY_9PODNIK, one GECO)
  would have been cross-deduped against each other, a different (unintended) guarantee than either
  rule actually makes. FIXED: key is now `ruleId + "|" + address` (`core.ts`, synced into
  `PlanningEngine.ts`, ported to `core_logic.py`) - matches "spadají do stejného plánovacího
  pravidla" exactly. Existing `MANDATORY_9PODNIK` behavior is unchanged (its ruleId was already
  constant across all its matches).
- BUG FOUND: `pickMandatory()`'s dedup only ran inside `selectWeekPOS()`, per week - but
  `addGpsBonus()` afterward draws from the wider `available` pool (not `pickMandatory()`'s
  filtered result), and two same-address POS are very often GPS-adjacent too (within
  `GPS_EXTRA_RADIUS_METERS`), so the "nearby" GPS bonus could silently re-add the just-deduped
  loser right back into the same week's plan, defeating the dedup entirely. Verified with a
  synthetic same-GPS-location test: both POS were selected despite dedupBy=ADDRESS, until fixed.
  FIXED: address dedup for mandatory-eligible items now runs ONCE, right after the candidate list
  is built (before ANY week is planned), physically removing the loser from `groups[tech]` so it
  can never re-enter via any path (`PlanningEngine.ts`, ported to `planning_engine.py`).
- Real-data finding: the 16 active CORN-market POS are currently ALL also `9PODNIKC` category, so
  they were already being deduped via `MANDATORY_9PODNIK` (15/16 survived) before this change -
  CORN's own dedup rule has not yet been observed to independently trigger on real data (all 16
  are currently at `weeksSinceLastVisit=4`, one week short of CORN's own deadline). GECO's 387
  active POS include 18 same-address pairs, all currently at `weeksSinceLastVisit=4` (one week
  short of GECO's `maxIntervalWeeks=5`) - the fix is verified correct via synthetic testing
  (`tools/sim/`) but has not yet been observed firing on real data either, since nothing is
  overdue yet as of 2026-07-08.

**5. Rolling multi-week export**: ALREADY SATISFIED - `TECHNICIAN_PLAN` (`tools/ux_style.py`'s
`build_technician_plan()`) already shows a technician's ENTIRE current campaign (every week in
`MANAGER_PLAN`, Draft included, grouped by week), live-formula-driven, exportable via Print/PDF or
the desktop app's per-technician export - not just the single most-recently-published week. No
code change.

All TypeScript changes synced (`tools/check_sync.py` passes, 18 blocks), mirrored into
`desktop_client/engines/` (Python port), and verified equivalent (`tools/sim/compare_engines.py`)
on the real production dataset (11,605 POS) plus 2 synthetic edge-case seeds (GECO address dedup,
CORN deferral phantom-requirement check). Full test suites green: 107 TypeScript
(`tests/core.test.ts`), 94 Python (`desktop_client/engines/test_core_logic.py`).

## 17. Resume-from-last-week, BLACKLIST, Smart Hold-back, proactive urgency boost (2026-07-09)

Second round of refinements to the same architecture, superseding/extending some of section 16's
scope (product owner: "Zapracuj je do PlanningEngine.ts a souvisejících souborů"). Export (former
section 16 point 8) was explicitly cancelled by the product owner ("to si vyřeším sám makrem") and
is out of scope entirely - not built, not planned.

**1. Resume-from-last-week (generation +1) as the default** - IMPLEMENTED. `PlanningEngine.ts`'s
`START_WEEK` default chain changed from "today's ISO week" to: explicit
`CONTROL.CAMPAIGN_START_WEEK` override (unchanged, always wins) → one past the highest `WEEK`
already present in `MANAGER_PLAN` ("resume where the last run left off") → today's real ISO week
(only reached on a genuinely first-ever run, when `MANAGER_PLAN` is still empty). Mirrored in
`planning_engine.py`.

**2. Manual BLACKLIST** - IMPLEMENTED. New `BLACKLIST` sheet (`POS`, `NOTES` columns,
`tools/scaffold_workbook.py`/`tools/ux_style.py`, real workbook). Checked immediately after
`status=Active`, before the existing `managerOverrideType=FORCE_EXCLUDE` check - a POS in either
mechanism never enters the candidate pool. Distinct from `FORCE_EXCLUDE` in that it's a dedicated,
paste-and-scan list rather than editing individual `POS_MASTER` dropdown cells one at a time; both
co-exist.

**3. Smart Hold-back with elastic, classification-tiered lookahead - IMPLEMENTED, as a NEW,
stronger mechanism alongside (not replacing) the pre-existing `campaignChangeSoon()`/`holdPremium`
soft tie-break already in `selectWeekPOS`'s sort.** Three new pure functions added to
`office-scripts/shared/core.ts` (synced into `PlanningEngine.ts`, ported to `core_logic.py`):
- `campaignStartsWithin(activityPlan, week, lookaheadWeeks)` - true if any `ACTIVITY_PLAN` campaign
  starts strictly after `week` and at or before `week + lookaheadWeeks` (a campaign already running
  as of `week` does not count).
- `shouldHoldBack(classification, weeksSinceLastVisit, deadlineWeeks, activityPlan, week, config)` -
  deliberately conservative: never defers unknown history (`weeksSinceLastVisit === null`, already
  maximally urgent by convention elsewhere), never defers past `deadlineWeeks` (the item's own
  matched `RECURRING`+`HARD` `CADENCE_RULES` row's `maxIntervalWeeks` if any, else
  `NEGLECTED_AFTER_WEEKS` - computed once per item at candidate-build time in `PlanningEngine.ts`/
  `planning_engine.py`, stored as `POSItem.deadlineWeeks`). Tolerance is classification-tiered:
  classification A gets `HOLDBACK_TOLERANCE_A_WEEKS` (default 1), everything else gets
  `HOLDBACK_TOLERANCE_OTHER_WEEKS` (default 3), both capped by `HOLDBACK_LOOKAHEAD_WEEKS` (default
  3, the widest possible elastic window).
- `computeUrgencyBoost(...)` - see point 4 below.

Wired into `PlanningEngine.ts`'s per-week candidate loop: non-mandatory items (`mandatoryRuleId ===
null`) for which `shouldHoldBack` returns true are removed from that week's `available` pool
entirely (a hard pool-removal, not a soft sort tie-break). Mandatory items are never held back - a
hard guarantee is not up for deferral once it applies. Capacity freed this way cascades
automatically to whatever else is competing that week, since `available` simply has fewer entries -
no separate "refill" mechanism needed, it falls out of the existing per-week loop structure.
Verified via 18 new unit tests (both languages) plus an end-to-end synthetic seed run through the
real `PlanningEngine.ts`/`planning_engine.py` (a classification-B GECO-category POS with 4 weeks of
slack against its own 5-week deadline, and an upcoming campaign 2 weeks out, was correctly excluded
from weeks 31-32 and picked up starting week 33 once the campaign was no longer "upcoming" - and the
Python port produced a byte-identical `MANAGER_PLAN`).

**4. Proactive urgency boost for POS approaching their own deadline** - IMPLEMENTED as a smooth
linear score ramp (`computeUrgencyBoost`, NOT a step function like the existing
`NEGLECTED_AFTER_WEEKS` bonus already inside `computeScore()`): 0 below `rampStartRatio` (default
0.5, i.e. halfway to `deadlineWeeks`), ramping linearly up to `URGENCY_BOOST_MAX` (default 20000)
exactly at the deadline. Applied as a separate additive pass (like the existing geo cluster bonus),
run BEFORE the geo cluster bonus pass so a boosted item's real value feeds its neighbors' cluster
bonus correctly - not folded into `computeScore()` itself, so that function's own tested contract
is untouched. `URGENCY_BOOST_MAX` is kept well below `NEGLECTED_BONUS` (50000) and classification A
(10000000) so it only ever nudges a POS ahead of other non-neglected competition as its deadline
approaches, never overriding the existing hard priority tiers.

All five new `CONTROL` settings (`HOLDBACK_LOOKAHEAD_WEEKS`, `HOLDBACK_TOLERANCE_A_WEEKS`,
`HOLDBACK_TOLERANCE_OTHER_WEEKS`, `URGENCY_BOOST_MAX`, `URGENCY_BOOST_RAMP_START_RATIO`) are
config-driven with documented defaults (section 0 "config over code"), added to
`tools/scaffold_workbook.py` and patched into the real workbook's `CONTROL` sheet.

**5. HOME UI additions - IMPLEMENTED** (`tools/ux_style.py`'s `build_home()`):
- Post-generation summary: a one-line live-formula callout right under the pipeline stages -
  "✅ Vybráno X poboček do plánu, celkové PPT: Y" (distinct-POS count over `MANAGER_PLAN`, same
  `SUMPRODUCT`/`COUNTIF` distinct-count pattern already used elsewhere on HOME; PPT is a plain
  `SUM`), or "Zatím žádný vygenerovaný plán" before the first Planning Engine run.
- Terminal-type "last used" countdown: a new "TERMINÁLY - PRŮMĚRNÝ POČET TÝDNŮ OD NÁVŠTĚVY" section
  with one tile per terminal type (VELKY TERMINAL / SMALL TERMINAL / LI), each an `AVERAGEIFS` over
  `POS_MASTER.weeksSinceLastVisit` for `status=Active` POS of that type. Severity-colored (WARNING/
  CRITICAL) using the SAME `NEGLECTED_AFTER_WEEKS`/`ADVISOR_NEGLECT_WARNING_RATIO_PERCENT` `CONTROL`
  thresholds Advisor Engine itself uses, deliberately - this headline agrees with whatever
  `ADVISOR_LOG` would eventually flag rather than inventing a second threshold.

**6. Daily technician stats (planned vs. ad-hoc, per day) - IMPLEMENTED.** `PerformanceEngine.ts`'s
`Bucket` gained `otherVisitsByDay` (Mon-Fri), populated from `OTHER_VISIT_LOG`'s own `date` column
with the same day-of-week bucketing `visitsByDay` already uses for `COMPLIANCE_LOG`'s realized-visit
dates. Five new `TECHNICIAN_PERFORMANCE_LOG` columns (`otherVisitsMon`..`otherVisitsFri`) appended at
the END of the row (after `monthKey`) so existing column-index-based readers
(`TECHNICIAN_SCORECARD`/`PERFORMANCE`) are unaffected. Verified via a synthetic seed (3 ad-hoc visits
- 2 Monday, 1 Tuesday - run through both `PerformanceEngine.ts` and `performance_engine.py`) showing
byte-identical `otherVisitsMon=2, otherVisitsTue=1` output.

All TypeScript changes synced (`tools/check_sync.py` passes, 18 blocks), mirrored into
`desktop_client/engines/`, and verified equivalent (`tools/sim/compare_engines.py`) on the real
production dataset (11,605 POS, `ImportEngine.ts`+`PlanningEngine.ts` pipeline) plus synthetic Smart
Hold-back and daily-stats seeds. Full test suites green: 127 TypeScript (`tests/core.test.ts`), 114
Python (`desktop_client/engines/test_core_logic.py`).

## 18. Monitoring efektivity - route efficiency detection (2026-07-09)

Product owner, speaking explicitly as vedoucí Field Force týmu ("chci se na dashboard jen podívat a
hned vidět, kdo ze mě dělá blbce"): implement a real actual-vs-optimal route efficiency signal, not
just raw daily km (which already existed - `maxKmDay`/`ROUTE_KM_WARNING_KM`/`ROUTE_KM_CRITICAL_KM`).

**"Matematické minimum" - `computeOptimalRouteKm()`** (`office-scripts/shared/core.ts`, synced into
`PerformanceEngine.ts`, ported to `core_logic.py`): the shortest possible OPEN path (free start, free
end - no known depot, same reasoning as `geoDays()`'s own comment) visiting a given day's
GPS-resolvable stops exactly once. Exact multi-source Held-Karp dynamic program for up to 13 points
(a realistic daily visit count given `TARGET_VISITS_DAY` + GPS bonus overflow), `O(2^n * n^2)`; falls
back to a nearest-neighbor heuristic (tried from every possible start, keeping the best) beyond that,
rather than growing exponentially unbounded. Verified via 6 new unit tests per language (collinear
points, order-independence, the >13-point fallback).

**Weekly metrics** (`PerformanceEngine.ts`/`performance_engine.py`, both `TECHNICIAN_PERFORMANCE_LOG`
and `TECHNICIAN_PERFORMANCE_SUMMARY`):
- `totalActualKmWeek` / `totalOptimalKmWeek`: summed only across days where BOTH actual and optimal
  are measurable (>=2 GPS-resolvable stops) - a single-stop day has no "route" to be efficient or
  inefficient about, and would otherwise dilute the ratio with a meaningless 0/0.
- `efficiencyRatioPercent` = actual/optimal * 100 (blank, not 0, when nothing is measurable yet -
  a real 0% "perfect" score must never be indistinguishable from "no data").
- `kmPerVisit` = totalActualKmWeek / realizedVisits.
- `efficiencyFlag` = "KRITICKÉ" (>= `ROUTE_EFFICIENCY_CRITICAL_PERCENT`, default 150 - the explicit
  "o 50 %+ vyšší než optimum" bar given by the product owner), "POZOR" (>= `ROUTE_EFFICIENCY_WARNING_PERCENT`,
  default 125), else "OK".
- `TECHNICIAN_PERFORMANCE_SUMMARY` additionally carries `longRunAvgEfficiencyRatio` - the SAME
  `FLAKANI_WINDOW_WEEKS` trailing window already used for "flaká riziko", so a single bad-route week
  (a diverted/forced detour) doesn't trigger KRITICKÉ on its own; a sustained pattern does. This is the
  signal the summary's own `efficiencyFlag` is actually based on, not the latest single week.

**New screens** (`tools/ux_style.py`):
- `EFFICIENCY` - a ranked heatmap, technicians sorted worst-to-best by `longRunAvgEfficiencyRatio`
  automatically (a single `LET()`+`FILTER()`+`SORTBY()` dynamic-array spill, deliberately NOT a native
  Table like `PERFORMANCE` - Excel does not allow a spilling formula inside a Table's range, and this
  screen is meant to already be sorted, not something a manager re-sorts themselves), a green→yellow→red
  `ColorScaleRule` on both ratio columns (the actual "heatmap"), and an auto-surfaced "🚩 X technik(ů)
  KRITICKY neefektivních: [jména]" callout - the "systém mi to sám vystrčí" requirement.
- `MANUAL` - static interpretation guide (Czech), written from the Field Force lead's own perspective:
  what the two headline numbers mean, how to read the color bands, what is/isn't a real signal (a
  single bad day vs. a sustained pattern, POS with no GPS on record, the "assumed planned order"
  limitation), concrete escalation thresholds (1x KRITICKÉ = watch, 2x in a row = sit down with them,
  3x+/sustained = combine with compliance % and flaká riziko), and a step-by-step pre-confrontation
  checklist.
- `HOME` gained a new "KDO JEZDÍ CIK-CAK" callout (distinct from the existing raw-km-based "KDO JEZDÍ
  NEEFEKTIVNĚ") keyed off the new ratio signal, linking straight to `EFFICIENCY`.

Two new `CONTROL` settings (`ROUTE_EFFICIENCY_WARNING_PERCENT`=125, `ROUTE_EFFICIENCY_CRITICAL_PERCENT`=150),
config-driven per section 0. Verified end-to-end with a synthetic seed (a deliberately zig-zagged
`MANAGER_PLAN_PUBLISHED` visiting order against 3 collinear GPS points) producing a byte-identical
150% ratio / KRITICKÉ flag in both `PerformanceEngine.ts` and `performance_engine.py`
(`tools/sim/compare_engines.py`), plus the full 8-engine pipeline re-verified equivalent on the real
11,605-POS production dataset. Full test suites green: 133 TypeScript (`tests/core.test.ts`), 120
Python (`desktop_client/engines/test_core_logic.py`).

**Known limitation, stated plainly to the product owner**: `VISIT_HISTORY_ACTUAL`/`OTHER_VISIT_LOG`
are still empty in the real workbook (no SalesApp import has happened yet), so `EFFICIENCY`/
`TECHNICIAN_PERFORMANCE_LOG`/`SUMMARY` will show "Zatím žádná data" until the first real import runs
- this is expected, not a bug, and was verified via the synthetic seed above rather than real data.

## 19. "Manažerské" triggery - podprůměrná návštěvnost, hodnotová hustota, délka návštěvy (2026-07-09)

Product owner, explicitly stepping back from the programmer role to the Field Force team lead role:
route efficiency alone ("KDO JEZDÍ CIK-CAK") is not enough - a technician can have a great route
shape while under-visiting relative to peers, or visiting only low-value POS, and GPS-based route
efficiency is only an estimate that "nemusí být na vinu". Three new signals added, all compared
against the **network peer average the same week** (a plain average across whichever technicians
have a bucket that week - deliberately simple, not a median; with ~27 technicians one outlier's own
pull on the average it's being compared against is small):

- **Návštěvnost (`volumeFlag`)**: `realizedVisits` vs. the network peer average AND the technician's
  own trailing average (excluding the current week) - both requested explicitly ("obojí najednou"),
  the flag uses whichever comparison is more severe. Catches a gap Compliance % cannot see: a
  technician planned very little who hits 100% compliance while doing objectively less work than
  peers.
- **Hodnotová hustota (`pptDensityFlag`)**: PPT captured per realized visit (`pptPerVisit`) vs. peer
  average - independent of route efficiency. "Hodně návštěv, ale jednoúčelové": a technician can have
  a perfect route shape while visiting only low-value POS; this is the only signal that catches that.
- **Délka návštěvy (`durationFlag`)**: average `Real duration (h)` from SalesApp
  (`avgVisitDurationHours`) vs. peer average - a directly-measured signal (not a GPS estimate).
  Required extending `ComplianceEngine.ts` to parse and carry a new `durationHours` column through
  `VISIT_HISTORY_ACTUAL`/`OTHER_VISIT_LOG` into `COMPLIANCE_LOG.matchedActualDurationHours` (null,
  never 0, when the SalesApp export doesn't carry the column or a row's value is non-numeric).

All three follow the same WARNING/CRITICAL-below-a-percentage convention as route efficiency, but
inverted (LOW is bad): `*_WARNING_PERCENT`=70, `*_CRITICAL_PERCENT`=50 (new `CONTROL` settings:
`VOLUME_WARNING_PERCENT`/`VOLUME_CRITICAL_PERCENT`, `PPT_DENSITY_WARNING_PERCENT`/`_CRITICAL_PERCENT`,
`DURATION_WARNING_PERCENT`/`_CRITICAL_PERCENT`), and use the SAME `FLAKANI_WINDOW_WEEKS` sustained
long-run averaging as flaká riziko/route efficiency on `TECHNICIAN_PERFORMANCE_SUMMARY` (a single bad
week is never enough on its own).

**Kombinovaný signál (`combinedRiskFlag`)** - the product owner's explicit correction after reviewing
the route-efficiency-only design: "GPS je odhad, takže to ani nemusí být na vinu". No single signal
(including a KRITICKÉ route ratio) surfaces a technician as "problémový" on its own anymore. A new
`PROBLEM_SIGNAL_MIN_COUNT` `CONTROL` setting (default 2) gates it: `combinedRiskFlag='Ano'` only when
at least that many of {flaká riziko, `volumeFlag`, `pptDensityFlag`, `durationFlag`, `efficiencyFlag`}
are simultaneously POZOR/KRITICKÉ. This is now what drives every automatic "problémový technik"
surface - `EFFICIENCY`'s callout and sort order (worst = most corroborating signals, not worst route
ratio), and HOME's renamed "KDO ZE MĚ DĚLÁ BLBCE" callout (was "KDO JEZDÍ CIK-CAK").

Verified via a dedicated synthetic seed (`LOW_TECH`: 1 realized visit / low PPT / short duration
against a 4-technician `NORMAL_TECH` peer baseline) producing `volumeFlag`/`pptDensityFlag`/
`durationFlag` all KRITICKÉ, `activeSignalCount=3`, `combinedRiskFlag='Ano'` - byte-identical in both
`PerformanceEngine.ts` and `performance_engine.py`. Also re-verified, using the existing
`efficiency_synth_seed` (KRITICKÉ route ratio, otherwise clean), that a LONE efficiency signal
produces `activeSignalCount=1` and `combinedRiskFlag='Ne'` - confirming Trigger C's "no single signal
alone" requirement holds. Full 8-engine pipeline re-checked equivalent on the real 11,605-POS
production dataset. Full test suites green: 133 TypeScript, 120 Python.

`MANUAL` rewritten with new sections explaining the three triggers, the combined-signal gate as the
primary thing to look at, and updated escalation guidance (1 signal = watch, `combinedRiskFlag='Ano'`
= sit down with them, repeated `Ano` / high signal count = full performance conversation).

## 20. Skutečný pracovní den - display of daily visit counts + real work-span/idle time (2026-07-11)

Product owner: "chybi mi tam zobrazení kolik udelal za den a podobně, neni ten salesapp pořádně
vytezeny" (missing a display of daily output, SalesApp isn't being properly mined), then clarifying
"také tam nevidím ten čas" (also don't see that time there) - two related but distinct gaps:

1. **Daily visit counts were already computed but never shown as a table.** `visitsMon..Fri`
   (campaign) and `otherVisitsMon..Fri` (ad-hoc) already existed on `TECHNICIAN_PERFORMANCE_LOG`
   (used elsewhere - the daily bar chart, the "Ostatní návštěvy" KPI) but had no dedicated per-day
   breakdown table. Pure visibility fix, no engine change: new "NÁVŠTĚVY PO DNECH (kampaň / ostatní)"
   table on `TECHNICIAN_SCORECARD`.
2. **Real clock time genuinely did not exist anywhere.** SalesApp's `Started at`/`Finished at`
   columns were imported nowhere before this - only `Real duration (h)` (2026-07-09, trigger C) was
   mined, which gives total active minutes but not *when* the day started/ended or how much idle time
   sat between visits. This needed new engine logic, not just a new table.

**New engine logic** (`ComplianceEngine.ts`/`compliance_engine.py` parse `Started at`/`Finished at`
from `SALESAPP_IMPORT`, carry them through `VISIT_HISTORY_ACTUAL`/`OTHER_VISIT_LOG`/
`COMPLIANCE_LOG.matchedActualStartedAt`/`matchedActualFinishedAt`; `PerformanceEngine.ts`/
`performance_engine.py` aggregate per technician per day, across BOTH campaign and ad-hoc visits
(a technician's idle time should reflect their whole day in the field, not just campaign stops):

- `workSpanHours` = latest `Finished at` minus earliest `Started at` that day.
- `idleHours` = `max(0, workSpanHours - sum(durationHours that day))` - the gap between "on the clock"
  and "actually recorded busy in SalesApp".

Both are `null` (blank cell, not 0) for a day with no timing data at all, or if `Finished at <= Started
at` (bad data). New `TECHNICIAN_PERFORMANCE_LOG` columns: `workSpanHoursMon..Fri`, `idleHoursMon..Fri`
(10 new columns, appended at the end - existing column-index readers unaffected). **Deliberately
informational only, no new flag/threshold** - the product owner asked for "zobrazení" (a display), not
a new trigger; unlike route efficiency or the three manažerské triggers, no WARNING/CRITICAL semaphore
was requested or added for idle time. If this proves useful as a trigger later, it should get the same
propose-then-confirm treatment as every other flag in this workbook, not be added silently.

New `TECHNICIAN_SCORECARD` table "PRACOVNÍ DEN - REÁLNÝ ČAS (SalesApp Started/Finished at)" reads
these via `FILTER()`+`INDEX()` (not the `SUMPRODUCT(condition*range)` pattern used for `kmMon..Fri`
elsewhere on this sheet) - `workSpanHours`/`idleHours` contain `""` for no-data days, and
`SUMPRODUCT`'s implicit arithmetic on a text value anywhere in the range throws `#VALUE!`; `FILTER`/
`INDEX` never perform arithmetic on the matched cell, so blank days render safely as "-".

**Bug found and fixed during verification**: `PerformanceEngine.ts`'s `ComplianceRow.matchedActualDate`
parsing used `dateVal instanceof Date ? dateVal : null` - strict, whereas `performance_engine.py`'s
port already used the lenient `_to_date()` (parses a plain ISO string too). Excel auto-detects a
simple `YYYY-MM-DD` string as a real Date cell *on read-back after a save*, but a same-run Office
Scripts execution (and the `tools/sim` mock workbook) does not - `ComplianceEngine.ts` writes
`matchedActualDate` as a string, so within one script run, this field was `null` and silently dropped
the technician from `visitsMon..Fri`/`posListMon..Fri`/the new work-span/idle aggregation. This was a
latent bug predating this feature (the day-of-week visit count and POS list were exposed to the same
risk), not introduced by it - fixed by routing `matchedActualDate` through the same `parseCellDate()`
helper already used for `matchedActualStartedAt`/`matchedActualFinishedAt`.

Verified via a dedicated synthetic seed (1 technician, 2 matched campaign visits 08:00-09:00 and
10:00-10:30, 1 ad-hoc visit 14:00-14:15, all Monday) run through `ComplianceEngine.ts` +
`PerformanceEngine.ts` and their Python ports: `workSpanHoursMon` = 6.25h (08:00 to 14:15, spanning
both campaign and ad-hoc visits), `idleHoursMon` = 4.5h (6.25h span minus 1.75h summed duration) -
byte-identical between both engines after the `matchedActualDate` fix (previously diverged: the TS
side under-counted `visitsMon`, `avgVisitDurationHours`, `workSpanHoursMon`, `idleHoursMon` due to the
bug above). `python3 tools/check_sync.py` passed (18 blocks); full TS unit suite green (133 tests).
Real workbook patched: `VISIT_HISTORY_ACTUAL`/`OTHER_VISIT_LOG` +2 columns each, `COMPLIANCE_LOG` +2,
`TECHNICIAN_PERFORMANCE_LOG` +10; `POS_MASTER`/`RAW_DATA` row counts unchanged (11,606/11,607) after
patching, `TECHNICIAN_SCORECARD` rebuilt with the two new tables. Follow-up
(2026-07-11, same day): the weekly work-span/idle totals got their own KPI-style tile
("Týden celkem", same visual treatment as the route-km weekly total) so they read at a
glance instead of only per-day; full 8-engine pipeline re-verified equivalent on the
real 11,605-POS dataset (`compare_engines.py`).

**"POS BEZ NÁVŠTĚVY (nikdy)"** (product owner, same day, follow-up: "rad bych i někde
viděl jaké POS v kampani vůbec nejel") - a new `TECHNICIAN_SCORECARD` table listing
Active POS assigned to the selected technician that have never once appeared in
`VISIT_HISTORY_ACTUAL` - pure sheet-level addition, no engine change, since the data
already exists. Distinct from "TOP PROBLÉMOVÉ POS" above it (which ranks POS by *how
many times* a planned visit was missed, on POS that HAVE been visited before) - this
is specifically POS with zero real campaign visits ever.

**Real data caught a bad initial design**: the first version used `POS_MASTER`'s
`lastRealVisitDate` (blank = never visited) as the signal, matching the existing
"TOP PROBLÉMOVÉ POS" section's style. Checked against the real workbook before
shipping and found it always non-blank: `ImportEngine.ts` seeds `lastRealVisitDate` to
"today" for every brand-new POS ("product owner confirmed that installation counts as
the first visit" - see that file's comment), so on real data every single Active POS
already carries a fabricated first-visit date, and the feature would have always shown
zero results. Switched to checking a POS's total absence from `VISIT_HISTORY_ACTUAL`
instead (`ISNA(MATCH(POS_MASTER!posId, VISIT_HISTORY_ACTUAL!posId, 0))`) - that sheet
only ever gets a row when `ComplianceEngine.ts` processes a real, campaign-purpose
SalesApp visit, so it's untouched by the install-day default and also correctly
excludes ad-hoc/`OTHER_VISIT_LOG` visits from counting as "the campaign visit
happened" (matches the literal ask: "v kampani vůbec nejel"). Verified in Python
against the real workbook: with `VISIT_HISTORY_ACTUAL` still empty (no SalesApp import
has happened yet on the real workbook), all 11,605 Active POS currently show as
"never visited" - expected given no real import has run yet, same "known limitation"
pattern already documented for `EFFICIENCY`/`TECHNICIAN_PERFORMANCE_LOG` above; the
number becomes meaningful after the first real SalesApp import.

## 21. Tourplan freshness, campaign completion dashboard, bulk POS activation (2026-07-11)

Four more requests from the same session, in order: "potřebuji opravdu viditelně vidět,
že ten tourplan bude aktuální ještě x dní a viditelně chci dát generovat nový, který
tourplan"; "hloubková kontrola, že všechno funguje"; a team-wide "kolik POS z kampaně už
má hotovo a kolik mu chybí" dashboard; and a bulk-add tool with an explicit correction
mid-conversation that it must never change POS ownership.

**Deep audit (before any new work)**: full 8-engine pipeline (Import through Reporting)
re-run on the real 11,605-POS dataset, TS vs Python confirmed equivalent
(`compare_engines.py`), `check_sync.py` (18 blocks at the time), both unit test suites
green (133 TS / 120 Python), no literal error strings found in any workbook cell. Also
used this pass to confirm the exact CATEGORY_RULES state driving the 4th feature below:
`1CD` and `1POSTA` both resolve to `EXCLUDE` (everything else starting with "1" defaults
to `CORE` via the `STARTS_1` wildcard row) - ~3,316 Active POS currently excluded this way.

**"PLÁN JE AKTUÁLNÍ JEŠTĚ X DNÍ"** - the single most prominent thing on HOME now (right
after the KPI row). Derives the published plan's last valid day from `PLAN_LIFECYCLE`'s
plain numeric year/week columns via ISO week arithmetic
(`DATE(Y,1,4)-WEEKDAY(DATE(Y,1,4),3)+(W-1)*7` is always that ISO week's Monday), rather
than from `MANAGER_PLAN_PUBLISHED`'s DATE column - `PlanningEngine.ts` writes that column
as a locale-formatted STRING (`toLocaleDateString("cs-CZ")`), and this session had
already found one real bug (see section 20's `matchedActualDate` fix) from trusting a
same-run string to behave like a real Date. A color-escalating day-count KPI tile (green
≥3 days, orange 1-2, red ≤0/"VYPRŠEL") plus a plain-language sentence naming the exact
next two scripts to run (`PlanningEngine.ts` → `PublishEngine.ts`). No literal
"click to regenerate" button is possible from an openpyxl-generated `.xlsx` - Office
Scripts button-binding ("Přidat tlačítko") is an Excel Online UI action performed on the
live workbook, not something storable in the file itself - so `NAVOD_INSTALACE.md`
documents that as a one-time manual step instead.

**Campaign completion, team-wide (`PERFORMANCE` sheet)** - three new columns ("POS v
kampani", "Hotovo", "Chybí") extending the existing native-Table technician comparison,
using the exact same "ever appeared in `VISIT_HISTORY_ACTUAL`" definition as section 20's
per-technician `TECHNICIAN_SCORECARD` table, just aggregated per technician instead of
filtered to one. The "is this POS ever visited" `MATCH` against a 200,000-row range is
computed ONCE as a hidden spilled helper column (`T`) rather than inside each of up to 60
per-technician `SUMPRODUCT`s - re-running that `MATCH` per row would multiply an already
11,605-row scan by the technician count, unnecessarily expensive for a Table that
recalculates on every sort/filter. The pre-existing `ROUTE_KM_WARNING_KM`/`_CRITICAL_KM`
threshold cells were relocated from hidden column Q to hidden column U, since Q became a
visible table column ("Hotovo") - they'd otherwise surface as stray bare numbers above it.

**Bulk POS activation** - product owner, asked directly whether this should reassign POS
ownership: "já nikdy do jejich 'přiřazení'... měnit nechci, ja chci mít možnost je přidat
jako máme třeba teď vyřazené 1CD, ale chci mít možnost určit buďto jaké, nebo prvních 500
nehledě kolik techniků to bude" - explicitly NOT a reassignment tool, and explicitly
allowed to spread across however many technicians the selected POS already belong to.
New `ActivatePOSEngine.ts` (+ `activate_pos_engine.py` port, registered in
`desktop_client/engines/run_pipeline.py` as `"activate_pos"`, NOT part of the default
Import→Planning→Publish pipeline - opt-in only) sets `POS_MASTER.managerOverrideType` to
`FORCE_INCLUDE` on selected POS - the exact mechanism `PlanningEngine.ts` already had for
"manually include a POS a category rule would otherwise filter out" - never touches
`assignedTechnician`. Two mutually exclusive selection modes, matching the product
owner's exact wording ("podle mého seznamu nebo ppt"):
- **Explicit list**: POS IDs pasted into a new `POS_ACTIVATE_LIST` sheet (same minimal
  paste-list convention as `BLACKLIST`, opposite direction - "activate" instead of
  "always exclude"). Wins whenever it has any rows.
- **Count by PPT**: new `CONTROL.ACTIVATE_COUNT_BY_PPT` setting (default 0 = disabled).
  Only used when `POS_ACTIVATE_LIST` is empty. Builds the pool of Active POS whose
  `CATEGORY_RULES` rule resolves to `EXCLUDE` and that aren't already
  `FORCE_INCLUDE`/`FORCE_EXCLUDE`, sorts by `ppt` descending, activates the top N.

An explicit `FORCE_EXCLUDE` override always wins over either mode (never silently
overridden - reported back in the run summary). Idempotent by construction: a POS already
`FORCE_INCLUDE` is left untouched (no duplicate note appended), and the count mode's pool
always excludes anything already activated, so re-running with the same N naturally picks
up the next-highest-PPT still-excluded POS rather than repeating the same ones - verified
directly (re-ran a count-mode seed's own output back through the engine: picked up
exactly the next-ranked POS, none of the already-activated ones touched again).

`POS_ACTIVATE_LIST` also carries a live preview block (columns D onward, keeping A:B pure
paste-input) - a formula approximation of `categoryRule()`'s exact-match > `STARTS_1` >
`*`-wildcard priority, built as one `LET()` spilling an array over all of `POS_MASTER`
(no `BYROW`/`LAMBDA` - kept to the same dynamic-array function set already proven
supported elsewhere in this workbook: `FILTER`/`SORT`/`LET`/`CHOOSE`/`IFS`/`AGGREGATE`/
`SUMPRODUCT`), so the manager can see the actual candidate pool and their PPT before
running the script, not just trust a number. The preview is informational only - the
engine, not this formula, is the authority on what actually gets activated.

Verified with two dedicated synthetic seeds (6 POS: 3 eligible-excluded at different PPT,
1 non-excluded, 1 `FORCE_EXCLUDE`, 1 Closed) covering both selection modes - byte-identical
`POS_MASTER.managerOverrideType`/`plannerNotes` output between `ActivatePOSEngine.ts` and
`activate_pos_engine.py`. `check_sync.py` passed (20 blocks, 9 deployable scripts - first
engine added to that list since `StartTrackingEngine.ts`). Full test suites green (133 TS,
120 Python). Real workbook patched: new `POS_ACTIVATE_LIST` sheet, new
`CONTROL.ACTIVATE_COUNT_BY_PPT` row, `PERFORMANCE` +3 columns, `HOME`'s pipeline-stage row
numbers shifted (+5 rows for the new freshness callout) - `POS_MASTER`/`RAW_DATA` row
counts unchanged (11,606/11,607) after every patch, as always.

## 22. OZ as a distinct entity + real SalesApp historical backfill (2026-07-11)

Product owner sent two real SalesApp exports (Feb-Apr and May-Jul 2026, 38,233 visit
rows combined) and clarified something the codebase hadn't modeled yet: SalesApp's
`Executor` column is shared by two distinct roles - field technicians (who this whole
workbook plans routes for) and **OZ** ("obchodní zástupce" - a separate role; exactly 3
people on the real export). "OZ má ty 3 čísla na začátku a technik ne" - an OZ's Executor
name is prefixed with a number starting with "3" (e.g. "301 Renata Němečková"); a
technician's is either unprefixed or a code NOT starting with "3" (the export also has
1xx/2xx/4xx-7xx codes - real technicians, not OZ). Confirmed: OZ visits are real and
should be kept as evidence, but we don't build tourplans for OZ and their activity must
never be attributed to whichever technician `POS_MASTER` happens to have assigned to
that POS - a real gap, since `PerformanceEngine.ts`'s `otherVisits`/work-span
aggregation attributes every `OTHER_VISIT_LOG` row to `posTechnician[posId]` regardless
of who actually made the visit; without this fix, OZ activity (particularly heavy around
"Malý terminál"/"Aktivita"/"Expanze" purposes) would have inflated technicians'
ad-hoc-visit and idle-time stats with visits they never made.

**`isOzExecutor()`** (`ComplianceEngine.ts`/`compliance_engine.py`): the Executor
string's leading whitespace-delimited token is a number starting with "3"
(`/^3\d*$/`). Checked BEFORE the campaign-purpose branch, not after - an OZ row is
routed away regardless of whether its own `MCHD - Nabeh kampane` column happens to say
"Ano" (in practice OZ purposes live in separate SalesApp columns entirely, but this
doesn't rely on that). New **`OZ_VISIT_LOG`** sheet (same 9-column shape as
`OTHER_VISIT_LOG`, its own dedup-by-UID set) - `PerformanceEngine.ts` never reads it, so
OZ visits structurally cannot reach any technician's stats.

**Historical backfill needed a second fix**: `ComplianceEngine.ts` had an early-return
guard - `MANAGER_PLAN_PUBLISHED` empty -> bail out entirely, "run Planning Engine then
Publish Engine first." No plan has ever been published on the real workbook, so this
guard would have refused to import 4+ months of real, valuable visit history. Traced the
guard: nothing downstream actually depends on `MANAGER_PLAN_PUBLISHED` being non-empty -
`VISIT_HISTORY_ACTUAL`/`OTHER_VISIT_LOG`/`OZ_VISIT_LOG` import and `POS_MASTER`'s
`lastRealVisitDate`/`weeksSinceLastVisit` updates are entirely independent of it; an
empty `plannedSet` just means every visit lands in `COMPLIANCE_LOG` as
`Navic_evidovano` (same as any POS visited outside its planned week today) instead of
matching nothing. Removed the guard (kept the `SALESAPP_IMPORT` empty check). Verified
with a dedicated seed (empty `MANAGER_PLAN_PUBLISHED`, one real visit) - both engines
import correctly, `Navic_evidovano` written, `POS_MASTER` updated, no crash.

**Real import result** (via `desktop_client/xlsx_engine_io.py` + a one-off script
running `compliance_engine.run()`/`advisor_engine.run()` directly against the real
workbook, backed up first): 12,509 realized campaign visits imported, 12,954
other-purpose technician visits, **1,604 OZ visits correctly routed to `OZ_VISIT_LOG`**,
6,065 `POS_MASTER` rows updated with real `lastRealVisitDate`/`weeksSinceLastVisit`, 98
Advisor Engine alerts (97 neglect risk, 1 volume trend). Verified TS vs Python
equivalent on this exact real 38,233-row import via `tools/sim/xlsx_to_json.py` +
`run_e2e.ts` + `compare_engines.py` (now also comparing `OZ_VISIT_LOG`) - byte-identical
across every compared sheet. `POS_MASTER`/`RAW_DATA` row counts unchanged (11,606/11,607)
as always. `TECHNICIAN_PERFORMANCE_LOG`/`SUMMARY` remain empty after this import - by
design: `PerformanceEngine.ts` only includes a week whose `PLAN_LIFECYCLE` row has a
non-blank `trackingStartedAt`, and no plan (past or present) has been published/tracked
yet on the real workbook, so there is nothing to compute a compliance % against for this
historical period. That's expected, not a bug - the value of this backfill is seeding
`lastRealVisitDate`/neglect tracking and the "POS bez návštěvy"/"Hotovo/Chybí" dashboards
with real signal now, so Planning Engine's cadence/neglect logic and those dashboards are
correct from the very first future plan onward.

**Still open** (product owner's follow-up requests, not yet implemented - see the
in-progress task list): translating the uploaded `Activity_plan_2026_01.xlsx` timeline
into `ACTIVITY_PLAN` rows; a campaign-scoped coverage/capacity-feasibility advisor
("losy campaign - do we cover the whole network minus LI in time?", "you'd need to raise
weekly capacity to hit this deadline"); a per-campaign target-terminal-type setting
(confirmed: "Nastavitelné v CONTROL per kampaň", not a fixed rule); TERMINAL_RULES
toggling stays a manual action, the system only warns (confirmed: "Manuálně přepnuš
TERMINAL_RULES sama/sám").

## 23. ACTIVITY_PLAN campaign coverage/capacity feasibility (2026-07-11)

Follow-up to section 22 - product owner: "chci aby tam dokázala fungovat i nějaká
predikce, když tam nasekám takto ten activity plán... chci aby mi to na začátku řeklo
hele tady by bylo dobré jet i malé terminály", "obecně platí, že jsou lehce důležitější
losy", concrete example "na Vánoce s losy objet celou síť kromě LI", and "systém by mi
řekl: hele ale to jim musíš zvýšit týdenní kapacitu".

Two new live-formula columns on `ACTIVITY_PLAN` (own column block, well clear of the
existing timeline/estimate columns - zero risk to any of their formulas), per campaign
row:
- **CÍLOVÝ POČET POS** - the target POS count for that row's campaign, built from Active
  `POS_MASTER` counts by terminal type. `VELKY TERMINAL` is always in scope (the only
  currently-Active type network-wide); `SMALL TERMINAL`/`LI` are only counted if new
  `CONTROL.{LOS,LOT}_TARGET_INCLUDES_{SMALL,LI}` (5 new settings, confirmed defaults:
  `LOS_TARGET_INCLUDES_SMALL=YES`, `LOS_TARGET_INCLUDES_LI=NO`, matching the Christmas
  example; `LOT_TARGET_INCLUDES_SMALL=NO`, `LOT_TARGET_INCLUDES_LI=NO` - proposed,
  narrower by default, matching "losy jsou lehce důležitější"). Not a fixed rule - "each
  campaign type" is the CONTROL-configurable unit, per the product owner's explicit
  correction when a fixed-terminal-set proposal was floated first.
- **STIHNEŠ TO? / DOPORUČENÍ** - compares that target against the existing
  `ODHAD_NAVSTEV_ZA_KAMPAN` (G column, already live-computed from
  weeks×distinct-technicians×`CONTROL.TARGET_VISITS_DAY`-derived weekly capacity). If
  short, states the shortfall AND the weekly-capacity-per-technician that WOULD close
  it (directly answering "musíš zvýšit týdenní kapacitu" with a number, not just a
  yes/no). Also flags when the campaign's target includes `SMALL TERMINAL` but
  `TERMINAL_RULES` currently has it off - the concrete "tady by bylo dobré jet i malé
  terminály" signal, surfaced automatically instead of requiring the manager to notice.
  Purely informational - never writes to `TERMINAL_RULES` itself (confirmed: manual
  toggle stays manual, "Manuálně přepnuš TERMINAL_RULES sama/sám").

New `CONTROL.WEEKLY_CAPACITY_PER_TECH_TARGET` (default 50, "do budoucna plánuji s
kapacitou techniků na 50POS/WEEK") shown as a second reference value (I6/J6) alongside
the current `TARGET_VISITS_DAY`-derived capacity (J5) for comparison - informational
only, not yet wired into the verdict formula (that still uses today's real capacity,
not the future target, since the feasibility check should reflect what's actually
achievable now).

**Known limitation, stated plainly**: the feasibility check is deliberately a rough
capacity sanity check ("if technicians spent ALL their capacity only on this campaign"),
not a full scheduling simulation - it doesn't account for technicians' other concurrent
obligations (other simultaneous campaigns, CORN/GECO/9PODNIK hard cadence guarantees,
regular non-campaign neglect coverage). A "✅ Stihneš" here means the raw numbers work
out, not that Planning Engine's actual weekly selection will hit every target POS -
building a true multi-campaign capacity allocator was out of scope for this pass.

**Also surfaced, not yet acted on** (needs product-owner confirmation before any
capacity-affecting change): checked whether the existing infrastructure already
guarantees the "dlouhodobý cíl je podívat se alespoň 3x ročně i na ty nejslabší POS"
goal for classification P (3,067 Active POS) - it does not. `CADENCE_RULES` has no rule
scoped by `classification` at all (only `categoryPrefix`/`category`/`market`, matching
CORN/GECO/9PODNIK/"1"-prefix), and the general `NEGLECTED_AFTER_WEEKS=26` only guarantees
~2x/year visibility as an Advisor Engine WARNING (not a Planning Engine HARD guarantee).
Closing this gap - e.g. a new `CADENCE_RULES` row scoped to classification P with
`maxIntervalWeeks≈17` - would meaningfully change capacity allocation across 3,067 POS,
so it needs explicit confirmation before implementing, not a silent addition.

**Follow-up, same session**: the coverage/feasibility columns above were shipped before
actually translating the uploaded `Activity_plan_2026_01.xlsx` into `ACTIVITY_PLAN` rows
- caught by the product owner ("ten activity plán jsi tam vůbec nepromítl"). That file is
a 145-column weekly visibility-planning grid (`TÝDEN`/`ODE DNE` header rows map column
index to ISO week 1-53 for 2026), with two rows ("VISIBILITA LOSY"/"VISIBILITA LOTERIE")
naming each named campaign at the column where it starts - no explicit end column, so a
campaign's end is inferred as the week immediately before the next-named campaign starts
(last campaign of the year capped at week 53). Parsed into 7 LOS + 11 LOT rows, replacing
`ACTIVITY_PLAN`'s previous 2 rows (which turned out to be an earlier, less complete
version of the same real data - "Zlato a diamanty (Gems)" week 31-35 and "SPORTKA LÉTO"
week 30-33 are recognizably the same campaigns as the prior "Gems"/"Sportka" rows, just
refined). The concrete Christmas example from the product owner ("na Vánoce s losy objet
celou síť kromě LI") is the parsed **"Advent + Vánoce"** LOS row, weeks 48-53 (23.11. to
year end) - its coverage/feasibility columns now compute live against the real network.
`PRIORITY`/`OVERRIDE_GAP` defaulted to 5/`NO` for every new row (matching the prior 2
rows' values) since the source file carries no per-campaign priority signal - worth
revisiting per-campaign once real priorities are known.

## 24. Tourplan week 28→29→30 migration + critical date-matching bug fix (2026-07-11)

Product owner's ultimate near-term goal: "vložit tam můj tourplán do 28. weeku a na
week 29 jim naplánovat dojet úplně celou síť z toho co jim chybí a od weeku 30 jim
začít plánovat nové kampaňové návštěvy... od weeku 30 by měl fungovat tento systém na
100%". Executed in three steps:

1. **Week 28 baseline**: the uploaded `Tourplan_week_2028_1.xlsx` snapshot's technician
   assignments were compared against `POS_MASTER.assignedTechnician` - already 100%
   consistent (6,188/6,188 matched, 0 differences), so no write was needed there.
2. **Week 29 - transitional catch-up**: directly constructed (bypassing Planning
   Engine's normal score/capacity-capped selection entirely, per explicit instruction
   "je mi jedno, že by tam nebylo těch nutných 40 na technika") - every Active
   VELKY/SMALL TERMINAL POS with no row in `VISIT_HISTORY_ACTUAL` ("POS bez návštěvy",
   the same real-history-based definition used elsewhere), grouped by technician
   (`managerOverrideTechnician` ?? `assignedTechnician`), sorted PPT descending,
   round-robin across Mon-Fri. Produced 2,621 rows written directly into both
   `MANAGER_PLAN` and `MANAGER_PLAN_PUBLISHED` (published immediately - it's a
   migration catch-up, not a draft to review) plus a `PLAN_LIFECYCLE` entry.
3. **Week 30+ - normal algorithm**: cleared a stale `CONTROL.CAMPAIGN_START_WEEK=31`
   override (would have skipped week 30 entirely) so Planning Engine's resume-from-
   last-week logic picked up naturally at week 30. Ran `planning_engine.run()` once
   (`CAMPAIGN_LENGTH=4` → attempted weeks 30-33, week 33 ended up with zero eligible
   candidates - not a bug, just no POS left in that window), then `publish_engine.run()`
   three times (one week per run, by design) for weeks 30/31/32, then
   `start_tracking_engine.run()`.

**Critical bug found while verifying the above**: re-running `performance_engine.run()`
afterward showed 0 technician/week rows despite claiming "12471 deduped compliance
evaluations" - traced to `ComplianceEngine.ts`/`compliance_engine.py`'s
`MANAGER_PLAN_PUBLISHED` → `COMPLIANCE_LOG` matching step requiring the DATE column to
be a real `Date`/`datetime`. But `PlanningEngine.ts` (and the week-29 script above, to
match) write that column via `toLocaleDateString("cs-CZ")` - a Czech-formatted STRING
("1. 6. 2026"), not a real Date. The strict type check silently discarded every
published-plan row, so `planned_set` stayed empty for every plan this system has ever
published - **not new to this session, a latent bug from day one, only surfaced now
because this was the first time a real plan was published and then re-evaluated via
the openpyxl execution path** (Office Scripts running live in Excel may coincidentally
behave differently; not verified either way).

A naive fix (fall back to generic `new Date(string)` parsing) was proven unsafe:
`new Date("1. 6. 2026")` evaluates to **6 January**, not 1 June - Node misreads the
Czech D.M order as US M.D. Fixed instead with an explicit day/month/year-capturing
regex (`parsePlanDate()` / `_parse_plan_date()`) added to both engines, verified via a
dedicated seed (`"1. 6. 2026"` → correctly matched, `Splneno_vcas`,
`matchedActualDate: "2026-06-01"`, identical in both the TS and Python engine).

**Real workbook remediation**: the bug had already caused `compliance_engine.run()` to
be re-run once against stale (pre-fix) code, which - since `COMPLIANCE_LOG` is
append-only by design, never deduped against its own prior rows - duplicated the
entire 12,509-row historical backfill a second time (25,018 rows) while still
producing zero matches for weeks 29-33. Remediated by truncating `COMPLIANCE_LOG` back
to the original 12,509 rows (a fresh backup was taken first) and re-running
Compliance → Advisor → Performance → Reporting with the fix in place. Result: week 29
(never-visited POS) now correctly shows 2,621 `Pending` rows; weeks 30-32 show 2,496
`Splneno_vcas` rows - these are POS the normal campaign algorithm selected that had
*already* been visited earlier in the real 2026 history (Feb-Jul), which is intended
behavior (`determineComplianceStatus`: an actual visit at or before the planned week
counts as on-time), not a new bug. `TECHNICIAN_PERFORMANCE_LOG`/`SUMMARY` now populated
(99 technician/week rows, 27 technicians). `POS_MASTER`/`RAW_DATA` row counts confirmed
unchanged throughout (11,606/11,607).

**Follow-up implication**: since this bug affected every plan ever published by this
system, any historical compliance data evaluated before this fix should be treated as
unreliable for published-plan matching (the `Navic_evidovano`/historical-backfill rows
from section 22 are unaffected - those never depended on `MANAGER_PLAN_PUBLISHED`
matching in the first place).

**Second instance, same root cause**: `PerformanceEngine.ts`/`performance_engine.py` had
an identical `instanceof Date`/`_to_date()`-ISO-only bug in their own, separate
`MANAGER_PLAN_PUBLISHED` → `plannedVisits`/`region` aggregation loop - silently zeroed
`plannedVisits` and blanked `region` for every published plan (found while inspecting why
`TECHNICIAN_PERFORMANCE_SUMMARY.region` was blank for every technician despite a
freshly-published, now-correctly-matched plan). Fixed with the same `parsePlanDate()` /
`_parse_plan_date()` approach, added as a new function (not a shared one - PerformanceEngine
and ComplianceEngine have no shared module boundary), verified via the same seed extended
to run Compliance→Performance together, byte-identical TS/Python output.

**Středisko (RSA/RSC/RSE) added alongside the fix**: while fixing this, added a second
per-technician aggregate - `stredisko`, the most common `POS_MASTER.posArea` value that
week (distinct from the pre-existing `region`, which is `POS_MASTER.area`, a district
name like "Praha"). Product owner, 2026-07-11: "do filtrů dej podle střediska (typicky to
tam máš jako oblast) RSC, RSA apod." Appended as the last column on both
`TECHNICIAN_PERFORMANCE_LOG` and `TECHNICIAN_PERFORMANCE_SUMMARY` (existing
column-index-based readers unaffected), and surfaced as a new native-Table column
("Středisko") on `PERFORMANCE` - filtering is Excel's built-in AutoFilter on that column,
no custom filter UI needed. Also added a `CATEGORY_RULES.RULE` dropdown
(`CORE`/`NORMAL`/`EXCLUDE`) and guidance comment, matching `TERMINAL_RULES.ACTIVE`'s
existing YES/NO dropdown - product owner: "nemám tam možnost zapnout třeba to 1CD... chci
aby to fungovalo fakt dost smooth."

## 25. Product-owner course correction: Excel cockpit UX work paused for a web-app pivot (2026-07-11)

Mid-session, after the above fixes, the product owner stopped feature work to reframe the
whole project from a manager's-workflow perspective: the system had grown technically
sound but operationally confusing - "musím přemýšlet nad jednotlivými enginy, jejich
pořadím a stavem systému... To není chyba implementace. Je to chyba produktu." The real
workflow is 10 steps (upload SalesApp/PPT/campaigns, generate a tour plan for an explicit
start-week+length, review, publish, let the system track compliance, repeat), which
collapses to 4 manager actions: **Nahrát data / Generovat tour plán / Publikovat /
Aktualizovat sledování**. Hard rule, already true today but not visibly reassuring: **a
new tour plan is only ever created by an explicit manual click** - the system may only
surface "plan exists through week X, consider generating the next one," never act on it.

An initial proposal (simplify the *Excel* UI into this 4-action "cockpit," collapsing
Publish into one click and auto-refreshing tracking after import) was superseded
mid-conversation by a bigger instruction: "Business logiku nech beze změny. Přestaň ji
přizpůsobovat Excelu. Přesuň ji do backendu a navrhni jednoduchou webovou aplikaci pro
manažera" (leave the business logic unchanged, stop adapting it to Excel, move it to a
backend, design a simple web app for the manager). Confirmed via AskUserQuestion:
**local-only** (FastAPI or similar, runs on the product owner's own machine, opened at
`localhost` in a browser - no cloud, no accounts, no change to the standing "no external
APIs / no online sync" rule) and **the existing `.xlsx` stays the data warehouse** (the
web backend reads/writes it exactly as `desktop_client/xlsx_engine_io.py` already does -
Office Scripts (`.ts`) become unnecessary since there's no more Excel UI driving them, but
the 9 engines' logic itself - already ported to `desktop_client/engines/` and verified
equivalent to the TS originals - does not change).

A concept mockup (4-stage pipeline UI mapped 1:1 to the 10 real steps, with the
"never auto-generate" rule as a visible on-page constraint) was published as an Artifact
for review. **Status: proposal presented, not yet approved for implementation** - the
product owner had not yet responded to the phased build plan (status cockpit first, then
Generovat, then Publikovat, then sledování) when this session's work was interrupted for
a git-hook commit. Also per the product owner's stated priority in the same message:
Reporting/Performance/analytics expansion is paused - the next work, once the web-app
direction is confirmed, should focus on Planning Engine quality itself (visit history,
last-visit-per-POS, campaign weighting, GPS clustering, capacity, rule precedence), not
new features.

## 26. Planning Engine business-logic audit (2026-07-11)

Product owner's revised priority: web-app work paused, focus entirely on Planning Engine
quality against 9 named criteria (visit history since year start, last visit per POS,
weeks since last visit, current campaigns, PPT, business rules, GPS clustering, technician
capacities, mandatory visits/priority) - "potřebuji, aby systém nejdříve uměl spolehlivě
vytvořit kvalitní tour plán. To je hlavní produkt." A full audit (TS + Python, both
verified line-for-line equivalent, no divergence) found the core scoring/selection logic
solid, with three headline gaps - but the product owner then scoped follow-up work
strictly: **audit business logic only, fix real bugs only, no refactoring/optimization
unless required to fix the planner, no new features.** Under that scope:

- **`managerOverridePriority` is parsed but never consumed by Planning Engine** (only
  `managerOverrideType`/`managerOverrideTechnician` are read) - NOT fixed. Making it do
  something requires new scoring logic, which is a feature, not a bug.
- **Mandatory (CORE/CADENCE-hard) visits correctly can never be silently dropped**
  (`selectWeekPOS()` pushes every `pickMandatory()` result unconditionally, capacity is
  never checked against it) - but a resulting capacity overrun isn't surfaced to the
  manager (no Advisor Engine alert type for it) - NOT fixed. That's Advisor Engine, a
  separate paused module, and a new alert type is new capability.
- **"Aktuální kampaně" don't drive targeted POS selection** - campaign scope only affects
  hold-back timing and an output label, not which POS get chosen for a given LOS/LOT
  activity (the engine's own comment at `PlanningEngine.ts` calls this "deferred from
  V10.5.5, needs a separate gap"). NOT fixed - implementing real campaign-scoped
  targeting is new logic.
- **`CAPACITY_OVERRIDE`'s `tech|year|week` lookup key uses the same "raw week, flat
  CONTROL.YEAR" convention as `MANAGER_PLAN`/`PLAN_LIFECYCLE`/`PublishEngine.ts`**
  (documented at `ComplianceEngine.ts:291-299`) - investigated as a possible
  year-boundary bug, concluded NOT a bug: it's consistent with the rest of the system.
  Left untouched.

**One real bug WAS fixed**: `ComplianceEngine.ts`'s SALESAPP_IMPORT DATE-column parse
(`office-scripts/ComplianceEngine.ts`, the "PARSE SALESAPP_IMPORT" loop) had the exact
same unsafe `new Date(String(v))` fallback already proven dangerous twice this session
(section 24) - dormant in practice (a real-workbook check confirmed SALESAPP_IMPORT's
Date column always arrives as genuine `datetime` cells), but directly feeds
`weeksSinceLastVisit`/`lastRealVisitDate`, i.e. the "historie návštěv" criterion the
planner scores on, so it was fixed defensively. New `parseSalesAppDate()`
(TS)/`_parse_salesapp_date()` (Python) accept a real Date, an explicit "D. M. YYYY"
string, or an unambiguous ISO ("YYYY-MM-DD...") string only - anything else is dropped,
never guessed at. Deliberately a NEW, narrowly-scoped function reusing neither
`parsePlanDate()` (MANAGER_PLAN_PUBLISHED-specific, no ISO fallback) nor `_to_date()`
(used at 2 other Python call sites left untouched, to avoid unintended behavior change
elsewhere). Verified via a dedicated seed (`salesapp_date_seed.json`): a real Date, a
Czech string ("1. 6. 2026" → correctly 1 June, not misparsed as 6 January), and an ISO
string all parsed identically in both engines; a garbage string was correctly dropped.
Re-run against a copy of the real workbook confirmed zero behavior change on real data
("0 new realized visits imported", identical to the pre-fix run).
