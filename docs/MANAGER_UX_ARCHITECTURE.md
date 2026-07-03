# Field Force Optimizer V11 — Manager UX layer (architecture proposal, NOT YET IMPLEMENTED)

Status: **proposal, awaiting product-owner approval before any implementation begins**, per
explicit instruction ("Teprve po schválení architektury začneme s implementací"). This document
exists so the design is reviewable and revisable before code is written, and so it's a durable
record of the decision once approved - consistent with how every other architectural decision in
this project is documented (see `ARCHITECTURE.md`, `BUSINESS_RULES.md`).

## 0. What this is and isn't

This is a **second, presentation-only layer on top of the existing planner**, not a second
application and not a second source of truth. Every hard constraint already established for the
whole project applies unchanged here:

- FieldForceOptimizer (Excel + Office Scripts) remains the sole source of truth for all business
  logic. This layer computes nothing new that isn't already an aggregation of existing engine
  output (`COMPLIANCE_LOG`, `VISIT_HISTORY_ACTUAL`, `MANAGER_PLAN_PUBLISHED`, `POS_MASTER`).
- No external API, no online sync, no write-back outside the existing weekly workflow.
- Priority order is explicit: **the planner is priority #1**. Nothing here may make the planner
  sheets slower, harder to find, or riskier to run. The new sheets are additive.

Given that, "new business logic" is off the table by construction - the only genuine engineering
work here is (1) a new **aggregation** step (turning existing per-visit logs into per-technician/
per-week summaries) and (2) **presentation** (how that aggregated data is laid out, filtered, and
styled so it reads like a manager dashboard instead of a spreadsheet).

## 1. The one new piece of real plumbing: `TECHNICIAN_PERFORMANCE_LOG`

Every UX screen below reads from ONE new aggregated table, never directly from the raw,
unbounded-growth logs (`COMPLIANCE_LOG`, `VISIT_HISTORY_ACTUAL`). This is the single most
important design decision in this proposal - see §4 (performance) for why.

**Shape**: one row per (technician, year, week) - not per visit. Built by a new step in
`ReportingEngine.ts` (same file that already builds `DASHBOARD`, since this is the same class of
work: aggregate-only, no new business rules, matches the existing "Reporting Engine computes
nothing new, it only aggregates" principle from `ARCHITECTURE.md` §5) - or a new
`PerformanceEngine.ts` if `ReportingEngine.ts` gets too large; decide once the first version is
written and its line count is known, not preemptively.

Columns (all derived from existing data, nothing new *computed*):

| Column | Source |
|---|---|
| technician, year, week | grouping key |
| region | most common `POS_MASTER.area` among that week's visits (informational) |
| plannedVisits | count of that technician's rows in `MANAGER_PLAN_PUBLISHED` for that week |
| realizedVisits | count of `Splneno_vcas` + `Splneno_pozde` in `COMPLIANCE_LOG` |
| splnenoVcas / splnenoPozde / nesplneno / navicEvidovano | counts by status, from `COMPLIANCE_LOG` |
| compliancePercent | realizedVisits / plannedVisits |
| visitsByDay (5 columns, MON-FRI) | count from `VISIT_HISTORY_ACTUAL`, grouped by weekday |
| merchCount / visibilityCount / otherCount | **blocked** - see §1a |

### 1a. Open question: Merch / Visibility split

Still needs your answer (asked in the previous message, repeating it here since it blocks part of
the data model): which SalesApp "Účel návštěvy" columns count as Merch vs. Visibility vs. other?
Everything else in this proposal works without this - it's an isolated three extra columns, not a
blocker for starting.

### 1b. Why this table, not live formulas over the raw logs

`COMPLIANCE_LOG` and `VISIT_HISTORY_ACTUAL` are explicitly append-only and grow forever (already
flagged as an open archival question in `BACKLOG.md`/`ARCHITECTURE.md` §11). A `TECHNICIAN_PLAN`-
style *live formula* view directly over those logs would get slower every week, forever, and both
already-flagged sheets have no archival strategy yet. `TECHNICIAN_PERFORMANCE_LOG` is a real,
computed-once-per-run table (like `COMPLIANCE_LOG` itself, not like `TECHNICIAN_PLAN`) - one row
per technician per week, so even after 3 years of weekly use with 30 technicians that's ~4,700
rows, not hundreds of thousands. All manager screens are fast because they read a small table, not
because any screen is individually optimized.

## 2. Sheet structure

```
HOME                    - redesigned, KPI cards + nav rail (existing sheet, restyled)
TECHNICIAN_SCORECARD    - new
PERFORMANCE             - new
WEEK_DETAIL             - new
DASHBOARD               - existing, extended with 1-2 more native charts
MAP                     - new, OPTIONAL, decided after seeing real GPS spread (see §6)
```

All existing planner/engine sheets (`POS_MASTER`, `MANAGER_PLAN`, `RAW_DATA`, `CONTROL`,
`CADENCE_RULES`, etc.) are **unchanged and stay hidden** exactly as today
(`hide_technical_sheets()`) - the manager never needs to see them to use this layer.

## 3. Navigation: the honest version of "left menu" Excel can actually do

Excel has no real floating sidebar. Two options exist, and I'd use both together:

- **Sheet tabs as the top-level switcher**: since all engine sheets are already hidden, the visible
  tab strip *is* effectively a menu - only HOME/SCORECARD/PERFORMANCE/WEEK_DETAIL/DASHBOARD/(MAP)
  show up. Cheap, native, no extra work.
- **A persistent nav rail inside each sheet** (frozen column A, or A:B): a short vertical stack of
  labeled buttons (reusing the existing `_nav_button()` hyperlink pattern already used throughout
  `HOME`/`IMPORT_HUB`) that jump between the 5-6 UX sheets, visible on every screen via frozen
  panes so it doesn't scroll away. This is what actually produces the "feels like an app with a
  side menu" impression, not the tab strip alone - worth the small amount of repeated setup.

**One real Excel limitation to flag now, not discover later**: a hyperlink can navigate to a sheet,
but it cannot also *set a cell value* on arrival (no VBA, no Office Script trigger on click). So
"click a technician's row in `PERFORMANCE` → jump straight to their pre-selected `SCORECARD`" is
**not natively possible** without a script bound to every single row (impractical, doesn't scale,
and script-bound buttons can't be provisioned from outside Excel anyway - same limitation already
hit with the Office Scripts "Add button" feature). The honest version: clicking a technician's name
in `PERFORMANCE` jumps to `SCORECARD`, and you then pick them from `SCORECARD`'s own dropdown
(one extra click). I'd rather tell you this now than silently ship something that looks like it
should auto-select and doesn't.

## 4. Component breakdown

### HOME
KPI card row (Compliance %, Planned, Realized, Nesplněno, Navíc, Risk POS count, trend arrow vs.
last week) - same card component as today's summary cards, restyled per the palette work already
done (STATUS_GOOD/WARNING/SERIOUS/CRITICAL). Values are single-cell formulas over
`TECHNICIAN_PERFORMANCE_LOG` (SUMIFS/AVERAGEIFS on the latest week), not new calculations.

### TECHNICIAN_SCORECARD
One Data Validation dropdown ("select technician") drives every other cell on the sheet via
INDEX/MATCH or SUMIFS keyed on that selection - region, POS count, visit count, compliance,
Splněno/Nesplněno/Navíc breakdown, long-run average, day-of-week workload bar. Trend uses Excel's
native **sparklines** (a real, built-in, lightweight Excel feature - perfect fit here, no chart
object overhead for a small inline trend). "TOP problematic POS" is a small `LARGE()`/`RANK()`-
driven table over that technician's `COMPLIANCE_LOG` rows (still bounded - one technician's rows
for a bounded lookback window, e.g. last 12 weeks).

### PERFORMANCE
A real Excel **Table** (`ListObject`) over `TECHNICIAN_PERFORMANCE_LOG`'s current-week slice, with
native AutoFilter - this is the built-in Excel feature that already gives you sortable/filterable
columns for free, no custom UI needed. Columns: Technik, Compliance %, Splněno, Pozdě, Navíc, Počet
návštěv, Trend (sparkline column). Technician name cells hyperlink to `SCORECARD` (see the
navigation limitation in §3).

### WEEK_DETAIL
Same pattern as `SCORECARD` but keyed by a week selector instead of a technician: all technicians'
numbers for that week, KPI row, comparison vs. previous week (simple delta formulas), best/worst
performer via `INDEX(MATCH(MAX(...)))` / `INDEX(MATCH(MIN(...)))` over that week's slice.

### DASHBOARD
Extends the existing native-chart pattern from `ARCHITECTURE.md` §20 (fixed-size chart data
blocks, not flowing ranges) with 1-2 more charts: compliance trend over time, technician
performance trend. Same "CHART DATA BLOCKS" convention, so this is additive, not a redesign of
what's already there.

## 5. Filtering approach

- **Per-sheet single-selection controls** (technician dropdown on `SCORECARD`, week dropdown on
  `WEEK_DETAIL`) - simple, robust, and every formula on the sheet has one clear input to reason
  about.
- **Native Table + AutoFilter** for the `PERFORMANCE` comparison grid - Excel's own filter UI,
  zero custom code, and it's exactly the "sortable/filterable table" you asked for.
- **Slicers**: a good fit *if* built on a PivotTable, but PivotTables have a real fragility risk
  here - their source range doesn't automatically follow a full sheet rewrite the way a plain
  formula range does, and every relevant sheet in this project gets fully cleared and rewritten on
  every engine run. I'd rather prove the Table+AutoFilter approach works first (robust, already
  used elsewhere - `TECHNICIAN_PLAN` already has `auto_filter`) and only add a PivotTable+Slicer
  view later, as an opt-in "explore" addition, once we've verified it survives repeated
  `ReportingEngine.ts` runs without breaking - not a launch-blocking risk, but worth sequencing
  after the simpler version is proven.

## 6. Map — recommend the same substitute already used for regional maps

`ARCHITECTURE.md` §20 already worked through this exact question for a *regional* map and
concluded: Excel's real mapping features (3D Maps / Filled Map) have no Office Scripts API, so
they can't be built or kept in sync by a script - only by a human re-clicking through Excel's UI
every time, which fails this project's "no manual step" standard for everything else. The same
reasoning applies here.

**What I'd build instead, if anything**: a plain XY scatter chart - `POS_MASTER`'s GPS X/Y for a
selected technician/week (from `WEEK_DETAIL`'s selection), one point per visited POS, colored by
weekday. This is a real native Excel chart, fully script-buildable and re-render-safe (same
fixed-data-block pattern as the other charts) - not a real map with a basemap underneath, just a
relative scatter of where the visits were. It answers "did they stay in one area or bounce around"
at a glance, which is most of what a route/heatmap would tell you anyway, without pretending to be
GIS. I would explicitly **not** attempt a route line or heatmap - without a real basemap both would
just be an abstract shape with no more information than the scatter plot, for meaningfully more
engineering effort.

**Recommendation**: build this only after §§1-5 are done and only if a look at real GPS data shows
it's actually informative for how your technicians work (dense city routes vs. spread rural
regions look very different on a scatter plot - worth a quick look at real data before committing
engineering time, not before).

## 7. Performance at scale

Addressed by construction, not by sheet-by-sheet optimization:

- Every UX sheet reads from `TECHNICIAN_PERFORMANCE_LOG` (bounded: technicians × weeks) or from a
  small, explicitly-bounded lookback slice of the raw logs (e.g. "this technician's last 12 weeks
  of `COMPLIANCE_LOG` rows" for the SCORECARD's "top problem POS" list) - never an unbounded scan
  of `COMPLIANCE_LOG`/`VISIT_HISTORY_ACTUAL` in full.
- Charts bind to fixed-size data blocks (§20's established pattern), never a flowing range.
- No volatile functions (`OFFSET`, `INDIRECT`, unscoped `TODAY()` in array formulas) in
  high-row-count contexts - `INDEX`/`MATCH`/`SUMIFS`/structured Table references throughout, same
  discipline already used in `TECHNICIAN_PLAN`.

## 8. Visual style

Reuses the palette/component work already done this session rather than inventing a second style:

- `NAVY` primary, `STATUS_GOOD/WARNING/SERIOUS/CRITICAL` for all status coloring (already
  accessibility-checked).
- One shared **KPI card** component, formalized into a single reusable function (today's
  `_summary_card` is ad hoc per-sheet; worth promoting to one canonical implementation used by
  every screen here, so all cards look and behave identically) - border, tint, icon, big number,
  muted label, optional trend arrow.
- Emoji-based icons throughout (already the project's convention - renders identically across
  Excel Online/Desktop/Mac, no external image assets, no broken-image risk).
- The nav rail (§3) as one shared component, reused verbatim on every UX sheet.

## 9. Proposed build order

Sequenced so each step is independently reviewable/revisable before the next starts, and so a
mistake in an early step doesn't get baked into five sheets before anyone sees it:

1. `TECHNICIAN_PERFORMANCE_LOG` (data only, no UI yet) - verify the numbers are right against
   `COMPLIANCE_LOG` by hand on real data before building anything visual on top of it.
2. Redesign `HOME` with the formalized KPI-card component + nav rail - proves out the visual
   language on a sheet that already exists, before replicating it to new sheets.
3. `TECHNICIAN_SCORECARD`.
4. `PERFORMANCE`.
5. `WEEK_DETAIL`.
6. `DASHBOARD` extension (new charts).
7. `MAP` - only if steps 1-6 land well and real GPS data looks worth it (§6).

## 10. What I need from you before starting

1. Sign-off on this architecture, or specific changes to it.
2. The Merch/Visibility SalesApp column mapping (§1a) - not blocking step 1, but needed before
   those three columns can be filled in.
3. Confirmation of the build order in §9, or a different priority if some screen matters more to
   you than the order I've proposed.
