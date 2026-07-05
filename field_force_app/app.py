"""
Field Force Optimizer — jednoduchá appka (nová, samostatná od
desktop_client/'s Distribution Client - viz field_force_app/README.md).

Mentální model, přesně 3 kroky, ne 8:
  1. POS/PPT report -> Zpracovat  (Import + Planning -> Draft tour plan)
  2. Publikovat & sledovat        (Publish + Start Tracking - manažerská
     kontrola, schválně oddělené tlačítko - viz pipeline.py's docstring)
  3. SalesApp report -> Zpracovat (Compliance + Advisor + Performance +
     Reporting -> aktualizované reporty)

Kdykoliv pak "📄 Otevřít report" vygeneruje a otevře jeden HTML soubor se
vším (tour plan, KPI přehled, kdo fláká, dlouhodobý trend) - appka
nevyžaduje, abys cokoliv otevíral v Excelu.

Business logika sama běží v desktop_client/engines/ (stejný, už otestovaný
kód jako Distribution Client V2) - tahle appka nic nepočítá jinak, jen jinak
sbírá vstupy (přímo ze souborů, ne kopírováním do listů) a jinak zobrazuje
výstupy (vlastní HTML report, ne otevírání Excelu).

Requires: openpyxl, ttkbootstrap (pip install openpyxl ttkbootstrap).
"""
from __future__ import annotations

import json
import os
import sys
import webbrowser
from pathlib import Path
from tkinter import filedialog, messagebox

import ttkbootstrap as tb
from ttkbootstrap.constants import BOTH, LEFT, RIGHT, X, W

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from field_force_app.pipeline import (  # noqa: E402
    run_evaluation_stage,
    run_planning_stage,
    run_publish_stage,
)
from field_force_app.report_import import ReportParseError, parse_pos_report, parse_salesapp_report, write_sheet_rows  # noqa: E402
from field_force_app.report_view import generate_html_report  # noqa: E402

NAVY = "#1F4E78"
CONFIG_PATH = Path.home() / ".field_force_app.json"


def _load_last_workbook() -> str | None:
    try:
        data = json.loads(CONFIG_PATH.read_text(encoding="utf-8"))
        path = data.get("workbook_path")
        return path if path and os.path.exists(path) else None
    except (OSError, json.JSONDecodeError):
        return None


def _save_last_workbook(path: str) -> None:
    try:
        CONFIG_PATH.write_text(json.dumps({"workbook_path": path}), encoding="utf-8")
    except OSError:
        pass  # not remembering the path across launches is not fatal


class FieldForceApp:
    def __init__(self, root: tb.Window):
        self.root = root
        self.root.title("Field Force Optimizer")
        self.root.geometry("760x640")
        self.root.minsize(640, 520)

        self.workbook_path: str | None = _load_last_workbook()

        self._build_layout()
        self._refresh_workbook_label()

    # ------------------------------------------------------------------
    def _build_layout(self):
        header = tb.Frame(self.root, bootstyle="primary", padding=(20, 16))
        header.pack(fill=X)
        tb.Label(
            header, text="Field Force Optimizer", font=("", 18, "bold"), bootstyle="inverse-primary"
        ).pack(anchor=W)
        tb.Label(
            header, text="Dva reporty dovnitř, tour plan a přehledy ven.",
            font=("", 10), bootstyle="inverse-primary",
        ).pack(anchor=W)

        wb_row = tb.Frame(self.root, padding=(20, 12, 20, 4))
        wb_row.pack(fill=X)
        tb.Label(wb_row, text="Workbook:", font=("", 9, "bold")).pack(side=LEFT)
        self.workbook_label = tb.Label(wb_row, text="—", bootstyle="secondary")
        self.workbook_label.pack(side=LEFT, padx=(8, 0))
        tb.Button(
            wb_row, text="📂 Vybrat workbook…", bootstyle="secondary-outline",
            command=self.on_pick_workbook,
        ).pack(side=RIGHT)

        body = tb.Frame(self.root, padding=(20, 8, 20, 8))
        body.pack(fill=BOTH, expand=True)

        self._build_step(
            body, "1", "POS/PPT report",
            "Vlož soubor s exportem POS/PPT → vytvoří/aktualizuje Draft tour plan.",
            picker_attr="pos_report_path", process_cmd=self.on_process_pos_report,
        )
        self._build_publish_step(body)
        self._build_step(
            body, "3", "SalesApp report",
            "Vlož export skutečných návštěv ze SalesApp → aktualizuje vyhodnocení a přehledy.",
            picker_attr="salesapp_report_path", process_cmd=self.on_process_salesapp_report,
        )

        report_row = tb.Frame(self.root, padding=(20, 4, 20, 12))
        report_row.pack(fill=X)
        tb.Button(
            report_row, text="📄  Otevřít report", bootstyle="success", width=24,
            command=self.on_open_report,
        ).pack(side=LEFT)

        log_frame = tb.Frame(self.root, padding=(20, 0, 20, 12))
        log_frame.pack(fill=BOTH, expand=True)
        tb.Label(log_frame, text="Průběh:", font=("", 9, "bold")).pack(anchor=W)
        self.log_text = tb.Text(log_frame, height=8, state="disabled", wrap="word")
        self.log_text.pack(fill=BOTH, expand=True)

    def _build_step(self, parent, number, title, description, picker_attr, process_cmd):
        card = tb.Frame(parent, bootstyle="light", padding=(14, 10))
        card.pack(fill=X, pady=(0, 8))
        setattr(self, picker_attr, None)

        top = tb.Frame(card, bootstyle="light")
        top.pack(fill=X)
        tb.Label(top, text=f"{number}) {title}", font=("", 11, "bold"), bootstyle="dark").pack(side=LEFT)

        tb.Label(card, text=description, font=("", 8), bootstyle="secondary", wraplength=640, justify="left").pack(
            anchor=W, pady=(2, 6)
        )

        row = tb.Frame(card, bootstyle="light")
        row.pack(fill=X)
        path_label = tb.Label(row, text="Soubor nevybrán", bootstyle="secondary")
        path_label.pack(side=LEFT, fill=X, expand=True)
        setattr(self, f"{picker_attr}_label", path_label)

        tb.Button(
            row, text="Vybrat soubor…", bootstyle="secondary-outline",
            command=lambda: self._pick_report_file(picker_attr),
        ).pack(side=RIGHT, padx=(6, 0))
        process_btn = tb.Button(row, text="▶ Zpracovat", bootstyle="dark", command=process_cmd)
        process_btn.pack(side=RIGHT)
        setattr(self, f"{picker_attr}_process_btn", process_btn)

    def _build_publish_step(self, parent):
        card = tb.Frame(parent, bootstyle="light", padding=(14, 10))
        card.pack(fill=X, pady=(0, 8))
        tb.Label(card, text="2) Publikovat & sledovat", font=("", 11, "bold"), bootstyle="dark").pack(anchor=W)
        tb.Label(
            card,
            text="Až bude Draft tour plan hotový k odeslání technikům - zveřejní nejbližší týden "
                 "a začne ho sledovat v přehledech. Schválně samostatný krok, ať máš šanci plán "
                 "před odesláním zkontrolovat.",
            font=("", 8), bootstyle="secondary", wraplength=640, justify="left",
        ).pack(anchor=W, pady=(2, 6))
        tb.Button(
            card, text="▶ Publikovat & sledovat", bootstyle="dark",
            command=self.on_publish,
        ).pack(anchor=W)

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------
    def _pick_report_file(self, picker_attr: str):
        path = filedialog.askopenfilename(
            title="Vyber soubor reportu", filetypes=[("Excel", "*.xlsx"), ("Všechny soubory", "*.*")],
        )
        if not path:
            return
        setattr(self, picker_attr, path)
        getattr(self, f"{picker_attr}_label").configure(text=os.path.basename(path))

    def _refresh_workbook_label(self):
        if self.workbook_path:
            self.workbook_label.configure(text=os.path.basename(self.workbook_path))
        else:
            self.workbook_label.configure(text="— nevybráno, klikni Vybrat workbook")

    def _log(self, lines: list[str]):
        self.log_text.configure(state="normal")
        for line in lines:
            self.log_text.insert("end", line + "\n")
        self.log_text.see("end")
        self.log_text.configure(state="disabled")

    def _require_workbook(self) -> bool:
        if self.workbook_path:
            return True
        messagebox.showwarning("Workbook nevybrán", "Nejdřív vyber workbook (.xlsx) tlačítkem nahoře.")
        return False

    # ------------------------------------------------------------------
    # Actions
    # ------------------------------------------------------------------
    def on_pick_workbook(self):
        path = filedialog.askopenfilename(
            title="Vyber workbook Field Force Optimizer", filetypes=[("Excel workbook", "*.xlsx")],
        )
        if not path:
            return
        self.workbook_path = path
        _save_last_workbook(path)
        self._refresh_workbook_label()
        self._log([f"Workbook nastaven: {path}"])

    def on_process_pos_report(self):
        if not self._require_workbook():
            return
        if not self.pos_report_path:
            messagebox.showwarning("Soubor nevybrán", "Nejdřív vyber soubor reportu POS/PPT.")
            return
        try:
            rows = parse_pos_report(self.pos_report_path)
            write_sheet_rows(self.workbook_path, "RAW_DATA", rows)
            log = run_planning_stage(self.workbook_path)
        except ReportParseError as e:
            messagebox.showerror("Report se nepodařilo přečíst", str(e))
            return
        except Exception as e:
            messagebox.showerror("Zpracování selhalo", str(e))
            return
        self._log(log)
        messagebox.showinfo("Hotovo", "POS/PPT report zpracován, Draft tour plan aktualizován.\n\n" + "\n".join(log))

    def on_publish(self):
        if not self._require_workbook():
            return
        try:
            log = run_publish_stage(self.workbook_path)
        except Exception as e:
            messagebox.showerror("Publikování selhalo", str(e))
            return
        self._log(log)
        messagebox.showinfo("Hotovo", "\n".join(log))

    def on_process_salesapp_report(self):
        if not self._require_workbook():
            return
        if not self.salesapp_report_path:
            messagebox.showwarning("Soubor nevybrán", "Nejdřív vyber soubor reportu ze SalesApp.")
            return
        try:
            rows = parse_salesapp_report(self.salesapp_report_path)
            write_sheet_rows(self.workbook_path, "SALESAPP_IMPORT", rows)
            log = run_evaluation_stage(self.workbook_path)
        except ReportParseError as e:
            messagebox.showerror("Report se nepodařilo přečíst", str(e))
            return
        except Exception as e:
            messagebox.showerror("Zpracování selhalo", str(e))
            return
        self._log(log)
        messagebox.showinfo("Hotovo", "SalesApp report zpracován, přehledy aktualizovány.\n\n" + "\n".join(log))

    def on_open_report(self):
        if not self._require_workbook():
            return
        out_path = str(Path(self.workbook_path).with_name("field_force_report.html"))
        try:
            generate_html_report(self.workbook_path, out_path)
        except Exception as e:
            messagebox.showerror("Report se nepodařilo vygenerovat", str(e))
            return
        self._log([f"Report vygenerován: {out_path}"])
        webbrowser.open(f"file://{out_path}")


def main():
    root = tb.Window(themename="flatly")
    FieldForceApp(root)
    root.mainloop()


if __name__ == "__main__":
    main()
