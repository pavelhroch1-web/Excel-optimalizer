"""
Field Force Optimizer - Tour Plán Generátor (desktop, .exe)

Jedno okno: (nepovinně) nahraj čerstvé SalesApp / POS exporty, zadej
počáteční týden / horizont / návštěvy a klikni Generovat. Vytvoří Excel
s 5týdenním (nebo N-týdenním) plánem: první týden = Dojezd (nejzanedbanější
POS), zbytek = Kampaň, plus manažerský override (Štolba za Dvořáka).

Běží LOKÁLNĚ - žádný cloud, žádné API, žádný OOM. Používá naprosto stejný
Planning Engine jako web (desktop_client/engines/), jen výpočet běží na tomto
PC. Data se berou z posledního snapshotu zabaleného v aplikaci; když nahraješ
nové exporty, plán se počítá z nich.

Spuštění ze zdroje:  python3 tourplan_app.py
Build do .exe:        build_tourplan_exe.bat  (na Windows PC s Pythonem)
"""

import os
import sys
import threading
import traceback
from datetime import date

# --- import paths: dev (repo) i frozen (.exe z PyInstalleru) ---------------
if getattr(sys, "frozen", False):
    BASE_DIR = sys._MEIPASS  # noqa: SLF001 - PyInstaller rozbalí sem
else:
    BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
for p in (BASE_DIR, os.path.join(BASE_DIR, "backend"),
          os.path.join(BASE_DIR, "tools"), os.path.join(BASE_DIR, "desktop_client")):
    if p not in sys.path:
        sys.path.insert(0, p)

SEED_WORKBOOK = os.path.join(BASE_DIR, "workbook", "FieldForceOptimizer_V11_scaffold.xlsx")

import tkinter as tk  # noqa: E402
from tkinter import filedialog  # noqa: E402

import ttkbootstrap as tb  # noqa: E402
from ttkbootstrap.constants import BOTH, LEFT, X, W, SUCCESS, SECONDARY, INFO  # noqa: E402

import pipeline  # noqa: E402  (backend/pipeline.py; jen openpyxl + stdlib)
from export_tourplan import build_base, build_plan, write_excel, FORCE_ASSIGN, CAP_PER_TECH_WEEK  # noqa: E402


class App:
    def __init__(self, root):
        self.root = root
        self.salesapp_paths = []
        self.pos_path = None
        root.title("Field Force Optimizer – Tour Plán Generátor")
        root.geometry("720x620")

        head = tb.Frame(root, padding=(20, 16))
        head.pack(fill=X)
        tb.Label(head, text="Tour Plán Generátor", font=("Segoe UI", 18, "bold")).pack(anchor=W)
        tb.Label(head, bootstyle=SECONDARY,
                 text="Plán běží lokálně na tomto PC – žádný server, žádné čekání. "
                      "Engine je stejný jako na webu.").pack(anchor=W)

        body = tb.Frame(root, padding=20)
        body.pack(fill=BOTH, expand=True)

        # --- 1) data (nepovinné) ---
        f1 = tb.Labelframe(body, text="1 · Data (nepovinné)", padding=14)
        f1.pack(fill=X, pady=(0, 12))
        tb.Label(f1, bootstyle=SECONDARY,
                 text="Když nic nenahraješ, použije se poslední snapshot v aplikaci. "
                      "Nahráním čerstvých SalesApp exportů se plán spočítá z aktuálních dat.").pack(anchor=W, pady=(0, 8))
        r1 = tb.Frame(f1); r1.pack(fill=X, pady=2)
        tb.Button(r1, text="Vybrat SalesApp exporty…", bootstyle=INFO,
                  command=self.pick_salesapp).pack(side=LEFT)
        self.lbl_salesapp = tb.Label(r1, text="žádné", bootstyle=SECONDARY)
        self.lbl_salesapp.pack(side=LEFT, padx=10)
        r2 = tb.Frame(f1); r2.pack(fill=X, pady=2)
        tb.Button(r2, text="Vybrat POS export… (nepovinné)", bootstyle=SECONDARY,
                  command=self.pick_pos).pack(side=LEFT)
        self.lbl_pos = tb.Label(r2, text="žádný", bootstyle=SECONDARY)
        self.lbl_pos.pack(side=LEFT, padx=10)

        # --- 2) parametry ---
        f2 = tb.Labelframe(body, text="2 · Parametry plánu", padding=14)
        f2.pack(fill=X, pady=(0, 12))
        grid = tb.Frame(f2); grid.pack(fill=X)
        cur_week = date.today().isocalendar()[1]
        self.var_week = tk.IntVar(value=max(cur_week, 29))
        self.var_len = tk.IntVar(value=5)
        self.var_visits = tk.IntVar(value=CAP_PER_TECH_WEEK)
        self.var_override = tk.BooleanVar(value=True)
        self._spin(grid, "Počáteční týden", self.var_week, 1, 53, 0)
        self._spin(grid, "Horizont (týdnů)", self.var_len, 1, 12, 1)
        self._spin(grid, "Návštěv/technik/týden", self.var_visits, 1, 80, 2)
        tb.Checkbutton(f2, text="Použít manažerský override (Štolba za Dvořáka, 25 POS v týdnu 30)",
                       variable=self.var_override, bootstyle=SUCCESS).pack(anchor=W, pady=(10, 0))

        # --- 3) generovat ---
        self.btn = tb.Button(body, text="Generovat a uložit Excel", bootstyle=SUCCESS,
                             command=self.on_generate)
        self.btn.pack(fill=X, pady=(4, 12), ipady=6)

        self.log = tk.Text(body, height=12, wrap="word", state="disabled",
                           font=("Consolas", 9))
        self.log.pack(fill=BOTH, expand=True)
        self._log(f"Snapshot: {'nalezen' if os.path.exists(SEED_WORKBOOK) else 'CHYBÍ – ' + SEED_WORKBOOK}")

    def _spin(self, parent, label, var, lo, hi, col):
        cell = tb.Frame(parent); cell.grid(row=0, column=col, padx=(0, 18), sticky=W)
        tb.Label(cell, text=label, bootstyle=SECONDARY).pack(anchor=W)
        tb.Spinbox(cell, from_=lo, to=hi, textvariable=var, width=10).pack(anchor=W)

    def pick_salesapp(self):
        paths = filedialog.askopenfilenames(
            title="SalesApp exporty (můžeš vybrat víc)",
            filetypes=[("Excel", "*.xlsx"), ("Vše", "*.*")])
        if paths:
            self.salesapp_paths = list(paths)
            self.lbl_salesapp.config(text=f"{len(paths)} soubor(ů)")

    def pick_pos(self):
        path = filedialog.askopenfilename(
            title="POS export", filetypes=[("Excel", "*.xlsx"), ("Vše", "*.*")])
        if path:
            self.pos_path = path
            self.lbl_pos.config(text=os.path.basename(path))

    def _log(self, msg):
        self.log.config(state="normal")
        self.log.insert("end", msg + "\n")
        self.log.see("end")
        self.log.config(state="disabled")
        self.root.update_idletasks()

    def on_generate(self):
        out = filedialog.asksaveasfilename(
            title="Uložit tour plán jako…", defaultextension=".xlsx",
            initialfile=f"TOUR_PLAN_tyden_{self.var_week.get()}.xlsx",
            filetypes=[("Excel", "*.xlsx")])
        if not out:
            return
        self.btn.config(state="disabled")
        threading.Thread(target=self._run, args=(out,), daemon=True).start()

    def _run(self, out):
        try:
            week = int(self.var_week.get())
            length = int(self.var_len.get())
            visits = int(self.var_visits.get())
            forced = FORCE_ASSIGN if self.var_override.get() else []

            if not os.path.exists(SEED_WORKBOOK):
                self._log("CHYBA: chybí zabalený snapshot workbooku.")
                return

            if self.salesapp_paths:
                self._log(f"Načítám {len(self.salesapp_paths)} SalesApp export(ů)…")
                sa = [pipeline.read_export_rows(p) for p in self.salesapp_paths]
                raw = pipeline.read_export_rows(self.pos_path) if self.pos_path else None
                self._log("Skládám stav (Import + Compliance) z čerstvých dat…")
                base = pipeline.build_upload_draft(raw, sa, seed_workbook=SEED_WORKBOOK)["state"]
            else:
                self._log("Používám poslední snapshot (bez nových dat)…")
                base = build_base(SEED_WORKBOOK)

            self._log(f"Generuji plán: týden {week}, horizont {length}, {visits} návštěv/technik…")
            rows, idx, base = build_plan(base, week, length, visits, forced)
            self._log(f"Naplánováno {len(rows)} návštěv. Zapisuji Excel…")
            write_excel(rows, idx, base, out, week, length)
            size = round(os.path.getsize(out) / 1e6, 2)
            self._log(f"HOTOVO ✓  Uloženo: {out}  ({size} MB)")
        except Exception as e:
            self._log("CHYBA: " + str(e))
            self._log(traceback.format_exc())
        finally:
            self.btn.config(state="normal")


def main():
    root = tb.Window(themename="flatly")
    App(root)
    root.mainloop()


if __name__ == "__main__":
    main()
