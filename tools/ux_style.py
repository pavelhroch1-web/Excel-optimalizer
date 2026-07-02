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
from openpyxl.formatting.rule import FormulaRule
from openpyxl.worksheet.datavalidation import DataValidation
from openpyxl.utils import get_column_letter, column_index_from_string

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
CORE_DAILY_SHEETS = ["HOME", "DASHBOARD", "TECHNICIAN_PLAN", "POS_MASTER", "ACTIVITY_PLAN"]
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
    "VISIT_HISTORY_ACTUAL", "VISIT_HISTORY",
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


def build_home(wb, real_control_values):
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

    # ---- Pipeline stages: each row's status (col G) is a LIVE formula that
    # reads the actual sheet the stage produces/consumes, not a static
    # instruction. Row numbers are fixed here so the "DALŠÍ KROK" callout
    # above can reference them even though it's written first. ----
    PIPE_FIRST_ROW = 10
    stages = [
        # (num, name, description, status formula, target sheet or None, color)
        (
            "1", "IMPORT DAT", "Nahraj export POS a SalesApp",
            '=IF(COUNTA(POS_MASTER!A:A)>1,"✅ Hotovo","❌ Chybí")',
            "RAW_DATA", "2E75B6",
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

    ws.cell(n_rows + 3, 12, "LOS").fill = los_fill
    ws.cell(n_rows + 3, 13, "= aktivní LOS kampaň v daném týdnu")
    ws.cell(n_rows + 4, 12, "LOT").fill = lot_fill
    ws.cell(n_rows + 4, 13, "= aktivní LOT kampaň v daném týdnu")
    ws.cell(n_rows + 5, 12,
            "Souběh dvou kampaní ve stejném týdnu = obě barvy vidíš ve stejném sloupci u různých řádků "
            "(porovnej řádky svisle).").font = NOTE_FONT
    ws.freeze_panes = "C2"


def build_technician_plan(wb, n_rows=3000, pos_master_notes_col="AK"):
    """This is exactly what a technician gets - nothing else. No WEEK
    counter, no internal category code, no POS_AREA, no system REASON tag:
    just what's needed to go do the visit, in print/export order. Pure
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
        "DATUM", "DEN", "TECHNIK", "POS", "NÁZEV PROVOZOVNY",
        "ULICE", "MĚSTO", "OBLAST", "AKTIVITA", "POZNÁMKA",
    ]
    for i, h in enumerate(headers):
        ws.cell(1, i + 1, h)

    formulas = [
        lambda r: f'=IF($D{r}="","",MANAGER_PLAN!B{r})',  # DATUM
        lambda r: f'=IF($D{r}="","",MANAGER_PLAN!C{r})',  # DEN
        lambda r: f'=IF(MANAGER_PLAN!E{r}="","",MANAGER_PLAN!D{r})',  # TECHNIK
        lambda r: f'=IF(MANAGER_PLAN!E{r}="","",MANAGER_PLAN!E{r})',  # POS
        lambda r: f'=IF($D{r}="","",MANAGER_PLAN!G{r})',  # NAZEV PROVOZOVNY
        lambda r: f'=IF($D{r}="","",TRIM(MANAGER_PLAN!H{r}&" "&MANAGER_PLAN!I{r}))',  # ULICE (+ CISLO)
        lambda r: f'=IF($D{r}="","",MANAGER_PLAN!J{r})',  # MESTO
        lambda r: f'=IF($D{r}="","",MANAGER_PLAN!K{r})',  # OBLAST
        lambda r: (
            f'=IF($D{r}="","",TRIM(IF(MANAGER_PLAN!N{r}<>"","LOS: "&MANAGER_PLAN!N{r}&" ","")'
            f'&IF(MANAGER_PLAN!O{r}<>"","LOT: "&MANAGER_PLAN!O{r},"")))'
        ),  # AKTIVITA
        lambda r: (
            f'=IF($D{r}="","",IFERROR(VLOOKUP($D{r},POS_MASTER!$A:${pos_master_notes_col},'
            f'{pos_master_notes_col_index(pos_master_notes_col)},FALSE),""))'
        ),  # POZNAMKA (manager note from POS_MASTER, not the internal REASON tag)
    ]
    for r in range(2, n_rows + 1):
        for i, formula_fn in enumerate(formulas):
            ws.cell(r, i + 1, formula_fn(r))

    for i, h in enumerate(headers):
        width = 12 if h in ("DATUM", "DEN", "TECHNIK", "POS") else 20
        ws.column_dimensions[get_column_letter(i + 1)].width = width

    ws.auto_filter.ref = f"A1:J{n_rows}"
    apply_banded_rows(ws, 2, n_rows, len(headers))
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
    band, just pushed down to make room - same data, better hierarchy."""
    ws = wb["DASHBOARD"]
    ws.sheet_view.showGridLines = False
    ws.column_dimensions["A"].width = 22
    for col in "BCDEF":
        ws.column_dimensions[col].width = 18

    ws.merge_cells("A1:F1")
    ws["A1"] = "DASHBOARD"
    ws["A1"].font = Font(bold=True, size=18, color=WHITE)
    ws["A1"].fill = PatternFill("solid", fgColor=NAVY)
    ws["A1"].alignment = Alignment(horizontal="left", vertical="center", indent=1)
    ws.row_dimensions[1].height = 32

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
    return ws


def find_tech_column_letter(pos_master_header_row):
    for i, h in enumerate(pos_master_header_row):
        if h == "assignedTechnician":
            return get_column_letter(i + 1)
    return "O"


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

    for sheet_name in list(wb.sheetnames):
        ws = wb[sheet_name]
        if ws.max_row == 0 or ws.max_column == 0:
            continue
        if sheet_name == "TECHNICIAN_PLAN":
            style_header_row(ws)  # header only - fill/dropdowns don't apply, it's a formula view
            continue
        if sheet_name == "DASHBOARD":
            continue  # build_dashboard_template already fully styled it
        style_header_row(ws)
        color_editable_columns(ws, sheet_name)
        add_dropdowns(ws, sheet_name)

    if "ACTIVITY_PLAN" in wb.sheetnames:
        # re-apply header styling (no freeze - handled by redesign's own
        # freeze_panes) since the generic pass above re-touched row 1.
        style_header_row(wb["ACTIVITY_PLAN"], freeze_below=False)

    for sheet_name in list(wb.sheetnames):
        protect_config_sheet(wb[sheet_name], sheet_name)

    build_home(wb, {
        k: v for k, v in control_values.items()
        if k in ("CAMPAIGN_START_WEEK", "CAMPAIGN_LENGTH", "VISITS_PER_WEEK",
                  "TARGET_VISITS_DAY", "YEAR")
    })
    apply_sheet_order_and_colors(wb)  # re-apply so HOME lands first
    hide_technical_sheets(wb)
    wb.active = 0  # HOME is what the user sees on open
