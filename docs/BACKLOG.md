# Field Force Optimizer V11 — BACKLOG.md

Non-blocking items found during implementation. Not stopping work for these; tracked here so
they aren't lost.

## Deferred engines (per agreed bottom-up build order)
- SalesApp import + basic real VISIT_HISTORY_ACTUAL/Compliance Engine — DONE (ComplianceEngine.ts).
  Per-POS LOS/LOT campaign attribution specifically remains blocked - see BUSINESS_RULES.md
  "Campaign/product attribution per visit" - waiting on product-owner confirmation of the
  ACTIVITY_PLAN-join design before implementing.
- Technician-level compliance KPI reporting uses MANAGER_PLAN's own technician assignment
  (sidesteps SalesApp "Executor" name-format mismatch entirely). Extra-visit rows keep the raw
  SalesApp Executor string unresolved - fine for audit, not usable for a technician-identity KPI
  if that's ever needed.
- Advisor Engine (all alert types) — table structure exists (ADVISOR_RULES), no logic yet
- Route/Geo Engine refinement: current PlanningEngine.ts does anchor+nearest day clustering
  (V10.5.5-equivalent), not yet the buffer-pool-then-cluster "compose the whole week as a
  geographic loop" design discussed for V11 (BUSINESS_RULES.md §7) — first version intentionally
  kept close to legacy geoDays() to reduce risk while getting the pipeline working end to end
- TECHNICIAN_PLAN (simplified per-technician output view)
- SEASONAL_STRATEGY / SCORE_PROFILES switching (only DEFAULT profile is read today)
- Plan lifecycle (Draft/Published/Active/Closed state machine) — PlanningEngine.ts currently
  overwrites MANAGER_PLAN on every run, same as V10.5.5's OUTPUT_PLAN, no lock/publish concept
  yet

## Simplifications in PlanningEngine.ts v1 (tracked, not hidden)
- Campaign-change min-gap override ("NEW CAMPAIGN OVERRIDE" in V10.5.5 — revisit a CORE POS
  sooner than minGapWeeks if the campaign material changed) is not implemented yet: it needs a
  real currentLosActivity/currentLotActivity comparison, which depends on Compliance Engine
  writing those fields to POS_MASTER. Gap logic in v1 only uses the plain minGap threshold.
- PARETO_GROUPS scope is only implemented for PER_TECHNICIAN. GLOBAL/PER_REGION/PER_MARKET are
  readable from config but not yet handled by PlanningEngine.ts.
- KA / IDT-above-threshold Pareto tiers are seeded inactive (no boundaryType/value yet) and are
  not part of scoring in v1 — BUSINESS_RULES.md open item, not a code gap.
- GECO / CORN cadence rules are seeded inactive — PlanningEngine.ts already reads CADENCE_RULES
  generically, so activating them later (once scope/guaranteeType are confirmed) should not
  require a code change, only flipping `active` to YES and filling in the values. Worth
  re-verifying this claim once those values are confirmed, not assumed correct forever.
- Structured SCORE_LOG (per-component score breakdown, explainability) is not written — v1 keeps
  V10.5.5's text REASON tag approach for speed. Real gap against the explainability goal in
  BUSINESS_RULES.md §0 — should be prioritized once the base pipeline is validated on real data.
- FORCE_INCLUDE bypassing Filters entirely is implemented per the proposed default in
  BUSINESS_RULES.md §10, which was never formally reconfirmed — flagged, not blocking.
- Manager override priority delta (Low/Normal/High/Critical) is not implemented at all yet —
  `managerOverridePriority` is stored in POS_MASTER but not read by PlanningEngine.ts.

## Ideas noted, not designed
- GPS bonus radius (300m) and max (5) are global CONTROL settings; could eventually be a
  per-CADENCE_RULES-row parameter if different POS groups need different bonus radii. Not
  needed until there's a concrete case for it.
