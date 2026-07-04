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
