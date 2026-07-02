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

**RULE: New POS without history**
CONDITION: `lastRealVisitDate IS NULL`
ACTION: POS is an eligible candidate immediately; priority determined by other rules (Cadence,
Pareto, PPT), not automatically boosted or suppressed
STATUS: CONFIRMED

**RULE: Closed POS**
CONDITION: `status = Closed` (set only by POS_STATUS_IMPORT, never inferred from RAW_DATA absence)
ACTION: never a candidate; history preserved forever
STATUS: CONFIRMED

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
STATUS: CONFIRMED

**RULE: GECO**
CONDITION: ★ OPEN — proposed `category = 1GECO` (387 POS), pending confirmation whether scope is
broader (e.g. entire `market = KA PARTNERS`, 2088 POS)
ACTION: maxIntervalWeeks = 5. guaranteeType ★ OPEN — proposed HARD (volume is small relative to
weekly capacity, so a hard reservation cannot meaningfully starve other visits — see worked
example in ARCHITECTURE.md §6)
STATUS: ★ OPEN — needs scope + guarantee-type confirmation before Planning Engine can score it

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

**RULE: Campaign/product attribution per visit — BLOCKED on input data**
The SalesApp export was checked column-by-column (37 columns) for a structured field naming which
LOS/LOT campaign a visit serviced. None exists — campaign names appear only in inconsistent free-
text notes (`OZ - Ostatní (do textu)`, `Technik/OZ - Poznámka`), which cannot be parsed reliably
without guessing. Proposed (not implemented) robust alternative: derive the serviced campaign from
`ACTIVITY_PLAN`'s week-based schedule, crossed with the `Nabeh kampane` (Ano/Ne) signal — i.e. "a
Nabeh-kampane=Ano visit in week W serviced whatever LOS/LOT was active per ACTIVITY_PLAN in week
W." This is a business interpretation of ambiguous data, not a technical detail — needs explicit
product-owner sign-off before implementing.
STATUS: ★ OPEN — blocks per-POS LOS/LOT compliance breakdown specifically; does NOT block basic
plan-vs-actual compliance (Splneno/pozde/Nesplneno/Navic), which only needs POS+week matching.

**RULE: Compliance aggregation**
ACTION: rolled up by week, month, per technician, and network-wide; feeds Advisor Engine trend
detection over a configurable `TREND_WINDOW_WEEKS` (default proposed: 4)
STATUS: CONFIRMED (mechanism), window default ★ OPEN

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
