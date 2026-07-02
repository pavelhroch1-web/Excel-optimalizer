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
from openpyxl.utils import get_column_letter

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
    ("START_HERE", "404040"),
    ("DASHBOARD", "375623"),
    ("POS_MASTER", "7030A0"),
    ("MANAGER_PLAN", "375623"),
    ("MANAGER_PLAN_PUBLISHED", "375623"),
    ("PLAN_LIFECYCLE", "375623"),
    ("RAW_DATA", "2E75B6"),
    ("POS_STATUS_IMPORT", "2E75B6"),
    ("SALESAPP_IMPORT", "2E75B6"),
    ("CONTROL", "BF8F00"),
    ("ACTIVITY_PLAN", "BF8F00"),
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


def build_start_here(wb, real_control_values):
    if "START_HERE" in wb.sheetnames:
        del wb["START_HERE"]
    ws = wb.create_sheet("START_HERE", 0)
    ws.sheet_view.showGridLines = False
    ws.column_dimensions["A"].width = 3
    ws.column_dimensions["B"].width = 90

    r = 1
    ws.cell(r, 2, "Field Force Optimizer V11").font = Font(bold=True, size=20, color=NAVY)
    r += 1
    ws.cell(r, 2, "Plánovací a vyhodnocovací systém pro terénní techniky - postaveno nad Excel + Office Scripts.").font = NOTE_FONT
    r += 2

    def section(title):
        nonlocal r
        ws.cell(r, 2, title).font = TITLE_FONT
        r += 1

    def bullet(text):
        nonlocal r
        ws.cell(r, 2, "•  " + text).alignment = Alignment(wrap_text=True, vertical="top")
        ws.row_dimensions[r].height = 16
        r += 1

    section("1. První spuštění")
    bullet("Otevři tento sešit v Excelu ONLINE (Office Scripts vyžaduje OneDrive/SharePoint).")
    bullet("Automatizace → New Script → vlož obsah office-scripts/ImportEngine.ts → spusť.")
    bullet("Zkontroluj list POS_MASTER - měl by se naplnit z RAW_DATA.")
    r += 1

    section("2. Týdenní workflow")
    bullet("1) Vlož nový export do RAW_DATA (a POS_STATUS_IMPORT / SALESAPP_IMPORT, pokud je máš).")
    bullet("2) Spusť ImportEngine.ts.")
    bullet("3) Spusť PlanningEngine.ts - vygeneruje návrh (Draft) v MANAGER_PLAN.")
    bullet("4) Zkontroluj/uprav plán ručně (POS_MASTER - sloupce managerOverride*).")
    bullet("5) Až budeš spokojen: spusť PublishEngine.ts - zamkne nejbližší týden a odešli ho technikům.")
    bullet("6) Když přijde nový SalesApp export: vlož ho do SALESAPP_IMPORT a spusť ComplianceEngine.ts.")
    bullet("7) Spusť AdvisorEngine.ts pro upozornění a ReportingEngine.ts pro aktualizaci DASHBOARDu.")
    r += 1

    section("3. Kde co najdeš")
    bullet("DASHBOARD - přehled sítě, plnění, KPI techniků, aktuální upozornění.")
    bullet("POS_MASTER - hlavní pracovní karta každého POS (tady děláš ruční zásahy).")
    bullet("MANAGER_PLAN - aktuální návrh plánu (Draft + zamčené Published týdny).")
    bullet("Žluté listy (CONTROL, ACTIVITY_PLAN, ...RULES...) - konfigurace, kterou upravuješ ty.")
    bullet("Šedé listy (COMPLIANCE_LOG, ADVISOR_LOG, ...) - historie, kterou spravuje systém.")
    r += 1

    section("4. Aktuální nastavení kampaně (informativní)")
    for k, v in real_control_values.items():
        bullet(f"{k} = {v}")
    r += 1

    build_legend(ws, r)
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

    # ACTIVITY_PLAN's timeline redesign must run BEFORE the generic per-sheet
    # styling pass below - that pass decoratively pre-styles empty future
    # rows (up to row 500), which would inflate ws.max_row and make the
    # timeline/estimate section think there are hundreds of campaign rows.
    if "POS_MASTER" in wb.sheetnames:
        pm_header = [c.value for c in wb["POS_MASTER"][1]]
        tech_col = find_tech_column_letter(pm_header)
    else:
        tech_col = "O"
    if "ACTIVITY_PLAN" in wb.sheetnames:
        redesign_activity_plan(wb, tech_col)

    for sheet_name in list(wb.sheetnames):
        ws = wb[sheet_name]
        if ws.max_row == 0 or ws.max_column == 0:
            continue
        style_header_row(ws)
        color_editable_columns(ws, sheet_name)
        add_dropdowns(ws, sheet_name)

    if "ACTIVITY_PLAN" in wb.sheetnames:
        # re-apply header styling (no freeze - handled by redesign's own
        # freeze_panes) since the generic pass above re-touched row 1.
        style_header_row(wb["ACTIVITY_PLAN"], freeze_below=False)

    for sheet_name in list(wb.sheetnames):
        protect_config_sheet(wb[sheet_name], sheet_name)

    apply_sheet_order_and_colors(wb)
    build_start_here(wb, {
        k: v for k, v in control_values.items()
        if k in ("CAMPAIGN_START_WEEK", "CAMPAIGN_LENGTH", "VISITS_PER_WEEK",
                  "TARGET_VISITS_DAY", "YEAR")
    })
    apply_sheet_order_and_colors(wb)  # re-apply so START_HERE lands first
