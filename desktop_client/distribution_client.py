"""
Field Force Optimizer - Distribution Client (V1)

A small standalone desktop app - NOT part of the planning architecture.
FieldForceOptimizer (the Excel workbook + Office Scripts) remains the sole
source of truth for all planning business logic: cadence, scoring,
compliance, capacity, publication. This app never plans, never optimizes,
never reads SALESAPP_IMPORT or any import-stage sheet, and never writes
anything back to the source workbook. It only reads the already-published
TECHNICIAN_PLAN sheet and gives the user a faster, more pleasant way to
browse it and export a separate Excel file per technician.

Usage: python3 distribution_client.py
Requires: openpyxl (already a dependency of tools/ in this repo).
Tkinter ships with standard Python on Windows/macOS installers; on Linux
it may need a separate OS package (e.g. `apt install python3-tk`) - no
extra GUI framework dependency beyond that.

Workflow this supports (per docs/BACKLOG.md "Distribution Client desktop
app"):
  1. Open a workbook - local file, or a file inside a OneDrive-synced
     folder (from this app's point of view that is just a local file path;
     there is no OneDrive API involved, no live connection, no sync).
  2. Browse the list of technicians and see the selected technician's
     weekly plan on screen.
  3. One click: export a separate .xlsx per technician, named
     "<Technik>_<Rok>_W<Tyden>.xlsx", into a folder the user picks.

All file-reading/writing logic lives in plan_export.py (no GUI dependency,
independently unit-testable) - this file is presentation only.
"""

import os
import tkinter as tk
from datetime import date, datetime
from tkinter import ttk, filedialog, messagebox

from plan_export import SHEET_NAME, export_technician_file, read_technician_plan


class DistributionClientApp:
    def __init__(self, root: tk.Tk):
        self.root = root
        self.root.title("Field Force Optimizer - Distribution Client")
        self.root.geometry("900x560")

        self.workbook_path = None
        self.headers = []
        self.by_technician = {}

        self._build_layout()

    # ------------------------------------------------------------------
    # Layout
    # ------------------------------------------------------------------

    def _build_layout(self):
        top = ttk.Frame(self.root, padding=10)
        top.pack(fill="x")

        ttk.Button(top, text="Otevřít workbook...", command=self.on_open_workbook).pack(side="left")
        self.workbook_label = ttk.Label(top, text="Žádný soubor není otevřený.", foreground="#595959")
        self.workbook_label.pack(side="left", padx=10)

        body = ttk.Frame(self.root, padding=(10, 0, 10, 10))
        body.pack(fill="both", expand=True)

        # Left: technician list
        left = ttk.Frame(body)
        left.pack(side="left", fill="y")
        ttk.Label(left, text="Technici", font=("", 10, "bold")).pack(anchor="w")
        self.tech_listbox = tk.Listbox(left, width=28, height=24, exportselection=False)
        self.tech_listbox.pack(fill="y", expand=True, pady=(4, 0))
        self.tech_listbox.bind("<<ListboxSelect>>", self.on_select_technician)

        # Right: selected technician's plan + export buttons
        right = ttk.Frame(body)
        right.pack(side="left", fill="both", expand=True, padx=(10, 0))

        self.plan_label = ttk.Label(right, text="Vyber technika vlevo.", font=("", 10, "bold"))
        self.plan_label.pack(anchor="w")

        self.tree = ttk.Treeview(right, show="headings")
        self.tree.pack(fill="both", expand=True, pady=(4, 8))

        actions = ttk.Frame(right)
        actions.pack(fill="x")
        self.export_selected_btn = ttk.Button(
            actions, text="Exportovat vybraného technika...", command=self.on_export_selected, state="disabled"
        )
        self.export_selected_btn.pack(side="left")
        self.export_all_btn = ttk.Button(
            actions, text="Exportovat všechny techniky...", command=self.on_export_all, state="disabled"
        )
        self.export_all_btn.pack(side="left", padx=(8, 0))

        self.status_label = ttk.Label(self.root, text="", foreground="#595959", padding=(10, 4))
        self.status_label.pack(fill="x", side="bottom")

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
        self.workbook_label.config(text=os.path.basename(path))

        self.tech_listbox.delete(0, "end")
        for tech in sorted(self.by_technician.keys()):
            self.tech_listbox.insert("end", tech)

        self.export_all_btn.config(state=("normal" if self.by_technician else "disabled"))
        self.export_selected_btn.config(state="disabled")
        self.plan_label.config(text="Vyber technika vlevo.")
        self._clear_tree()

        if not self.by_technician:
            self.set_status(
                f"List {SHEET_NAME} je otevřený, ale nemá žádná data - zkontroluj, že plán byl publikován "
                "a soubor byl uložen z Excelu (kešované hodnoty vzorců)."
            )
        else:
            self.set_status(f"Načteno {len(self.by_technician)} techniků z {SHEET_NAME}.")

    def on_select_technician(self, _event=None):
        selection = self.tech_listbox.curselection()
        if not selection:
            return
        tech = self.tech_listbox.get(selection[0])
        rows = self.by_technician.get(tech, [])
        self.plan_label.config(text=f"{tech} - {len(rows)} návštěv")
        self._fill_tree(rows)
        self.export_selected_btn.config(state="normal")

    def on_export_selected(self):
        selection = self.tech_listbox.curselection()
        if not selection:
            return
        tech = self.tech_listbox.get(selection[0])
        output_dir = filedialog.askdirectory(title="Vyber cílovou složku")
        if not output_dir:
            return
        try:
            path = export_technician_file(self.headers, tech, self.by_technician[tech], output_dir)
        except Exception as e:
            messagebox.showerror("Export selhal", str(e))
            return
        self.set_status(f"Uloženo: {path}")
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
        self.set_status(f"Exportováno {len(written)} souborů do {output_dir}.")
        if errors:
            messagebox.showwarning(
                "Export dokončen s chybami",
                f"Uloženo {len(written)} souborů.\nChyby:\n" + "\n".join(errors),
            )
        else:
            messagebox.showinfo("Hotovo", f"Exportováno {len(written)} souborů do:\n{output_dir}")

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
            self.tree.column(h, width=110, anchor="w")
        for row in rows:
            values = [self._format_cell(row.get(h, "")) for h in self.headers]
            self.tree.insert("", "end", values=values)

    @staticmethod
    def _format_cell(value):
        if isinstance(value, (datetime, date)):
            return value.strftime("%d.%m.%Y")
        return "" if value is None else value

    def set_status(self, text: str):
        self.status_label.config(text=text)


def main():
    root = tk.Tk()
    try:
        style = ttk.Style()
        if "clam" in style.theme_names():
            style.theme_use("clam")
    except Exception:
        pass
    DistributionClientApp(root)
    root.mainloop()


if __name__ == "__main__":
    main()
