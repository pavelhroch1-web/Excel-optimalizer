"""
Field Force Optimizer - Distribution Client (V1 view/export + V2 local
engine execution)

A small standalone desktop app. Two distinct capabilities, kept clearly
separated in the UI because they carry very different risk:

  1. VIEW/EXPORT (read-only, the original V1 scope): reads the already-
     published TECHNICIAN_PLAN sheet and lets the user browse it and
     export a separate Excel file per technician. Never writes to the
     source workbook.

  2. RUN IMPORT/PLANNING/PUBLISH LOCALLY (V2, product-owner-approved
     architecture change - see docs/ARCHITECTURE.md "Desktop Client local
     engine execution"): runs a Python port of ImportEngine.ts/
     PlanningEngine.ts/PublishEngine.ts (desktop_client/engines/) directly
     against the workbook file on disk, via openpyxl - no Microsoft Graph
     API, no online sync, matching the project's hard "no external API"
     constraint. This DOES write to the source workbook (with an automatic
     backup first) and DOES duplicate business logic that used to exist
     only in Office Scripts - a deliberate, documented exception to
     "FieldForceOptimizer is the sole source of truth", mitigated by
     tools/sim/compare_engines.py verifying the Python port against the
     real TypeScript engines on real production data. Excel/Office Scripts
     remain the authoritative implementation; this is a second, tested
     implementation of the same rules, not a replacement.

Usage: python3 distribution_client.py
Requires: openpyxl, ttkbootstrap (pip install openpyxl ttkbootstrap).
ttkbootstrap is a themed skin for Tkinter (still stdlib Tkinter underneath,
no separate GUI framework/runtime) - gives a modern flat look with almost
no extra code versus plain ttk.

All view/export file I/O lives in plan_export.py; all local-engine I/O
lives in xlsx_engine_io.py + engines/ (no GUI dependency, independently
testable) - this file is presentation only.
"""

import os
from datetime import date, datetime
from tkinter import filedialog, messagebox, ttk

import ttkbootstrap as tb
from ttkbootstrap.constants import BOTH, LEFT, RIGHT, X, Y, W

import xlsx_engine_io
from engines.import_engine import run as run_import_engine
from engines.mock_workbook import MockWorkbook
from engines.planning_engine import run as run_planning_engine
from engines.publish_engine import run as run_publish_engine
from plan_export import SHEET_NAME, export_technician_file, read_technician_plan

ENGINE_RUNNERS = {"import": run_import_engine, "planning": run_planning_engine, "publish": run_publish_engine}

# Plain tkinter.ttk.Scrollbar, not tb.Scrollbar: ttkbootstrap renders any
# scrollbar's track/thumb via Pillow (PIL.ImageTk) as soon as a themed
# ttkbootstrap.Window is active - even a plain ttk.Scrollbar gets pulled
# into that, since ttkbootstrap's Style hooks the whole ttk widget system,
# not just its own tb.-prefixed widgets. PyInstaller's PIL hook doesn't
# bundle the PIL._tkinter_finder submodule this needs at runtime, so a
# packaged .exe crashed on startup with ModuleNotFoundError even though it
# ran fine from source - found by actually running the packaged .exe, not
# just the Python source. Real fix is in build_exe.bat
# (--hidden-import PIL._tkinter_finder); kept ttk.Scrollbar here anyway
# since it's one less thing depending on Pillow rendering succeeding.

NAVY = "#1F4E78"  # same brand color as the Excel workbook (tools/ux_style.py's NAVY)
MUTED = "#6c757d"


class DistributionClientApp:
    def __init__(self, root: tb.Window):
        self.root = root
        self.root.title("Field Force Optimizer — Distribution Client")
        self.root.geometry("1040x640")
        self.root.minsize(820, 520)

        self.workbook_path = None
        self.headers: list[str] = []
        self.by_technician: dict[str, list[dict]] = {}
        self.filtered_technicians: list[str] = []

        self._build_layout()

    # ------------------------------------------------------------------
    # Layout
    # ------------------------------------------------------------------

    def _build_layout(self):
        self._build_header()
        self._build_summary_bar()
        self._build_engine_panel()

        body = tb.Frame(self.root, padding=(16, 8, 16, 12))
        body.pack(fill=BOTH, expand=True)
        body.columnconfigure(1, weight=1)
        body.rowconfigure(0, weight=1)

        self._build_technician_panel(body)
        self._build_plan_panel(body)

        self.status_label = tb.Label(
            self.root, text="Otevři workbook a začni.", bootstyle="secondary", padding=(16, 6)
        )
        self.status_label.pack(fill=X, side="bottom")

    def _build_header(self):
        header = tb.Frame(self.root, bootstyle="primary", padding=(20, 16))
        header.pack(fill=X)

        title_col = tb.Frame(header, bootstyle="primary")
        title_col.pack(side=LEFT, fill=X, expand=True)
        tb.Label(
            title_col, text="Field Force Optimizer", font=("", 18, "bold"), bootstyle="inverse-primary"
        ).pack(anchor=W)
        tb.Label(
            title_col, text="Distribution Client — rozpis techniků, jedním klikem",
            font=("", 10), bootstyle="inverse-primary"
        ).pack(anchor=W)

        tb.Button(
            header, text="📂  Otevřít workbook…", bootstyle="light", command=self.on_open_workbook, width=22
        ).pack(side=RIGHT)

    def _build_summary_bar(self):
        self.summary_frame = tb.Frame(self.root, padding=(16, 10))
        self.summary_frame.pack(fill=X)

        self.summary_file_card = self._summary_card(self.summary_frame, "SOUBOR", "Žádný soubor není otevřený")
        self.summary_tech_card = self._summary_card(self.summary_frame, "TECHNICI", "—")
        self.summary_visits_card = self._summary_card(self.summary_frame, "NÁVŠTĚVY CELKEM", "—")

    def _summary_card(self, parent, label, value):
        card = tb.Frame(parent, bootstyle="light", padding=(14, 8))
        card.pack(side=LEFT, fill=X, expand=True, padx=(0, 10))
        tb.Label(card, text=label, font=("", 8, "bold"), bootstyle="secondary").pack(anchor=W)
        value_label = tb.Label(card, text=value, font=("", 14, "bold"), bootstyle="dark")
        value_label.pack(anchor=W)
        card.value_label = value_label
        return card

    def _build_engine_panel(self):
        panel = tb.Frame(self.root, bootstyle="warning", padding=(16, 8))
        panel.pack(fill=X)

        label_col = tb.Frame(panel, bootstyle="warning")
        label_col.pack(side=LEFT, fill=X, expand=True)
        tb.Label(
            label_col, text="Lokální spuštění enginů (zapisuje přímo do souboru)",
            font=("", 9, "bold"), bootstyle="inverse-warning",
        ).pack(anchor=W)
        tb.Label(
            label_col,
            text="Vytvoří se záloha před zápisem. Zavři soubor v Excelu, než spustíš. "
                 "Business logika je ověřená proti Office Scriptům (tools/sim/compare_engines.py), "
                 "ale Excel zůstává oficiálním zdrojem pravdy.",
            font=("", 8), bootstyle="inverse-warning", wraplength=560, justify="left",
        ).pack(anchor=W)

        btn_col = tb.Frame(panel, bootstyle="warning")
        btn_col.pack(side=RIGHT)
        self.import_btn = tb.Button(
            btn_col, text="▶ Import", bootstyle="dark", width=12, state="disabled",
            command=lambda: self._run_local_engine(["import"], {"POS_MASTER"}, "Import Engine"),
        )
        self.import_btn.pack(side=LEFT, padx=3)
        self.planning_btn = tb.Button(
            btn_col, text="▶ Planning", bootstyle="dark", width=12, state="disabled",
            command=lambda: self._run_local_engine(
                ["planning"], {"MANAGER_PLAN", "PLAN_LIFECYCLE"}, "Planning Engine"
            ),
        )
        self.planning_btn.pack(side=LEFT, padx=3)
        self.publish_btn = tb.Button(
            btn_col, text="▶ Publish", bootstyle="dark", width=12, state="disabled",
            command=lambda: self._run_local_engine(
                ["publish"], {"MANAGER_PLAN_PUBLISHED", "PLAN_LIFECYCLE"}, "Publish Engine"
            ),
        )
        self.publish_btn.pack(side=LEFT, padx=3)

    def _build_technician_panel(self, body):
        left = tb.Frame(body, padding=(0, 0, 12, 0))
        left.grid(row=0, column=0, sticky="ns")

        tb.Label(left, text="Technici", font=("", 11, "bold")).pack(anchor=W, pady=(0, 6))

        self.search_var = tb.StringVar()
        search_entry = tb.Entry(left, textvariable=self.search_var, width=26)
        search_entry.pack(fill=X, pady=(0, 8))
        self._set_placeholder(search_entry, "🔍  Hledat technika…")

        list_frame = tb.Frame(left)
        list_frame.pack(fill=BOTH, expand=True)
        self.tech_listbox = tb.Treeview(
            list_frame, columns=("count",), show="tree", height=22, bootstyle="primary", selectmode="browse"
        )
        self.tech_listbox.column("#0", width=210)
        self.tech_listbox.pack(side=LEFT, fill=BOTH, expand=True)
        scroll = ttk.Scrollbar(list_frame, orient="vertical", command=self.tech_listbox.yview)
        scroll.pack(side=RIGHT, fill=Y)
        self.tech_listbox.configure(yscrollcommand=scroll.set)
        self.tech_listbox.bind("<<TreeviewSelect>>", self.on_select_technician)

        # Wired up last, once self.tech_listbox exists - the trace callback
        # touches it, and StringVar.trace_add can fire as soon as it's
        # registered (e.g. from the placeholder text being inserted above).
        self.search_var.trace_add("write", lambda *_: self._apply_search_filter())

        actions = tb.Frame(left, padding=(0, 10, 0, 0))
        actions.pack(fill=X)
        self.export_all_btn = tb.Button(
            actions, text="⇩  Exportovat všechny", bootstyle="secondary-outline",
            command=self.on_export_all, state="disabled",
        )
        self.export_all_btn.pack(fill=X)

    def _build_plan_panel(self, body):
        right = tb.Frame(body)
        right.grid(row=0, column=1, sticky="nsew")
        right.rowconfigure(1, weight=1)
        right.columnconfigure(0, weight=1)

        plan_header = tb.Frame(right)
        plan_header.grid(row=0, column=0, sticky="ew", pady=(0, 8))
        self.plan_label = tb.Label(plan_header, text="Vyber technika vlevo.", font=("", 12, "bold"))
        self.plan_label.pack(side=LEFT)
        self.export_selected_btn = tb.Button(
            plan_header, text="⇩  Exportovat tento rozpis…", bootstyle="success",
            command=self.on_export_selected, state="disabled",
        )
        self.export_selected_btn.pack(side=RIGHT)

        tree_frame = tb.Frame(right)
        tree_frame.grid(row=1, column=0, sticky="nsew")
        self.tree = tb.Treeview(tree_frame, show="headings", bootstyle="primary")
        self.tree.pack(side=LEFT, fill=BOTH, expand=True)
        tree_scroll = ttk.Scrollbar(tree_frame, orient="vertical", command=self.tree.yview)
        tree_scroll.pack(side=RIGHT, fill=Y)
        self.tree.configure(yscrollcommand=tree_scroll.set)

    @staticmethod
    def _set_placeholder(entry, text):
        entry.insert(0, text)
        entry.configure(bootstyle="secondary")

        def on_focus_in(_e):
            if entry.get() == text:
                entry.delete(0, "end")
                entry.configure(bootstyle="default")

        def on_focus_out(_e):
            if not entry.get():
                entry.insert(0, text)
                entry.configure(bootstyle="secondary")

        entry.bind("<FocusIn>", on_focus_in)
        entry.bind("<FocusOut>", on_focus_out)
        entry._placeholder_text = text

    # ------------------------------------------------------------------
    # Actions
    # ------------------------------------------------------------------

    def on_open_workbook(self):
        path = filedialog.askopenfilename(
            title="Vyber workbook Field Force Optimizer",
            filetypes=[("Excel workbook", "*.xlsx")],
        )
        if not path:
            return
        try:
            headers, by_technician = read_technician_plan(path)
        except Exception as e:
            messagebox.showerror("Nepodařilo se otevřít soubor", str(e))
            return

        self.workbook_path = path
        self.headers = headers
        self.by_technician = by_technician

        self.summary_file_card.value_label.configure(text=os.path.basename(path))
        total_visits = sum(len(rows) for rows in by_technician.values())
        self.summary_tech_card.value_label.configure(text=str(len(by_technician)))
        self.summary_visits_card.value_label.configure(text=str(total_visits))

        self.search_var.set("")
        self._apply_search_filter()

        self.export_all_btn.config(state=("normal" if self.by_technician else "disabled"))
        self.export_selected_btn.config(state="disabled")
        self.import_btn.config(state="normal")
        self.planning_btn.config(state="normal")
        self.publish_btn.config(state="normal")
        self.plan_label.config(text="Vyber technika vlevo.")
        self._clear_tree()

        if not self.by_technician:
            self.set_status(
                f"List {SHEET_NAME} je otevřený, ale nemá žádná data — zkontroluj, že plán byl publikován "
                "a soubor byl uložen z Excelu (kešované hodnoty vzorců).",
                bootstyle="warning",
            )
        else:
            self.set_status(f"Načteno {len(self.by_technician)} techniků z {SHEET_NAME}.", bootstyle="secondary")

    def _apply_search_filter(self):
        query = self.search_var.get().strip().lower()
        if query and not query.startswith("🔍"):
            names = [t for t in sorted(self.by_technician.keys()) if query in t.lower()]
        else:
            names = sorted(self.by_technician.keys())
        self.filtered_technicians = names

        self.tech_listbox.delete(*self.tech_listbox.get_children())
        for tech in names:
            count = len(self.by_technician.get(tech, []))
            self.tech_listbox.insert("", "end", iid=tech, text=f"{tech}  ({count})")

    def on_select_technician(self, _event=None):
        selection = self.tech_listbox.selection()
        if not selection:
            return
        tech = selection[0]
        rows = self.by_technician.get(tech, [])
        self.plan_label.config(text=f"{tech}  ·  {len(rows)} návštěv")
        self._fill_tree(rows)
        self.export_selected_btn.config(state="normal")

    def on_export_selected(self):
        selection = self.tech_listbox.selection()
        if not selection:
            return
        tech = selection[0]
        output_dir = filedialog.askdirectory(title="Vyber cílovou složku")
        if not output_dir:
            return
        try:
            path = export_technician_file(self.headers, tech, self.by_technician[tech], output_dir)
        except Exception as e:
            messagebox.showerror("Export selhal", str(e))
            return
        self.set_status(f"Uloženo: {path}", bootstyle="success")
        messagebox.showinfo("Hotovo", f"Rozpis pro {tech} byl uložen jako:\n{os.path.basename(path)}")

    def on_export_all(self):
        if not self.by_technician:
            return
        output_dir = filedialog.askdirectory(title="Vyber cílovou složku")
        if not output_dir:
            return
        written = []
        errors = []
        for tech, rows in self.by_technician.items():
            try:
                written.append(export_technician_file(self.headers, tech, rows, output_dir))
            except Exception as e:
                errors.append(f"{tech}: {e}")
        self.set_status(f"Exportováno {len(written)} souborů do {output_dir}.", bootstyle="success")
        if errors:
            messagebox.showwarning(
                "Export dokončen s chybami",
                f"Uloženo {len(written)} souborů.\nChyby:\n" + "\n".join(errors),
            )
        else:
            messagebox.showinfo("Hotovo", f"Exportováno {len(written)} souborů do:\n{output_dir}")

    def _run_local_engine(self, engine_names: list[str], output_sheets: set, label: str):
        if not self.workbook_path:
            return
        confirmed = messagebox.askyesno(
            f"Spustit {label}?",
            f"Tato akce PŘEPÍŠE data v otevřeném souboru přímo na disku, mimo Excel:\n"
            f"{os.path.basename(self.workbook_path)}\n\n"
            "Před zápisem se automaticky vytvoří záložní kopie "
            "(stejná složka, přípona .backup_RRRRMMDD_HHMMSS.xlsx).\n\n"
            "DŮLEŽITÉ: pokud je soubor otevřený v Excelu, zavři ho teď - jinak "
            "můžeš při příštím uložení v Excelu přepsat tento zápis, nebo naopak.\n\n"
            "Business logika běží ve zvláštní Python implementaci, ověřené proti "
            "skutečným Office Scripts na reálných datech, ale Excel/Office Scripts "
            "zůstávají oficiálním zdrojem pravdy.\n\n"
            "Pokračovat?",
            icon="warning",
        )
        if not confirmed:
            return
        try:
            backup_path = xlsx_engine_io.backup_workbook(self.workbook_path)
            state = xlsx_engine_io.read_state(self.workbook_path)
            workbook = MockWorkbook(state)
            log_lines = [ENGINE_RUNNERS[name](workbook) for name in engine_names]
            xlsx_engine_io.write_state(self.workbook_path, workbook.dump(), output_sheets)
        except Exception as e:
            messagebox.showerror(f"{label} selhalo", str(e))
            return
        message = "\n".join(log_lines)
        self.set_status(" | ".join(log_lines), bootstyle="success")
        messagebox.showinfo(
            f"{label} dokončeno",
            f"{message}\n\nZáloha původního souboru: {os.path.basename(backup_path)}\n\n"
            "Pozn.: listy jako TECHNICIAN_PLAN mají živé vzorce a přepočtou se, "
            "až soubor otevřeš a uložíš v Excelu - tady zůstávají neaktuální.",
        )

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    def _clear_tree(self):
        self.tree.delete(*self.tree.get_children())
        self.tree["columns"] = ()

    def _fill_tree(self, rows):
        self._clear_tree()
        self.tree["columns"] = self.headers
        for h in self.headers:
            self.tree.heading(h, text=h)
            self.tree.column(h, width=120, anchor=W)
        for i, row in enumerate(rows):
            values = [self._format_cell(row.get(h, "")) for h in self.headers]
            tag = "odd" if i % 2 else "even"
            self.tree.insert("", "end", values=values, tags=(tag,))
        self.tree.tag_configure("even", background="#f4f6f9")
        self.tree.tag_configure("odd", background="#ffffff")

    @staticmethod
    def _format_cell(value):
        if isinstance(value, (datetime, date)):
            return value.strftime("%d.%m.%Y")
        return "" if value is None else value

    def set_status(self, text: str, bootstyle: str = "secondary"):
        self.status_label.config(text=text, bootstyle=bootstyle)


def main():
    root = tb.Window(themename="flatly")
    DistributionClientApp(root)
    root.mainloop()


if __name__ == "__main__":
    main()
