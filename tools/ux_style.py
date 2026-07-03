"""
Applies UX/visual polish to the V11 workbook: sheet organization, color
coding (editable vs system-managed), data validation dropdowns, a legend,
a START_HERE guide, and a redesigned ACTIVITY_PLAN with a live timeline +
impact estimate.

Pure presentation layer - does not change any business logic, engine
behavior, or the data model any engine reads/writes by position. Called by
scaffold_workbook.py after all sheets/data are in place.

IMPORTANT SAFETY CONSTRAINT: real Excel "Protect Sheet" blocks Office
Scripts' Range.clear()/setValues() calls unless the script explicitly
unprotects first - none of our engines do that. Enabling real cell
locking + sheet protection is therefore ONLY safe on sheets no engine ever
writes to (pure config). Sheets an engine writes to (POS_MASTER,
MANAGER_PLAN, MANAGER_PLAN_PUBLISHED, PLAN_LIFECYCLE, COMPLIANCE_LOG,
ADVISOR_LOG, VISIT_HISTORY_ACTUAL, DASHBOARD) and pure import-staging sheets
get color-only "please don't hand-edit this" cues, never real protection -
this trade-off is documented in the legend so it isn't a silent gap.
"""
from openpyxl.styles import Font, PatternFill, Alignment, Border, Side
from openpyxl.formatting.rule import FormulaRule, DataBarRule
from openpyxl.worksheet.datavalidation import DataValidation
from openpyxl.utils import get_column_letter, column_index_from_string
from openpyxl.comments import Comment
from openpyxl.chart import LineChart, BarChart, Reference

# ============================================================================
# PALETTE
# ============================================================================

NAVY = "1F4E78"
WHITE = "FFFFFF"
EDITABLE_FILL = "FFF2CC"       # warm cream - "you type here"
SYSTEM_FILL = "E7E6E6"         # neutral grey - "the system manages this"
IMPORT_FILL = "DDEBF7"         # light blue - "paste your export here"
OUTPUT_FILL = "E2EFDA"         # light green - "generated results"
LOG_FILL = "F2F2F2"            # very light grey - "append-only history"
WARNING_FILL = "FCE4D6"        # soft orange - inactive/TODO config rows
LOS_FILL = "BDD7EE"            # timeline: LOS campaigns
LOT_FILL = "F8CBAD"            # timeline: LOT campaigns

HEADER_FONT = Font(color=WHITE, bold=True, size=11)
HEADER_FILL = PatternFill("solid", fgColor=NAVY)
TITLE_FONT = Font(bold=True, size=14, color=NAVY)
SECTION_FONT = Font(bold=True, size=11, color=NAVY)
NOTE_FONT = Font(italic=True, size=9, color="808080")
THIN_BORDER = Border(*(Side(style="thin", color="BFBFBF"),) * 4)

# Sheet grouping -> tab color + intended sheet order (top to bottom in Excel)
SHEET_GROUPS = [
    ("HOME", "404040"),
    ("DASHBOARD", "375623"),
    ("TECHNICIAN_PLAN", "375623"),
    ("POS_MASTER", "7030A0"),
    ("ACTIVITY_PLAN", "BF8F00"),
    ("IMPORT_HUB", "2E75B6"),
    ("RAW_DATA", "2E75B6"),
    ("POS_STATUS_IMPORT", "2E75B6"),
    ("SALESAPP_IMPORT", "2E75B6"),
    ("MANAGER_PLAN", "375623"),
    ("MANAGER_PLAN_PUBLISHED", "375623"),
    ("PLAN_LIFECYCLE", "375623"),
    ("CONTROL", "BF8F00"),
    ("MARKET_RULES", "BF8F00"),
    ("TERMINAL_RULES", "BF8F00"),
    ("CATEGORY_RULES", "BF8F00"),
    ("CADENCE_RULES", "BF8F00"),
    ("PARETO_GROUPS", "BF8F00"),
    ("SCORE_PROFILES", "BF8F00"),
    ("ADVISOR_RULES", "BF8F00"),
    ("CAPACITY_OVERRIDE", "BF8F00"),
    ("COMPLIANCE_LOG", "595959"),
    ("ADVISOR_LOG", "595959"),
    ("VISIT_HISTORY_ACTUAL", "595959"),
    ("VISIT_HISTORY", "595959"),
]

# The 5 sheets a normal user (regional manager) works with day to day.
# Everything else that stays visible (import staging) is a necessary but
# occasional "mailbox", not part of the daily working set - communicated on
# HOME, not hidden, because the user must paste into it weekly.
CORE_DAILY_SHEETS = ["HOME", "DASHBOARD", "TECHNICIAN_PLAN", "POS_MASTER", "ACTIVITY_PLAN", "IMPORT_HUB"]
IMPORT_UTILITY_SHEETS = ["RAW_DATA", "POS_STATUS_IMPORT", "SALESAPP_IMPORT"]

# Everything not in CORE_DAILY_SHEETS/IMPORT_UTILITY_SHEETS is implementation
# detail (raw engine data, config, logs) - hidden from the normal user, but
# still fully readable/writable by Office Scripts (hidden sheets are not
# restricted via the API, only invisible in the tab bar).
HIDDEN_SHEETS = {
    "MANAGER_PLAN", "MANAGER_PLAN_PUBLISHED", "PLAN_LIFECYCLE",
    "CONTROL", "MARKET_RULES", "TERMINAL_RULES", "CATEGORY_RULES",
    "CADENCE_RULES", "PARETO_GROUPS", "SCORE_PROFILES", "ADVISOR_RULES",
    "CAPACITY_OVERRIDE", "COMPLIANCE_LOG", "ADVISOR_LOG",
    "VISIT_HISTORY_ACTUAL", "VISIT_HISTORY", "PLANNING_HORIZON_RULES",
}

# Sheets an engine writes to programmatically - never real-protected, see
# module docstring. Everything else (pure config, user-pasted imports) is
# safe to lock/protect.
ENGINE_WRITABLE = {
    "POS_MASTER", "MANAGER_PLAN", "MANAGER_PLAN_PUBLISHED", "PLAN_LIFECYCLE",
    "COMPLIANCE_LOG", "ADVISOR_LOG", "VISIT_HISTORY_ACTUAL", "DASHBOARD",
}

# Per-sheet: which columns (by header name) are meant for manual editing.
# Everything else on that sheet is shown as system/read-only styling.
EDITABLE_COLUMNS = {
    "CONTROL": ["VALUE"],
    "ACTIVITY_PLAN": ["TYPE", "ACTIVITY", "START_WEEK", "END_WEEK", "PRIORITY", "OVERRIDE_GAP"],
    "TERMINAL_RULES": ["ACTIVE"],
    "MARKET_RULES": ["ACTIVE"],
    "CATEGORY_RULES": ["CATEGORY", "RULE"],
    "CADENCE_RULES": ["scope", "matchValue", "minGapWeeks", "maxIntervalWeeks", "intervalType",
                       "guaranteeType", "dedupBy", "campaignChangeOverride", "priority", "active",
                       "validFrom", "validTo", "notes"],
    "PLANNING_HORIZON_RULES": ["appliesFromWeek", "appliesToWeek", "horizonWeeks", "reason", "active", "notes"],
    "PARETO_GROUPS": ["scope", "boundaryType", "boundaryValue", "active", "notes"],
    "SCORE_PROFILES": ["weight", "notes"],
    "ADVISOR_RULES": ["ruleId", "type", "condition", "threshold", "severity", "messageTemplate", "active"],
    "CAPACITY_OVERRIDE": ["technician", "year", "week", "capacity"],
    "POS_STATUS_IMPORT": ["POS", "ACTIVE"],
    "POS_MASTER": ["managerOverrideType", "managerOverridePriority", "managerOverrideTechnician", "plannerNotes"],
}

# Dropdown validations: sheet -> {header name: (list_of_values, allow_blank)}
DROPDOWNS = {
    "TERMINAL_RULES": {"ACTIVE": (["YES", "NO"], False)},
    "MARKET_RULES": {"ACTIVE": (["YES", "NO"], False)},
    "ACTIVITY_PLAN": {"TYPE": (["LOS", "LOT"], False)},
    "CADENCE_RULES": {
        "active": (["YES", "NO"], False),
        "guaranteeType": (["HARD", "SOFT_HIGH_WEIGHT"], True),
        "intervalType": (["RECURRING", "ONCE_PER_CAMPAIGN"], True),
        "dedupBy": (["NONE", "ADDRESS"], True),
        "campaignChangeOverride": (["YES", "NO"], True),
    },
    "PARETO_GROUPS": {
        "active": (["YES", "NO"], False),
        "boundaryType": (["PERCENTILE", "FIXED_VALUE"], True),
        "scope": (["PER_TECHNICIAN", "GLOBAL", "PER_REGION", "PER_MARKET"], True),
    },
    "POS_STATUS_IMPORT": {"ACTIVE": ([1, 0], False)},
    "POS_MASTER": {
        "managerOverrideType": (["", "FORCE_INCLUDE", "FORCE_EXCLUDE"], True),
        "managerOverridePriority": (["", "Low", "Normal", "High", "Critical"], True),
        "status": (["Active", "Closed"], False),
    },
}


def _header_index(ws):
    return {str(c.value): i + 1 for i, c in enumerate(ws[1]) if c.value not in (None, "")}


def style_header_row(ws, freeze_below=True, freeze_col=None):
    for cell in ws[1]:
        if cell.value in (None, ""):
            continue
        cell.font = HEADER_FONT
        cell.fill = HEADER_FILL
        cell.alignment = Alignment(horizontal="center", vertical="center", wrap_text=True)
    ws.row_dimensions[1].height = 28
    if freeze_below:
        col = freeze_col or "A"
        ws.freeze_panes = f"{col}2"


# Sheets that are wholesale paste-zones (whole sheet = editable import data,
# no per-column editable/system split makes sense) get a flat wash instead.
IMPORT_STAGING_SHEETS = {"RAW_DATA", "POS_STATUS_IMPORT", "SALESAPP_IMPORT"}
OUTPUT_SHEETS = {"MANAGER_PLAN", "MANAGER_PLAN_PUBLISHED", "DASHBOARD", "PLAN_LIFECYCLE"}
LOG_SHEETS = {"COMPLIANCE_LOG", "ADVISOR_LOG", "VISIT_HISTORY_ACTUAL", "VISIT_HISTORY"}

# Import Hub guidance: a cell comment on the header cell of each staging
# sheet, not a banner row - a banner row would shift every data row down by
# one, breaking every engine's row-1-is-header assumption. A comment is pure
# metadata (openpyxl's getValues()-equivalent used by Office Scripts never
# sees it), so it is risk-free. Content documents a capability that already
# works today (ComplianceEngine.ts dedupes SALESAPP_IMPORT rows by UID
# against VISIT_HISTORY_ACTUAL on every run, and ImportEngine.ts upserts
# POS_MASTER by posId), it just wasn't visible/discoverable before.
IMPORT_HUB_GUIDANCE = {
    # RAW_DATA is NOT here deliberately - unlike the other staging sheets it
    # has a legacy instruction row already at row 1 (see
    # fix_raw_data_layout()), rewritten in place as a visible banner instead
    # of a hover-only comment, so a second overlapping comment would be
    # redundant noise on the same cell.
    "POS_STATUS_IMPORT": (
        "Sem vlož export stavu POS (aktivní/uzavřené). Stejná struktura pokaždé - "
        "lze vkládat opakovaně, Import Engine vždy aktualizuje POS_MASTER podle POS_ID."
    ),
    "SALESAPP_IMPORT": (
        "Sem vlož export ze SalesApp. Klidně více exportů najednou (např. 2-3 měsíce) - "
        "přidávej pod poslední řádek. Compliance Engine automaticky odstraní duplicity "
        "podle UID návštěvy a zachová historii - bezpečné i pro překrývající se exporty."
    ),
    # Core working screens don't get their own title-banner rows the way
    # HOME/DASHBOARD/IMPORT_HUB do - a banner row would push every real data
    # row down by one and break every engine's "row 1 is the header"
    # assumption for these two specifically (POS_MASTER/ACTIVITY_PLAN are
    # both read positionally by ImportEngine.ts). A header-cell comment gets
    # the same "what is this screen for" clarity without that risk - see the
    # docstring on _nav_button for the same reasoning applied to buttons.
    "POS_MASTER": (
        "POS_MASTER = centrální evidence všech POS. Identifikace vždy podle POS_ID. "
        "Provozovny se stejnou adresou (CORN/9PODNIK) se plánují jako jeden fyzický POS. "
        "Editovat lze jen žlutě podbarvené sloupce (ruční poznámky/výjimky) - zbytek počítají enginy."
    ),
    "ACTIVITY_PLAN": (
        "KROK 4 týdenní rutiny: ACTIVITY_PLAN = plánování kampaní (LOS/LOT). Přidej nebo "
        "uprav kampaň v řádku (typ, název, od týdne, do týdne) a hned vidíš vpravo odhad "
        "dopadu (počet návštěv, časová osa) - žádné přepočítávání ručně. Po úpravě pokračuj "
        "spuštěním Planning Engine (IMPORT_HUB, krok 5)."
    ),
}


def add_sheet_purpose_notes(wb):
    for sheet_name, text in IMPORT_HUB_GUIDANCE.items():
        if sheet_name not in wb.sheetnames:
            continue
        ws = wb[sheet_name]
        ws["A1"].comment = Comment(text, "Field Force Optimizer")


def build_import_hub(wb, pos_master_tech_col="O"):
    """The actual front door for data entry - foolproof by construction, not
    by trusting the user to remember which of 3 staging sheets a file goes
    to. Product owner's real weekly routine is fixed (SalesApp export -> PPT
    campaign assignment -> small activity tweaks -> run planner -> publish),
    so this screen is built around that exact sequence as numbered steps,
    each answering the same 4 questions on-screen instead of in a doc
    nobody reads: where does this data come from, what file goes here, what
    happens after you run the engine, what's the next step.

    Office Scripts have no OS file-picker API (see docs/ARCHITECTURE.md
    section 15d) so this cannot be a literal drag-and-drop widget - it is
    the closest equivalent achievable on this platform: one screen, live
    row counts per staging sheet (so "did my paste work" is answered
    without opening each sheet), one-click links to each paste target.
    Pure presentation - reads existing sheets, writes nothing any engine
    depends on."""
    if "IMPORT_HUB" in wb.sheetnames:
        del wb["IMPORT_HUB"]
    ws = wb.create_sheet("IMPORT_HUB")
    ws.sheet_view.showGridLines = False
    ws.column_dimensions["A"].width = 4
    ws.column_dimensions["B"].width = 30
    for col in "CDEF":
        ws.column_dimensions[col].width = 16
    ws.column_dimensions["G"].width = 20

    ws.merge_cells("A1:G2")
    ws["A1"] = "IMPORT HUB"
    ws["A1"].font = Font(bold=True, size=22, color=WHITE)
    ws["A1"].fill = PatternFill("solid", fgColor=NAVY)
    ws["A1"].alignment = Alignment(horizontal="left", vertical="center", indent=2)
    ws.row_dimensions[1].height = 28
    ws.row_dimensions[2].height = 22
    ws.merge_cells("A3:G3")
    ws["A3"] = "Týdenní rutina - postupuj podle kroků níže, nic jiného řešit nemusíš"
    ws["A3"].font = Font(italic=True, size=11, color=WHITE)
    ws["A3"].fill = PatternFill("solid", fgColor=NAVY)
    ws["A3"].alignment = Alignment(horizontal="left", vertical="center", indent=2)
    ws.row_dimensions[3].height = 20
    ws.freeze_panes = "A4"  # banner stays visible while scrolling through the 5 steps

    r = 5

    def step_card(num, title, source, target_sheet, after, color, extra_note=None):
        nonlocal r
        badge_row = r
        ws.merge_cells(f"B{r}:G{r}")
        ws.cell(r, 2, f"KROK {num} — {title}").font = Font(bold=True, size=13, color=NAVY)
        r += 1
        ws.merge_cells(f"B{r}:G{r}")
        ws.cell(r, 2, f"Odkud: {source}").font = Font(size=10)
        r += 1
        ws.merge_cells(f"B{r}:G{r}")
        ws.cell(r, 2, f"Co se stane po importu: {after}").font = NOTE_FONT
        r += 1
        if target_sheet:
            # RAW_DATA has one extra non-data row at the top (a legacy
            # instruction line inherited from the source workbook, ABOVE its
            # real header - see fix_raw_data_layout()) - COUNTA needs -2
            # there, not -1, to actually count data rows and not off-by-one
            # undercount by exactly one row every time.
            header_rows = 2 if target_sheet == "RAW_DATA" else 1
            ws.cell(r, 2, "Řádků nyní:").font = Font(size=9, color="595959")
            count_cell = ws.cell(r, 3, f'=COUNTA({target_sheet}!A:A)-{header_rows}')
            count_cell.font = Font(bold=True, size=13, color=NAVY)
            status_cell = ws.cell(r, 4, f'=IF(C{r}>0,"✅ Data v systému","⏳ Čeká na vložení")')
            status_cell.font = Font(bold=True, size=9)
            status_cell.alignment = Alignment(horizontal="left")
            ws.conditional_formatting.add(
                status_cell.coordinate,
                FormulaRule(formula=[f'LEFT({status_cell.coordinate},1)="✅"'], font=Font(color="375623", bold=True)),
            )
            ws.conditional_formatting.add(
                status_cell.coordinate,
                FormulaRule(formula=[f'LEFT({status_cell.coordinate},1)="⏳"'], font=Font(color="BF8F00", bold=True)),
            )
            _nav_button(ws, f"F{r}", "Vložit / otevřít →", target_sheet, color=color)
        elif extra_note:
            ws.merge_cells(f"B{r}:G{r}")
            ws.cell(r, 2, extra_note).font = Font(italic=True, size=9, color="808080")
        # Badge spans exactly the 4 rows this card just used (title, source,
        # after, action row) - written last since only now is r_end known.
        ws.merge_cells(f"A{badge_row}:A{r}")
        ws.cell(badge_row, 1, str(num)).font = Font(bold=True, size=18, color=WHITE)
        ws.cell(badge_row, 1).fill = PatternFill("solid", fgColor=color)
        ws.cell(badge_row, 1).alignment = Alignment(horizontal="center", vertical="center")
        r += 2

    step_card(
        1, "Export ze SalesApp (návštěvy)",
        "SalesApp → export realizovaných návštěv za uplynulý týden (klidně i více týdnů/měsíců najednou)",
        "SALESAPP_IMPORT",
        "Compliance Engine sloučí nové řádky s historií, odstraní duplicity podle UID a přepočítá plnění plánu.",
        "2E75B6",
    )
    step_card(
        2, "PPT zadání kampaní (export POS dat)",
        "Export POS dat od zákazníka (PPT zadání) - stejná struktura každý týden, VŽDY kompletní seznam všech POS",
        "RAW_DATA",
        "Import Engine sloučí podle POS_ID; provozovny se stejnou adresou (CORN/9PODNIK) zůstávají jeden fyzický POS. Ruční poznámky u POS se nepřepíší. POS, který v tomto exportu chybí, se automaticky označí jako Closed - žádný zvláštní krok navíc není potřeba.",
        "2E75B6",
        extra_note=None,
    )

    step_card(
        3, "Spusť Import Engine",
        "Záložka Automatizace v Excelu",
        None,
        "POS_MASTER se aktualizuje, historie návštěv se rozšíří (nikdy nepřepisuje ani nemaže).",
        "BF8F00",
        extra_note="⚙ Automatizace → skript \"ImportEngine.ts\" (1. z 3 tento týden: Import → Planning → Publish) - spouští se jako Office Script, ne jako list.",
    )

    # Before Planning Engine: the one check that actually matters here. A
    # POS with no assigned technician never gets planned - Planning Engine
    # has nothing to route it to, and nothing else on this workbook would
    # ever surface that gap. It's invisible until a customer complains their
    # terminal hasn't been serviced in months. Checked right here, between
    # "data just landed" and "run the planner", because this is the one
    # point in the week where fixing it (correcting the assignment in
    # RAW_DATA or via a manager override) is still cheap.
    unassigned_formula = f'=COUNTIFS(POS_MASTER!Q:Q,"Active",POS_MASTER!{pos_master_tech_col}:{pos_master_tech_col},"")'
    ws.merge_cells(f"B{r}:D{r}")
    ws.cell(r, 2, "Aktivní POS bez přiřazeného technika:").font = Font(size=10)
    check_cell = ws.cell(r, 5, unassigned_formula)
    check_cell.font = Font(bold=True, size=14, color=NAVY)
    ws.conditional_formatting.add(
        check_cell.coordinate,
        FormulaRule(formula=[f"{check_cell.coordinate}>0"], fill=PatternFill("solid", fgColor="FCE4D6"), font=Font(bold=True, size=14, color="C00000")),
    )
    _nav_button(ws, f"G{r}", "Zkontrolovat →", "POS_MASTER", color="7030A0")
    r += 2

    ws.merge_cells(f"A{r}:G{r}")
    ws.cell(r, 1, "POKRAČUJ DÁL")
    ws.cell(r, 1).font = TITLE_FONT
    r += 1
    for num, title, target, color in [
        (4, "Uprav aktivity/kampaně (ACTIVITY_PLAN), pokud je potřeba - volitelné, jen když se něco mění", "ACTIVITY_PLAN", "BF8F00"),
        (5, "Automatizace → \"PlanningEngine.ts\" (2. skript), zkontroluj MANAGER_PLAN, pak \"PublishEngine.ts\" (3. skript) publikuje nejbližší týden", "TECHNICIAN_PLAN", "375623"),
    ]:
        ws.cell(r, 1, str(num)).font = Font(bold=True, size=14, color=WHITE)
        ws.cell(r, 1).fill = PatternFill("solid", fgColor=color)
        ws.cell(r, 1).alignment = Alignment(horizontal="center", vertical="center")
        ws.merge_cells(f"B{r}:E{r}")
        ws.cell(r, 2, f"KROK {num} — {title}").font = Font(size=11)
        ws.cell(r, 2).alignment = Alignment(vertical="center")
        _nav_button(ws, f"G{r}", "Otevřít →", target, color=color)
        ws.row_dimensions[r].height = 22
        r += 1
    r += 1

    ws.cell(r, 1, "POSLEDNÍ AKTUALIZACE POS_MASTER").font = Font(size=9, color="595959")
    r += 1
    ws.cell(r, 1, '=IFERROR(TEXT(MAX(POS_MASTER!AM:AM),"DD.MM.YYYY HH:MM"),"zatím žádný import")')
    ws.cell(r, 1).font = Font(bold=True, size=14, color=NAVY)
    r += 2

    _nav_button(ws, f"A{r}", "← Zpět na HOME", "HOME", color="404040")
    return ws


def fix_raw_data_layout(ws, max_rows=500):
    """RAW_DATA is copied verbatim from the legacy V10.5.5 workbook, which
    has a quirk none of the other reference sheets share: row 1 is a stray
    instruction sentence ("Sem vloz export POS"), and the REAL column header
    (CISLO TERMINALU, POS, MARKET, ...) is row 2. ImportEngine.ts already
    knows this (reads raw[1] as the header row, data from raw[2] onward -
    see its own "RAW_DATA COLUMN MAPPING" comment) - this is a confirmed,
    intentional, working data contract, NOT something to "fix" by deleting
    the row (that would be a business-logic-adjacent change to a working
    positional read).

    What WAS actually broken: every generic styling helper in this module
    assumes row 1 is the header, so before this function existed, the
    styling was inverted - the stray instruction sentence got the big navy
    header treatment, and the REAL header row got the pale "this is
    editable data" wash, making the actual column names look like ordinary
    data. Found during a full production review by comparing this sheet
    against the source workbook, not by inspection of the styled workbook
    alone - the mis-styling looked plausible until compared to row 2's
    actual content."""
    # Row 1: a light instruction banner, not a heavy header bar - restyled
    # with a friendlier sentence (ImportEngine.ts never reads this text, so
    # changing it carries no data risk).
    ws["A1"] = "Vlož export POS dat sem (od řádku 3) - lze vkládat i více exportů za sebou"
    ws["A1"].font = Font(italic=True, size=10, color="595959")
    ws["A1"].fill = PatternFill("solid", fgColor=IMPORT_FILL)
    ws.row_dimensions[1].height = 18
    if ws["A1"].comment:
        ws["A1"].comment = None  # redundant with the rewritten instruction text above

    # Row 2: the REAL header - gets the real header treatment.
    for cell in ws[2]:
        if cell.value in (None, ""):
            continue
        cell.font = HEADER_FONT
        cell.fill = HEADER_FILL
        cell.alignment = Alignment(horizontal="center", vertical="center", wrap_text=True)
    ws.row_dimensions[2].height = 28

    # Data wash starts at row 3, not row 2.
    capped_max_row = min(ws.max_row, max_rows)
    for row in ws.iter_rows(min_row=3, max_row=max(capped_max_row, 3), max_col=ws.max_column or 1):
        for cell in row:
            cell.fill = PatternFill("solid", fgColor=IMPORT_FILL)

    last_col = get_column_letter(ws.max_column or 16)
    ws.auto_filter.ref = f"A2:{last_col}{max(ws.max_row, 2)}"
    ws.freeze_panes = "A3"


def color_editable_columns(ws, sheet_name, max_rows=500):
    if sheet_name in IMPORT_STAGING_SHEETS:
        fill = PatternFill("solid", fgColor=IMPORT_FILL)
    elif sheet_name in OUTPUT_SHEETS:
        fill = PatternFill("solid", fgColor=OUTPUT_FILL)
    elif sheet_name in LOG_SHEETS:
        fill = PatternFill("solid", fgColor=LOG_FILL)
    else:
        fill = None

    if fill is not None:
        # Flat wash for the data area - but capped at a reasonable row count.
        # RAW_DATA can carry 11k+ real rows; individually styling every one
        # of those cells would bloat the file for no visual benefit past
        # what's on screen at once, and the tab color + header already
        # signal "this is an import sheet". Cap applies to all sheets in
        # this bucket for one consistent, predictable rule.
        capped_max_row = min(ws.max_row, max_rows)
        for row in ws.iter_rows(min_row=2, max_row=max(capped_max_row, 2), max_col=ws.max_column or 1):
            for cell in row:
                cell.fill = fill
        return

    editable = EDITABLE_COLUMNS.get(sheet_name)
    if not editable:
        return
    idx = _header_index(ws)
    editable_cols = {idx[h] for h in editable if h in idx}
    fill_editable = PatternFill("solid", fgColor=EDITABLE_FILL)
    fill_system = PatternFill("solid", fgColor=SYSTEM_FILL)
    for row in ws.iter_rows(min_row=2, max_row=max(ws.max_row, max_rows), max_col=ws.max_column or 1):
        for cell in row:
            if cell.column in editable_cols:
                cell.fill = fill_editable
            else:
                cell.fill = fill_system


def add_dropdowns(ws, sheet_name, max_rows=500):
    rules = DROPDOWNS.get(sheet_name)
    if not rules:
        return
    idx = _header_index(ws)
    for header, (values, allow_blank) in rules.items():
        if header not in idx:
            continue
        col_letter = get_column_letter(idx[header])
        formula = '"' + ",".join(str(v) for v in values) + '"'
        dv = DataValidation(type="list", formula1=formula, allow_blank=allow_blank, showDropDown=False)
        dv.error = "Vyber prosím hodnotu ze seznamu."
        dv.errorTitle = "Neplatná hodnota"
        ws.add_data_validation(dv)
        dv.add(f"{col_letter}2:{col_letter}{max_rows}")


def protect_config_sheet(ws, sheet_name):
    if sheet_name in ENGINE_WRITABLE:
        return
    editable = set(EDITABLE_COLUMNS.get(sheet_name, []))
    idx = _header_index(ws)
    editable_cols = {idx[h] for h in editable if h in idx}
    for row in ws.iter_rows(min_row=1, max_row=max(ws.max_row, 300), max_col=ws.max_column or 1):
        for cell in row:
            cell.protection = cell.protection.copy(locked=cell.column not in editable_cols)
    ws.protection.sheet = True
    ws.protection.formatFormulas = False
    ws.protection.selectLockedCells = True
    ws.protection.selectUnlockedCells = True


def apply_sheet_order_and_colors(wb):
    order = [name for name, _ in SHEET_GROUPS if name in wb.sheetnames]
    remaining = [n for n in wb.sheetnames if n not in order]
    wb._sheets = [wb[n] for n in order + remaining]
    colors = dict(SHEET_GROUPS)
    for name in wb.sheetnames:
        if name in colors:
            wb[name].sheet_properties.tabColor = colors[name]


def build_legend(ws, start_row):
    ws.cell(start_row, 1, "LEGENDA").font = SECTION_FONT
    legend_rows = [
        (EDITABLE_FILL, "Editovatelné pole - sem zapisuješ hodnoty"),
        (SYSTEM_FILL, "Systémové pole - počítá/zapisuje ho engine, needituj ručně"),
        (IMPORT_FILL, "Import zóna - sem vlož export (Ctrl+A / Ctrl+V přes hlavičku)"),
        (OUTPUT_FILL, "Výstup - generuje Planning/Reporting Engine"),
        (LOG_FILL, "Log - append-only historie, needituj, needituj ani nemaž"),
        (WARNING_FILL, "Neaktivní / čeká na potvrzení hodnoty (viz notes sloupec)"),
    ]
    r = start_row + 1
    for color, text in legend_rows:
        cell = ws.cell(r, 1, "")
        cell.fill = PatternFill("solid", fgColor=color)
        ws.cell(r, 2, text).alignment = Alignment(vertical="center")
        ws.row_dimensions[r].height = 18
        r += 1
    return r


def _nav_button(ws, cell_ref, label, target_sheet, color=NAVY, width=None):
    # Real cell-level hyperlink (openpyxl Cell.hyperlink), NOT a =HYPERLINK()
    # formula. openpyxl never calculates formulas, so a formula-based
    # button is stored with an empty cached value; some Excel contexts
    # recalculate on open (fullCalcOnLoad) and some don't render it until
    # you force a recalc, making the button look dead - this was a real
    # bug (product owner confirmed nav buttons did not work), found and
    # fixed by inspecting the saved XML. A native hyperlink has no
    # calculation dependency at all: it works the instant the file opens.
    cell = ws[cell_ref]
    cell.value = label
    cell.hyperlink = f"#{target_sheet}!A1"
    cell.font = Font(bold=True, size=12, color=WHITE, underline=None)
    cell.fill = PatternFill("solid", fgColor=color)
    cell.alignment = Alignment(horizontal="center", vertical="center")
    cell.border = THIN_BORDER


def build_home(wb, real_control_values, pos_master_tech_col="O"):
    """A real app hub, not a README sheet: a live pipeline status strip
    (each stage checks the actual workbook state - Import/Plan/Rozpis/
    Publikace/Vyhodnocení/Dashboard - and shows Hotovo/Chybí with a
    one-click link), a single "co dělat dál" callout that always points at
    the first incomplete stage, KPI numbers, quick nav and the legend -
    built for someone opening this workbook for the first time to
    understand within 30 seconds where things stand and what to do next,
    without reading any instructions first."""
    for old_name in ("START_HERE", "HOME"):
        if old_name in wb.sheetnames:
            del wb[old_name]
    ws = wb.create_sheet("HOME", 0)
    ws.sheet_view.showGridLines = False
    ws.column_dimensions["A"].width = 3
    for col in "BCDEFGH":
        ws.column_dimensions[col].width = 15

    # ---- Banner ----
    ws.merge_cells("A1:H2")
    ws["A1"] = "FIELD FORCE OPTIMIZER"
    ws["A1"].font = Font(bold=True, size=26, color=WHITE)
    ws["A1"].fill = PatternFill("solid", fgColor=NAVY)
    ws["A1"].alignment = Alignment(horizontal="left", vertical="center", indent=2)
    ws.row_dimensions[1].height = 30
    ws.row_dimensions[2].height = 22
    ws.merge_cells("A3:H3")
    ws["A3"] = "Plánování a řízení terénních techniků"
    ws["A3"].font = Font(italic=True, size=11, color=WHITE)
    ws["A3"].fill = PatternFill("solid", fgColor=NAVY)
    ws["A3"].alignment = Alignment(horizontal="left", vertical="center", indent=2)
    ws.row_dimensions[3].height = 20
    ws.freeze_panes = "A4"  # banner stays visible while scrolling through the pipeline/legend below

    # ---- Pipeline stages: each row's status (col G) is a LIVE formula that
    # reads the actual sheet the stage produces/consumes, not a static
    # instruction. Row numbers are fixed here so the "DALŠÍ KROK" callout
    # above can reference them even though it's written first. ----
    PIPE_FIRST_ROW = 10
    stages = [
        # (num, name, description, status formula, target sheet or None, color)
        (
            "1", "IMPORT DAT", "Otevři Import Hub a vlož export(y) - lze i více najednou",
            '=IF(COUNTA(POS_MASTER!A:A)>1,"✅ Hotovo","❌ Chybí")',
            "IMPORT_HUB", "2E75B6",
        ),
        (
            "2", "PLÁN KAMPANÍ", "Nastav kampaně v ACTIVITY_PLAN",
            '=IF(COUNTA(ACTIVITY_PLAN!A:A)>1,"✅ Hotovo","❌ Chybí")',
            "ACTIVITY_PLAN", "BF8F00",
        ),
        (
            "3", "ROZPIS TECHNIKŮ", "Planning Engine vytvoří rozpis",
            '=IF(COUNTA(MANAGER_PLAN!A:A)>1,"✅ Hotovo","❌ Chybí")',
            "TECHNICIAN_PLAN", "375623",
        ),
        (
            "4", "PUBLIKACE", "Publish Engine odešle plán technikům",
            '=IF(COUNTIF(PLAN_LIFECYCLE!C:C,"Published")+COUNTIF(PLAN_LIFECYCLE!C:C,"Active")>0,"✅ Hotovo","❌ Chybí")',
            None, "BF8F00",
        ),
        (
            "5", "VYHODNOCENÍ", "Compliance + Advisor Engine porovná realitu s plánem",
            '=IF(COUNTA(COMPLIANCE_LOG!A:A)>1,"✅ Hotovo","❌ Chybí")',
            "DASHBOARD", "375623",
        ),
        (
            "6", "DASHBOARD", "Sleduj plnění, KPI a upozornění",
            '=IF(COUNTA(DASHBOARD!A5:A500)>0,"✅ Aktivní","⏳ Čeká na první běh")',
            "DASHBOARD", "375623",
        ),
    ]
    status_cells = [f"G{PIPE_FIRST_ROW + i}" for i in range(len(stages))]
    step_labels = [
        "1) Vlož export POS a SalesApp do RAW_DATA / POS_STATUS_IMPORT / SALESAPP_IMPORT a spusť Import Engine",
        "2) Vytvoř nebo prodluž kampaň v ACTIVITY_PLAN",
        "3) Spusť Planning Engine - vytvoří rozpis technikům",
        "4) Publikuj plán (Publish Engine)",
        "5) Spusť Compliance a Advisor Engine - vyhodnotí skutečné návštěvy proti plánu",
    ]

    # ---- "DALŠÍ KROK" callout: one live sentence, always the first
    # incomplete pipeline stage - this is the answer to "what do I do now",
    # not a checklist the user has to read themselves. ----
    r = 5
    ws.cell(r, 1, "DALŠÍ KROK").font = SECTION_FONT
    r += 1
    ws.merge_cells(f"A{r}:H{r+1}")
    next_step_cell = ws.cell(r, 1)
    ifs_args = []
    for cell_ref, label in zip(status_cells, step_labels):
        ifs_args.append(f'{cell_ref}="❌ Chybí"')
        ifs_args.append(f'"{label}"')
    next_step_cell.value = "=IFS(" + ",".join(ifs_args) + ',TRUE,"✅ Vše hotovo pro tento týden - sleduj plnění na DASHBOARD")'
    next_step_cell.font = Font(bold=True, size=13, color=NAVY)
    next_step_cell.fill = PatternFill("solid", fgColor="FFF2CC")
    next_step_cell.alignment = Alignment(horizontal="left", vertical="center", indent=1, wrap_text=True)
    ws.row_dimensions[r].height = 20
    ws.row_dimensions[r + 1].height = 20
    ws.conditional_formatting.add(
        next_step_cell.coordinate,
        FormulaRule(formula=[f'LEFT({next_step_cell.coordinate},1)="✅"'], fill=PatternFill("solid", fgColor="E2EFDA")),
    )
    r += 3

    # ---- Pipeline status strip ----
    ws.cell(r, 1, "STAV PROCESU").font = TITLE_FONT
    r += 1
    assert r == PIPE_FIRST_ROW, "PIPE_FIRST_ROW must match the row this loop actually starts at"
    green_fill = PatternFill("solid", fgColor="E2EFDA")
    red_fill = PatternFill("solid", fgColor="FCE4D6")
    for num, name, desc, status_formula, target, color in stages:
        ws.cell(r, 1, num).font = Font(bold=True, size=16, color=WHITE)
        ws.cell(r, 1).fill = PatternFill("solid", fgColor=color)
        ws.cell(r, 1).alignment = Alignment(horizontal="center", vertical="center")
        ws.merge_cells(f"B{r}:E{r}")
        ws.cell(r, 2, f"{name} — {desc}").font = Font(size=11)
        ws.cell(r, 2).alignment = Alignment(vertical="center")
        status_cell = ws.cell(r, 7, status_formula)
        status_cell.font = Font(bold=True, size=11)
        status_cell.alignment = Alignment(horizontal="center", vertical="center")
        ws.conditional_formatting.add(
            status_cell.coordinate,
            FormulaRule(formula=[f'LEFT({status_cell.coordinate},1)="✅"'], fill=green_fill),
        )
        ws.conditional_formatting.add(
            status_cell.coordinate,
            FormulaRule(formula=[f'LEFT({status_cell.coordinate},1)="❌"'], fill=red_fill),
        )
        if target:
            _nav_button(ws, f"H{r}", "Otevřít →", target, color=color)
        else:
            ws.cell(r, 8, "⚙ Automatizace")
            ws.cell(r, 8).font = Font(italic=True, size=10, color="808080")
            ws.cell(r, 8).alignment = Alignment(horizontal="center", vertical="center")
        ws.row_dimensions[r].height = 26
        r += 1
    r += 1

    # ---- Status strip (live formulas - stays accurate without any manual
    # update). Two rows of 3 tiles: row 1 is "how big is the plan", row 2 is
    # "how is it going" - the latter simply mirrors DASHBOARD's own KPI
    # tiles (B3/C3) rather than recomputing them, so there is exactly one
    # source of truth for compliance numbers (ReportingEngine.ts), just
    # surfaced here too. Distinct-POS/distinct-technician tiles reuse the
    # same SUMPRODUCT/COUNTIF distinct-count pattern already proven in
    # redesign_activity_plan()'s reference panel. ----
    ws.cell(r, 1, "TENTO TÝDEN").font = SECTION_FONT
    r += 1
    mp_pos_range = "MANAGER_PLAN!E2:E200000"
    mp_tech_range = "MANAGER_PLAN!D2:D200000"
    strip = [
        ("B", "Aktuální kampaň týden", '=IFERROR(VLOOKUP("CAMPAIGN_START_WEEK",CONTROL!A:B,2,FALSE),"-")'),
        ("D", "POS v systému", '=COUNTA(POS_MASTER!A:A)-1'),
        ("F", "Naplánováno návštěv", '=COUNTA(MANAGER_PLAN!A:A)-1'),
        ("B", "POS pokryto plánem", f'=SUMPRODUCT(({mp_pos_range}<>"")/COUNTIF({mp_pos_range},{mp_pos_range}&""))'),
        ("D", "Techniků naplánováno", f'=SUMPRODUCT(({mp_tech_range}<>"")/COUNTIF({mp_tech_range},{mp_tech_range}&""))'),
        ("F", "Compliance (splněno / nesplněno)", '=DASHBOARD!C3&" / "&DASHBOARD!D3'),
        ("B", "Aktivní kampaně", '=COUNTA(ACTIVITY_PLAN!A:A)-1'),
        ("D", "Otevřená upozornění", "=DASHBOARD!E3"),
    ]
    row_offsets = [0, 0, 0, 3, 3, 3, 6, 6]
    for (col, label, formula), row_offset in zip(strip, row_offsets):
        ws[f"{col}{r + row_offset}"] = label
        ws[f"{col}{r + row_offset}"].font = Font(size=9, color="595959")
        value_font = Font(bold=True, size=20, color=NAVY) if row_offset == 0 else Font(bold=True, size=16, color=NAVY)
        ws[f"{col}{r + row_offset + 1}"] = formula
        ws[f"{col}{r + row_offset + 1}"].font = value_font
    ws.conditional_formatting.add(
        f"D{r + 7}",
        FormulaRule(formula=[f"D{r + 7}>0"], fill=PatternFill("solid", fgColor="FCE4D6")),
    )
    r += 9

    # ---- Pre-publish sanity check: the one thing a manager actually needs
    # before hitting Publish for 10-20 technicians once a week - "did anyone
    # get silently left out of this plan". Compares distinct technicians
    # with at least one Active POS assigned (POS_MASTER) against distinct
    # technicians actually present in this week's plan (MANAGER_PLAN). A
    # mismatch is the single costliest realistic weekly mistake in this
    # workflow (a technician shows up Monday with no route) and is
    # otherwise invisible - nothing else on this workbook would surface it
    # before publish. Pure comparison of two counts already computed above,
    # no new business logic, no new engine field. ----
    tech_pm_range = f"POS_MASTER!{pos_master_tech_col}2:{pos_master_tech_col}20000"
    active_range = "POS_MASTER!Q2:Q20000"
    active_tech_count_formula = (
        f'=SUMPRODUCT(({tech_pm_range}<>"")*({active_range}="Active")'
        f'/COUNTIFS({tech_pm_range},{tech_pm_range}&"",{active_range},"Active"))'
    )
    ws.cell(r, 1, "PŘED PUBLIKACÍ ZKONTROLUJ").font = TITLE_FONT
    r += 1
    ws.cell(r, 1, "Technik. s aktivními POS").font = Font(size=9, color="595959")
    ws.cell(r, 3, "Technik. v plánu tento týden").font = Font(size=9, color="595959")
    r += 1
    ws.cell(r, 1, active_tech_count_formula).font = Font(bold=True, size=16, color=NAVY)
    ws.cell(r, 3, f'=SUMPRODUCT(({mp_tech_range}<>"")/COUNTIF({mp_tech_range},{mp_tech_range}&""))').font = Font(bold=True, size=16, color=NAVY)
    check_row = r
    r += 1
    ws.merge_cells(f"A{r}:G{r}")
    check_cell = ws.cell(
        r, 1,
        f'=IF(A{check_row}=C{check_row},"✅ Počty souhlasí",'
        f'"⚠ Nesoulad ("&A{check_row}&" vs "&C{check_row}&") - zkontroluj, jestli někdo nechybí v plánu")'
    )
    check_cell.font = Font(bold=True, size=11, color=NAVY)
    check_cell.fill = PatternFill("solid", fgColor="FFF2CC")
    check_cell.alignment = Alignment(horizontal="left", vertical="center", indent=1)
    ws.row_dimensions[r].height = 20
    ws.conditional_formatting.add(
        check_cell.coordinate,
        FormulaRule(formula=[f'LEFT({check_cell.coordinate},1)="✅"'], fill=PatternFill("solid", fgColor="E2EFDA")),
    )
    ws.conditional_formatting.add(
        check_cell.coordinate,
        FormulaRule(formula=[f'LEFT({check_cell.coordinate},1)="⚠"'], fill=PatternFill("solid", fgColor="FCE4D6")),
    )
    r += 2

    # ---- Quick navigation ----
    ws.cell(r, 1, "RYCHLÁ NAVIGACE").font = TITLE_FONT
    r += 1
    quick_links = [
        ("DASHBOARD", "DASHBOARD", "375623"),
        ("TECHNICIAN_PLAN", "TECHNICIAN_PLAN", "375623"),
        ("POS_MASTER", "POS_MASTER", "7030A0"),
        ("ACTIVITY_PLAN", "ACTIVITY_PLAN", "BF8F00"),
    ]
    col_idx = 1
    for label, target, color in quick_links:
        col_letter = get_column_letter(col_idx)
        _nav_button(ws, f"{col_letter}{r}", label, target, color=color)
        col_idx += 2
    ws.row_dimensions[r].height = 24
    r += 2

    # ---- Legend, inline, not an appendix ----
    ws.cell(r, 1, "JAK ČÍST BARVY").font = TITLE_FONT
    r += 1
    legend_items = [
        (EDITABLE_FILL, "Editovatelné - sem zapisuješ"),
        (SYSTEM_FILL, "Systémové - počítá engine"),
        (IMPORT_FILL, "Sem vlož export"),
        (OUTPUT_FILL, "Výsledek plánování"),
    ]
    col_idx = 1
    for color, text in legend_items:
        col_letter = get_column_letter(col_idx)
        ws[f"{col_letter}{r}"] = ""
        ws[f"{col_letter}{r}"].fill = PatternFill("solid", fgColor=color)
        ws.merge_cells(f"{get_column_letter(col_idx+1)}{r}:{get_column_letter(col_idx+1)}{r}")
        ws.cell(r, col_idx + 1, text).font = Font(size=9)
        ws.cell(r, col_idx + 1).alignment = Alignment(vertical="center", wrap_text=True)
        col_idx += 2
    ws.row_dimensions[r].height = 28
    r += 2

    # ---- First-time setup, kept short - detail lives in office-scripts/README.md ----
    ws.cell(r, 1, "PRVNÍ SPUŠTĚNÍ").font = TITLE_FONT
    r += 1
    for text in [
        "1) Otevři tento sešit v Excelu na webu (OneDrive/SharePoint) - Office Scripts to vyžadují.",
        "2) Záložka Automatizace → New Script → vlož obsah office-scripts/ImportEngine.ts → Spustit.",
        "3) Opakuj pro PlanningEngine.ts, PublishEngine.ts, ComplianceEngine.ts, AdvisorEngine.ts, ReportingEngine.ts.",
    ]:
        ws.merge_cells(f"B{r}:H{r}")
        ws.cell(r, 2, text).font = Font(size=10)
        r += 1

    return ws


def redesign_activity_plan(wb, tech_column_letter):
    """Adds a live impact estimate + a Gantt-style timeline heatmap to the
    RIGHT of the existing A:F data table. The data table itself (A:F) is
    left at its original position/column order deliberately - ImportEngine.ts
    reads it positionally (row[0..5]), and moving it would be a business-
    logic-adjacent risk for a pure UX task. Everything new lives in columns
    G onward, which no engine reads."""
    ws = wb["ACTIVITY_PLAN"]
    # Real data row count from column A (TYPE) - NOT ws.max_row, which can
    # already be inflated by decorative pre-styling of empty future rows if
    # this runs after the generic per-sheet styling pass (it must run
    # before that pass - see apply_all - but this guard makes the function
    # correct regardless of call order).
    n_rows = 1
    for r in range(2, ws.max_row + 1):
        if ws.cell(r, 1).value not in (None, ""):
            n_rows = r
    n_rows = max(n_rows, 2)

    # ---- G: live per-campaign estimate ----
    ws["G1"] = "ODHAD_NAVSTEV_ZA_KAMPAN"
    for r in range(2, n_rows + 1):
        ws.cell(r, 7, f"=IF($C{r}=\"\",\"\",($D{r}-$C{r}+1)*$J$2*$J$5)")

    # ---- I/J: reference values panel that the estimate formulas read ----
    ws["I1"] = "REFERENČNÍ HODNOTY (pro odhad)"
    ws["I1"].font = SECTION_FONT
    pm_range = f"POS_MASTER!{tech_column_letter}2:{tech_column_letter}20000"
    ws["I2"] = "Počet techniků (distinct, z POS_MASTER)"
    ws["J2"] = f'=SUMPRODUCT(({pm_range}<>"")/COUNTIF({pm_range},{pm_range}&""))'
    ws["I3"] = "Cílový počet návštěv/den (CONTROL.TARGET_VISITS_DAY)"
    ws["J3"] = '=IFERROR(VLOOKUP("TARGET_VISITS_DAY",CONTROL!A:B,2,FALSE),8)'
    ws["I4"] = "Průměr pracovních dní/týden (odhad vč. svátků)"
    ws["J4"] = 4.8
    ws["I5"] = "→ Kapacita/technik/týden (řádky J3*J4)"
    ws["J5"] = "=J3*J4"
    for row in (1, 2, 3, 4, 5):
        ws.cell(row, 9).font = NOTE_FONT if row != 1 else SECTION_FONT
    ws["J4"].fill = PatternFill("solid", fgColor=EDITABLE_FILL)

    # ---- L onward: timeline heatmap (weeks as columns) ----
    weeks = []
    for r in range(2, n_rows + 1):
        sv, ev = ws.cell(r, 3).value, ws.cell(r, 4).value
        if isinstance(sv, (int, float)):
            weeks.append(int(sv))
        if isinstance(ev, (int, float)):
            weeks.append(int(ev))
    if weeks:
        week_start, week_end = min(weeks) - 3, max(weeks) + 3
    else:
        week_start, week_end = 1, 20
    week_start = max(1, week_start)

    ws.cell(1, 11, "").fill = PatternFill("solid", fgColor="FFFFFF")  # column K = spacer
    timeline_first_col = 12  # L
    ws.cell(1, timeline_first_col - 1, "ČASOVÁ OSA KAMPANÍ (týden)").font = SECTION_FONT
    for i, week in enumerate(range(week_start, week_end + 1)):
        col = timeline_first_col + i
        cell = ws.cell(1, col, week)
        cell.font = Font(bold=True, size=8)
        cell.alignment = Alignment(horizontal="center")
        ws.column_dimensions[get_column_letter(col)].width = 3.2

    los_fill = PatternFill("solid", fgColor=LOS_FILL)
    lot_fill = PatternFill("solid", fgColor=LOT_FILL)
    last_col_letter = get_column_letter(timeline_first_col + (week_end - week_start))
    for r in range(2, n_rows + 1):
        for i, week in enumerate(range(week_start, week_end + 1)):
            col = timeline_first_col + i
            col_letter = get_column_letter(col)
            week_header_ref = f"{col_letter}$1"
            in_range_formula = f"AND({week_header_ref}>=$C{r},{week_header_ref}<=$D{r})"
            ws.conditional_formatting.add(
                f"{col_letter}{r}",
                FormulaRule(formula=[f'AND({in_range_formula},$A{r}="LOS")'], fill=los_fill),
            )
            ws.conditional_formatting.add(
                f"{col_letter}{r}",
                FormulaRule(formula=[f'AND({in_range_formula},$A{r}="LOT")'], fill=lot_fill),
            )

    # "You are here" - a vertical band on whichever timeline column is the
    # current week, so orientation in a several-months-wide timeline doesn't
    # require counting columns. Safe to compare directly against
    # ISOWEEKNUM(TODAY()): PlanningEngine.ts generates real calendar dates
    # via isoMonday(CONTROL.YEAR, week) using this exact same week number
    # (see ReportingEngine.ts's PLANNING READINESS section for the same
    # reasoning applied elsewhere) - it is a real ISO week number, not an
    # unrelated campaign-relative counter, under the existing documented
    # single-year-per-run simplification (docs/BACKLOG.md).
    # Header-row only (not every data cell) so it never competes with the
    # LOS/LOT fill on the same cell - a header highlight is enough to orient
    # "we are here" in a several-months-wide timeline without hiding which
    # campaign type is active during the current week.
    today_fill = PatternFill("solid", fgColor="FFF2A6")
    for i, week in enumerate(range(week_start, week_end + 1)):
        col_letter = get_column_letter(timeline_first_col + i)
        ws.conditional_formatting.add(
            f"{col_letter}1",
            FormulaRule(formula=[f"{col_letter}1=ISOWEEKNUM(TODAY())"], fill=today_fill),
        )

    ws.cell(n_rows + 3, 12, "LOS").fill = los_fill
    ws.cell(n_rows + 3, 13, "= aktivní LOS kampaň v daném týdnu")
    ws.cell(n_rows + 4, 12, "LOT").fill = lot_fill
    ws.cell(n_rows + 4, 13, "= aktivní LOT kampaň v daném týdnu")
    ws.cell(n_rows + 5, 12, "").fill = today_fill
    ws.cell(n_rows + 5, 13, "= aktuální týden (dnes)")
    ws.cell(n_rows + 6, 12,
            "Souběh dvou kampaní ve stejném týdnu = obě barvy vidíš ve stejném sloupci u různých řádků "
            "(porovnej řádky svisle).").font = NOTE_FONT

    # AutoFilter + banded rows on the editable data table (A:G, including
    # the live estimate column) - a campaign list spanning many months is
    # only actually usable if it can be filtered/sorted like the working
    # screen it is, not just displayed.
    ws.auto_filter.ref = f"A1:G{n_rows}"
    apply_banded_rows(ws, 2, n_rows, 7)

    ws.freeze_panes = "C2"


def build_technician_plan(wb, n_rows=3000, pos_master_notes_col="AK"):
    """This is exactly what a technician gets - almost nothing else. No
    internal category code, no POS_AREA, no system REASON tag: just what's
    needed to go do the visit, in print/export order (TYDEN/week number is
    included - product owner confirmed it's important for them, 2026-07-03
    - unlike the other omissions above, which stay deliberately excluded).
    Pure
    live-formula view (no engine change): stays in sync automatically
    whenever Planning Engine regenerates MANAGER_PLAN, including Draft
    weeks, so the manager sees the full upcoming picture, not just what's
    already Published.

    MANAGER_PLAN column layout this reads from (fixed, see
    scaffold_workbook.py): A=WEEK, B=DATE, C=DAY, D=TECHNICIAN, E=POS,
    F=KATEGORIE, G=NAZEV_PROVOZOVNY, H=ULICE, I=CISLO, J=MESTO, K=OBLAST,
    L=POS_AREA, M=PPT, N=LOS_ACTIVITY, O=LOT_ACTIVITY, P=REASON, Q=GPS_GROUP.
    AKTIVITA combines N+O (a POS can carry both a LOS and a LOT campaign in
    the same week). POZNÁMKA is NOT column P (REASON is an internal
    cadence-engine tag, e.g. "CORE cadence due" - not something a
    technician needs) - it's a live lookup of POS_MASTER.plannerNotes, the
    actual manager-written note for that POS.

    n_rows=3000 note: PlanningEngine.ts keeps every Published/Active/Closed
    week in MANAGER_PLAN forever (never trims old weeks), so this static
    row cap (~2.5 weeks of typical volume at ~1200 rows/week) is a real
    limitation once the workbook has been in weekly use for a while, not
    just a formatting choice - flagged in docs/BACKLOG.md as a follow-up
    (either raise the cap, or - better - give MANAGER_PLAN an actual
    archival strategy, which was already a known future need for exactly
    this reason)."""
    if "TECHNICIAN_PLAN" in wb.sheetnames:
        del wb["TECHNICIAN_PLAN"]
    ws = wb.create_sheet("TECHNICIAN_PLAN")

    headers = [
        "TYDEN", "DATUM", "DEN", "TECHNIK", "POS", "ČÍSLO TERMINÁLU", "NÁZEV PROVOZOVNY",
        "ULICE", "MĚSTO", "OBLAST", "AKTIVITA", "POZNÁMKA",
    ]
    for i, h in enumerate(headers):
        ws.cell(1, i + 1, h)

    formulas = [
        lambda r: f'=IF(MANAGER_PLAN!E{r}="","",MANAGER_PLAN!A{r})',  # TYDEN
        lambda r: f'=IF($E{r}="","",MANAGER_PLAN!B{r})',  # DATUM
        # DEN: dates.ts's workDays() names Monday-Friday as MON/TUE/WED/THU/FRI
        # (English abbreviations, fine for an internal sheet like MANAGER_PLAN)
        # - translated to Czech here since this sheet is what a technician
        # actually reads. SWITCH falls back to the raw value for anything
        # unexpected rather than showing blank, so a format change upstream
        # fails loud, not silent.
        lambda r: (
            f'=IF($E{r}="","",SWITCH(MANAGER_PLAN!C{r},'
            f'"MON","Pondělí","TUE","Úterý","WED","Středa","THU","Čtvrtek","FRI","Pátek",'
            f'MANAGER_PLAN!C{r}))'
        ),  # DEN
        lambda r: f'=IF(MANAGER_PLAN!E{r}="","",MANAGER_PLAN!D{r})',  # TECHNIK
        lambda r: f'=IF(MANAGER_PLAN!E{r}="","",MANAGER_PLAN!E{r})',  # POS
        # ČÍSLO TERMINÁLU: live lookup of POS_MASTER.terminalId (column B) -
        # product owner asked for the terminal number alongside the week
        # number, 2026-07-03. NOTE: POS_MASTER currently stores exactly one
        # terminalId per POS row - if a POS genuinely has 2 terminals (product
        # owner confirmed this can happen), ImportEngine.ts's current
        # one-RAW_DATA-row-per-POS_MASTER-row upsert doesn't yet represent
        # that (flagged in docs/BACKLOG.md as a follow-up, not silently
        # assumed away); this lookup shows whichever terminal that POS's
        # single POS_MASTER row currently has.
        lambda r: (
            f'=IF($E{r}="","",IFERROR(VLOOKUP($E{r},POS_MASTER!$A:$B,2,FALSE),""))'
        ),  # CISLO TERMINALU
        lambda r: f'=IF($E{r}="","",MANAGER_PLAN!G{r})',  # NAZEV PROVOZOVNY
        lambda r: f'=IF($E{r}="","",TRIM(MANAGER_PLAN!H{r}&" "&MANAGER_PLAN!I{r}))',  # ULICE (+ CISLO)
        lambda r: f'=IF($E{r}="","",MANAGER_PLAN!J{r})',  # MESTO
        lambda r: f'=IF($E{r}="","",MANAGER_PLAN!K{r})',  # OBLAST
        lambda r: (
            f'=IF($E{r}="","",TRIM(IF(MANAGER_PLAN!N{r}<>"","LOS: "&MANAGER_PLAN!N{r}&" ","")'
            f'&IF(MANAGER_PLAN!O{r}<>"","LOT: "&MANAGER_PLAN!O{r},"")))'
        ),  # AKTIVITA
        lambda r: (
            f'=IF($E{r}="","",IFERROR(VLOOKUP($E{r},POS_MASTER!$A:${pos_master_notes_col},'
            f'{pos_master_notes_col_index(pos_master_notes_col)},FALSE),""))'
        ),  # POZNAMKA (manager note from POS_MASTER, not the internal REASON tag)
    ]
    for r in range(2, n_rows + 1):
        for i, formula_fn in enumerate(formulas):
            ws.cell(r, i + 1, formula_fn(r))

    for i, h in enumerate(headers):
        width = 8 if h == "TYDEN" else 12 if h in ("DATUM", "DEN", "TECHNIK", "POS", "ČÍSLO TERMINÁLU") else 20
        ws.column_dimensions[get_column_letter(i + 1)].width = width

    ws.auto_filter.ref = f"A1:L{n_rows}"
    apply_banded_rows(ws, 2, n_rows, len(headers))
    # Highlight today's visits - the one row (or handful, one per technician)
    # a technician actually needs when they open this sheet on the day
    # itself, so they don't have to filter/scroll to find it.
    ws.conditional_formatting.add(
        f"A2:L{n_rows}",
        FormulaRule(formula=["$B2=TODAY()"], fill=PatternFill("solid", fgColor="FFF2A6")),
    )
    ws.freeze_panes = "A2"

    # Print-ready: this sheet is explicitly meant to be printed/exported per
    # technician, not just viewed on screen.
    ws.page_setup.orientation = "landscape"
    ws.page_setup.fitToWidth = 1
    ws.page_setup.fitToHeight = 0
    ws.sheet_properties.pageSetUpPr.fitToPage = True
    ws.print_title_rows = "1:1"
    return ws


def pos_master_notes_col_index(col_letter):
    return column_index_from_string(col_letter)


def apply_banded_rows(ws, first_data_row, last_row, n_cols, band_color="F2F2F2"):
    """Alternating row shading via conditional formatting (not direct cell
    fill) - survives engine clear(contents)/setValues() cycles because it's
    attached to the range, not to individual cell styles that clear() could
    interact with, and doesn't need the range to actually contain data to
    render correctly for a growing/shrinking table."""
    last_col_letter = get_column_letter(n_cols)
    band_range = f"A{first_data_row}:{last_col_letter}{last_row}"
    ws.conditional_formatting.add(
        band_range,
        FormulaRule(formula=[f"ISEVEN(ROW())"], fill=PatternFill("solid", fgColor=band_color)),
    )


def hide_technical_sheets(wb):
    for name in HIDDEN_SHEETS:
        if name in wb.sheetnames:
            wb[name].sheet_state = "hidden"


def build_dashboard_template(wb):
    """Pre-styles a KPI-tile header band that ReportingEngine.ts writes
    numbers into (fixed cell positions, see ReportingEngine.ts). The
    detailed tables ReportingEngine already produces are kept below this
    band, just pushed down to make room - same data, better hierarchy.

    Also builds the three native Excel charts (weekly trend, technician
    workload, regional completion) bound to the FIXED chart-data ranges in
    columns H:K that ReportingEngine.ts writes on every run (see that
    file's "CHART DATA BLOCKS" comment for why fixed ranges, not the
    flowing detail sections, are what a chart can safely reference). Charts
    are openpyxl objects created once here; Office Scripts never touches
    them, only the cell values they read from - so a chart keeps rendering
    correctly across every future engine run with no further Python step."""
    ws = wb["DASHBOARD"]
    ws.sheet_view.showGridLines = False
    ws.column_dimensions["A"].width = 22
    for col in "BCDEF":
        ws.column_dimensions[col].width = 18
    for col in "HIJK":
        ws.column_dimensions[col].width = 14

    ws.merge_cells("A1:F1")
    ws["A1"] = "DASHBOARD"
    ws["A1"].font = Font(bold=True, size=20, color=WHITE)  # matches IMPORT_HUB's banner size - HOME (26) is the only intentionally-larger one, as the primary entry point
    ws["A1"].fill = PatternFill("solid", fgColor=NAVY)
    ws["A1"].alignment = Alignment(horizontal="left", vertical="center", indent=1)
    ws.row_dimensions[1].height = 30

    tile_specs = [
        ("B3", "Aktivní POS", "375623"),
        ("C3", "Splněno včas", "375623"),
        ("D3", "Nesplněno", "C00000"),
        ("E3", "Otevřené alerty", "BF8F00"),
    ]
    for cell_ref, label, color in tile_specs:
        col = cell_ref[0]
        ws[f"{col}2"] = label
        ws[f"{col}2"].font = Font(bold=True, size=9, color="595959")
        ws[f"{col}2"].alignment = Alignment(horizontal="center")
        ws[cell_ref].font = Font(bold=True, size=22, color=color)
        ws[cell_ref].alignment = Alignment(horizontal="center", vertical="center")
        ws.row_dimensions[3].height = 34
    ws.freeze_panes = "A5"

    # Severity badges for the flowing ADVISOR ALERTS rows (ReportingEngine.ts
    # writes "TYPE (SEVERITY)" as column A of each alert row, e.g.
    # "TECHNICIAN_OVERLOAD (CRITICAL)") - a colored left-edge cue so a
    # CRITICAL alert is visually distinct from an informational one without
    # reading the text. Row-range based (A5:A2000), not tied to any fixed
    # row count, since the flowing section's length varies run to run - a
    # text-match rule keeps working regardless of exactly which row it lands
    # on, unlike the chart data blocks above which need fixed positions.
    severity_colors = [("CRITICAL", "C00000"), ("WARNING", "BF8F00"), ("INFO", "2E75B6")]
    for keyword, color in severity_colors:
        ws.conditional_formatting.add(
            "A5:A2000",
            FormulaRule(
                formula=[f'ISNUMBER(SEARCH("({keyword})",$A5))'],
                fill=PatternFill("solid", fgColor=color),
                font=Font(color=WHITE, bold=True),
            ),
        )

    _build_dashboard_charts(ws)
    return ws


def _build_dashboard_charts(ws):
    # ---- WEEKLY TREND: label H1, header H2:K2, data H3:K14 (12 weeks) ----
    ws["H1"] = "📈 VÝVOJ PLNĚNÍ PO TÝDNECH"
    ws["H1"].font = SECTION_FONT
    for col, label in zip("HIJK", ["Week", "Splněno včas", "Splněno pozdě", "Nesplněno"]):
        ws[f"{col}2"] = label
        ws[f"{col}2"].font = Font(bold=True, size=9, color="595959")

    weekly_chart = LineChart()
    weekly_chart.title = "Plnění plánovaných návštěv po týdnech"
    weekly_chart.style = 2
    weekly_chart.y_axis.title = "Počet návštěv"
    weekly_chart.x_axis.title = "Týden"
    weekly_chart.height = 8
    weekly_chart.width = 22
    cats = Reference(ws, min_col=8, min_row=3, max_row=14)
    for col, name, color in [(9, "Splněno včas", "375623"), (10, "Splněno pozdě", "BF8F00"), (11, "Nesplněno", "C00000")]:
        data = Reference(ws, min_col=col, min_row=2, max_row=14)
        weekly_chart.add_data(data, titles_from_data=True)
    weekly_chart.set_categories(cats)
    for series, color in zip(weekly_chart.series, ["375623", "BF8F00", "C00000"]):
        series.graphicalProperties.line.solidFill = color
        series.graphicalProperties.line.width = 20000
        series.smooth = False
    ws.add_chart(weekly_chart, "M1")

    # ---- TECHNICIAN WORKLOAD: label H17, header H18:K18, data H19:K32 ----
    ws["H17"] = "👥 VYTÍŽENÍ TECHNIKŮ (nejnovější týden)"
    ws["H17"].font = SECTION_FONT
    for col, label in zip("HIJK", ["Technik", "Naplánováno", "Kapacita", "Vytížení %"]):
        ws[f"{col}18"] = label
        ws[f"{col}18"].font = Font(bold=True, size=9, color="595959")

    workload_chart = BarChart()
    workload_chart.type = "col"
    workload_chart.title = "Naplánováno vs. kapacita (aktuální týden)"
    workload_chart.style = 10
    workload_chart.y_axis.title = "Počet návštěv"
    workload_chart.height = 8
    workload_chart.width = 22
    w_cats = Reference(ws, min_col=8, min_row=19, max_row=32)
    for col, color in [(9, "375623"), (10, "BFBFBF")]:
        data = Reference(ws, min_col=col, min_row=18, max_row=32)
        workload_chart.add_data(data, titles_from_data=True)
    workload_chart.set_categories(w_cats)
    for series, color in zip(workload_chart.series, ["375623", "BFBFBF"]):
        series.graphicalProperties.solidFill = color
    ws.add_chart(workload_chart, "M19")  # 2-row buffer below the weekly trend chart above (M1, ~15 rows tall) so they don't visually crowd each other

    # Progress bar on Utilization % (K19:K32) - safe here (unlike the
    # flowing detail sections, where the same column means different things
    # in different sections) since this fixed block has exactly one meaning
    # per column.
    ws.conditional_formatting.add(
        "K19:K32",
        DataBarRule(start_type="num", start_value=0, end_type="num", end_value=150, color="375623"),
    )

    # ---- REGIONAL OVERVIEW: label H35, header H36:I36, data H37:I48 ----
    ws["H35"] = "🗺 REGIONÁLNÍ PŘEHLED (completion %)"
    ws["H35"].font = SECTION_FONT
    for col, label in zip("HI", ["Market", "Completion %"]):
        ws[f"{col}36"] = label
        ws[f"{col}36"].font = Font(bold=True, size=9, color="595959")

    regional_chart = BarChart()
    regional_chart.type = "bar"  # horizontal - reads better with region names
    regional_chart.title = "Splnění plánu podle regionu"
    regional_chart.style = 12
    regional_chart.y_axis.title = "Completion %"
    regional_chart.height = 8
    regional_chart.width = 22
    r_cats = Reference(ws, min_col=8, min_row=37, max_row=48)
    r_data = Reference(ws, min_col=9, min_row=36, max_row=48)
    regional_chart.add_data(r_data, titles_from_data=True)
    regional_chart.set_categories(r_cats)
    regional_chart.series[0].graphicalProperties.solidFill = "2E75B6"
    ws.add_chart(regional_chart, "M37")  # same buffer reasoning as the workload chart above

    # Progress bar on Completion % (I37:I48) - same reasoning as above.
    ws.conditional_formatting.add(
        "I37:I48",
        DataBarRule(start_type="num", start_value=0, end_type="num", end_value=100, color="2E75B6"),
    )


def find_tech_column_letter(pos_master_header_row):
    for i, h in enumerate(pos_master_header_row):
        if h == "assignedTechnician":
            return get_column_letter(i + 1)
    return "O"


def enhance_pos_master(wb, max_rows=20000):
    """POS_MASTER is the planner's working registry, not a report - a
    manager scanning it needs to spot "which POS need my attention" without
    reading every row. Three visual cues, all pure presentation over fields
    engines already compute (no new business logic, no new field):
      - status badge (Active=green, Closed=grey)
      - neglected-risk highlight on weeksSinceLastVisit, using the SAME
        NEGLECTED_AFTER_WEEKS threshold AdvisorEngine.ts already uses for
        its own NEGLECT_RISK alert - one threshold, read from CONTROL, not
        a second hardcoded copy of the number
      - manual-override highlight (managerOverrideType non-blank) so an
        exception a manager set weeks ago doesn't silently get forgotten
    Plus AutoFilter, since a registry the user can't filter isn't usable as
    a working screen."""
    if "POS_MASTER" not in wb.sheetnames:
        return
    ws = wb["POS_MASTER"]
    header = [c.value for c in ws[1]]
    col = lambda name: get_column_letter(header.index(name) + 1) if name in header else None

    last_col = get_column_letter(ws.max_column or 39)
    ws.auto_filter.ref = f"A1:{last_col}{max_rows}"

    status_col = col("status")
    if status_col:
        ws.conditional_formatting.add(
            f"{status_col}2:{status_col}{max_rows}",
            FormulaRule(formula=[f'{status_col}2="Active"'], font=Font(color="375623", bold=True)),
        )
        ws.conditional_formatting.add(
            f"{status_col}2:{status_col}{max_rows}",
            FormulaRule(formula=[f'{status_col}2="Closed"'], font=Font(color="808080")),
        )

    weeks_col = col("weeksSinceLastVisit")
    if weeks_col:
        threshold_formula = 'IFERROR(VLOOKUP("NEGLECTED_AFTER_WEEKS",CONTROL!$A:$B,2,FALSE),26)'
        ws.conditional_formatting.add(
            f"{weeks_col}2:{weeks_col}{max_rows}",
            FormulaRule(
                formula=[f'AND({weeks_col}2<>"",{weeks_col}2>={threshold_formula})'],
                fill=PatternFill("solid", fgColor=WARNING_FILL),
            ),
        )

    override_col = col("managerOverrideType")
    if override_col:
        ws.conditional_formatting.add(
            f"{override_col}2:{override_col}{max_rows}",
            FormulaRule(
                formula=[f'{override_col}2<>""'],
                fill=PatternFill("solid", fgColor="E2D4F0"),
                font=Font(bold=True),
            ),
        )


def apply_all(wb, control_rows):
    """Single entry point called by scaffold_workbook.py after all sheets
    and data are populated."""
    control_values = {}
    for row in control_rows[1:]:
        if row and row[0]:
            control_values[str(row[0]).strip()] = row[1] if len(row) > 1 else ""

    # Build TECHNICIAN_PLAN before the generic styling pass touches
    # anything, and ACTIVITY_PLAN's timeline redesign likewise - that pass
    # decoratively pre-styles empty future rows (up to row 500), which
    # would inflate ws.max_row and confuse row-count-dependent logic.
    if "POS_MASTER" in wb.sheetnames:
        pm_header = [c.value for c in wb["POS_MASTER"][1]]
        tech_col = find_tech_column_letter(pm_header)
    else:
        tech_col = "O"
    if "ACTIVITY_PLAN" in wb.sheetnames:
        redesign_activity_plan(wb, tech_col)
    if "MANAGER_PLAN" in wb.sheetnames:
        build_technician_plan(wb)
    if "DASHBOARD" in wb.sheetnames:
        build_dashboard_template(wb)
    if "POS_MASTER" in wb.sheetnames:
        apply_banded_rows(wb["POS_MASTER"], 2, 500, wb["POS_MASTER"].max_column or 39)
        enhance_pos_master(wb)

    for sheet_name in list(wb.sheetnames):
        ws = wb[sheet_name]
        if ws.max_row == 0 or ws.max_column == 0:
            continue
        if sheet_name == "TECHNICIAN_PLAN":
            style_header_row(ws)  # header only - fill/dropdowns don't apply, it's a formula view
            continue
        if sheet_name == "DASHBOARD":
            continue  # build_dashboard_template already fully styled it
        if sheet_name == "ACTIVITY_PLAN":
            # freeze_below=False: redesign_activity_plan() already set
            # freeze_panes="C2" (keep TYPE+ACTIVITY visible while scrolling
            # through a several-months-wide timeline) - this generic pass's
            # default freeze_below=True would silently reset it to "A2"
            # (row-only freeze), which was a real bug: found while testing
            # multi-month timeline orientation, it meant the campaign
            # name/type scrolled off-screen exactly when the timeline was
            # most useful.
            style_header_row(ws, freeze_below=False)
            color_editable_columns(ws, sheet_name)
            add_dropdowns(ws, sheet_name)
            continue
        if sheet_name == "RAW_DATA":
            # RAW_DATA's real header is row 2, not row 1 - see
            # fix_raw_data_layout()'s docstring. Handled entirely there
            # instead of the generic row-1-is-header helpers.
            fix_raw_data_layout(ws)
            continue
        style_header_row(ws)
        color_editable_columns(ws, sheet_name)
        add_dropdowns(ws, sheet_name)
        if sheet_name in IMPORT_UTILITY_SHEETS:
            # POS_STATUS_IMPORT/SALESAPP_IMPORT (RAW_DATA handled separately
            # above) - found missing during a full production review: every
            # other working screen had AutoFilter, these two didn't.
            last_col = get_column_letter(ws.max_column or 16)
            ws.auto_filter.ref = f"A1:{last_col}{max(ws.max_row, 2)}"

    for sheet_name in list(wb.sheetnames):
        protect_config_sheet(wb[sheet_name], sheet_name)

    add_sheet_purpose_notes(wb)
    build_import_hub(wb, pos_master_tech_col=tech_col)

    build_home(wb, {
        k: v for k, v in control_values.items()
        if k in ("CAMPAIGN_START_WEEK", "CAMPAIGN_LENGTH", "VISITS_PER_WEEK",
                  "TARGET_VISITS_DAY", "YEAR")
    }, pos_master_tech_col=tech_col)
    apply_sheet_order_and_colors(wb)  # re-apply so HOME lands first
    hide_technical_sheets(wb)
    wb.active = 0  # HOME is what the user sees on open
