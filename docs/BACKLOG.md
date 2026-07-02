# Field Force Optimizer V11 — BACKLOG.md

Non-blocking items found during implementation. Not stopping work for these; tracked here so
they aren't lost.

## UX layer follow-ups (found while building tools/ux_style.py)
- **MANAGER_PLAN has no archival/trim strategy and grows unbounded.**
  PlanningEngine.ts keeps every Published/Active/Closed week forever (by
  design - locked weeks must never be silently dropped), but nothing ever
  removes old Closed weeks either. At ~1200 rows/week this reaches tens of
  thousands of rows within a year. Directly affects TECHNICIAN_PLAN's live
  formula view (tools/ux_style.py build_technician_plan), which uses a
  static 3000-row cap - fine today, will eventually need either a much
  higher cap or (better) an actual archival strategy for MANAGER_PLAN
  itself, consistent with the same open question already flagged for
  VISIT_HISTORY/SCORE_LOG in docs/ARCHITECTURE.md section 11.
- TECHNICIAN_PLAN shows the full Draft+Published picture (everything
  currently in MANAGER_PLAN). Once the archival strategy above exists, it
  should probably also gain a "only show this week + N upcoming" filter so
  it doesn't slowly fill with irrelevant historical rows - not needed yet
  since MANAGER_PLAN itself doesn't grow that large in normal short-term use.

## Advisor Engine follow-ups (not blocking, tracked for later)
- Campaign-completion risk alerts: waiting on an active HARD cadence rule with a recurring
  deadline (GECO/CORN currently inactive by config).
- Combine-visit (LOS+LOT) opportunity alerts: blocked on the same open campaign-attribution
  question as Compliance Engine's per-visit breakdown (docs/BUSINESS_RULES.md).
- Override-consequence notes (Advisor flags when a manual override conflicts with automatic
  Filters/Cadence logic): mechanical, not blocked on anything, just not built yet - good next
  small increment.
- ADVISOR_RULES config table exists but AdvisorEngine.ts v1 reads its three alert types' actual
  thresholds from new CONTROL rows instead of from ADVISOR_RULES rows. Generalizing to a fully
  config-driven rule table (arbitrary new alert types without code changes) is a reasonable
  future refactor once there's a second or third concrete alert type to generalize from - didn't
  build the generalized version speculatively ahead of a second real use case.
- All four new CONTROL threshold values added this round (ADVISOR_NEGLECT_WARNING_RATIO_PERCENT,
  ADVISOR_TREND_WINDOW_WEEKS, ADVISOR_OVERLOAD_WARNING_RATE_PERCENT,
  ADVISOR_OVERLOAD_CRITICAL_RATE_PERCENT) are proposed defaults, not confirmed business rules -
  should be tuned once real weekly data accumulates.

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
- Plan lifecycle — DONE (PublishEngine.ts, PLAN_LIFECYCLE, MANAGER_PLAN_PUBLISHED). Follow-up not
  yet built: "post-publish amendment" (docs/BUSINESS_RULES.md section 11 - manual changes to an
  already-Published week should be recorded as a visible, timestamped delta, not a silent
  rewrite). Today, a manager can still edit POS_MASTER overrides, but there is no mechanism yet
  to amend a specific already-published visit and have that show up distinctly from the original
  snapshot.

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
