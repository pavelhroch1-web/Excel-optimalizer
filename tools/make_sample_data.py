"""Generate a small, self-contained SAMPLE dataset for testing the app end to end
without real data. Writes three Excel files into sample_data/:

  1. POS_master.xlsx     - ~40 synthetic POS (real column names, Czech GPS)
  2. SalesApp_export.xlsx - visits for those POS over the last ~6 weeks
  3. Bulk_vouchers.xlsx   - POS + počet, for the bulk Task Engine upload

The files are internally consistent (SalesApp Store UID == POS terminalId,
Executor == the POS's technician), so importing all three exercises segments,
coverage, duration, capacity, clustering, Task Engine and the maps.

Run:  python3 tools/make_sample_data.py
"""
from __future__ import annotations

import datetime
import os
import random

import openpyxl

random.seed(42)
OUT = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "sample_data")
os.makedirs(OUT, exist_ok=True)

TECHS = ["Jan Novák", "Petr Svoboda", "Eva Dvořáková", "Tomáš Král"]
CITIES = {"Praha": (50.083, 14.42), "Brno": (49.195, 16.608), "Ostrava": (49.835, 18.292)}
TTYPES = ["VELKY TERMINAL", "SMALL TERMINAL", "LI"]
CATS = ["1GECO", "4OSTATNI", "1POSTA", "1ORLEN"]
MARKETS = ["IDT", "PETROL", "ČESKÁ POŠTA", "KA PARTNERS"]
CLS = ["A", "B", "P"]

pos_rows = []
for i in range(40):
    city = random.choice(list(CITIES))
    lat0, lon0 = CITIES[city]
    tid = 81000000 + i * 7 + 1                      # terminal_id (link key)
    pos_rows.append({
        "posId": 82000000 + i, "terminalId": tid,
        "nazev": f"Prodejna {random.choice(MARKETS)} {i+1}",
        "street": f"Ulice {i+1}", "houseNumber": str(random.randint(1, 200)),
        "city": city, "area": city[:3].upper(), "posArea": "RS" + random.choice("ABCDEFG"),
        "category": random.choice(CATS), "market": random.choice(MARKETS),
        "classification": random.choice(CLS), "terminalType": random.choices(TTYPES, weights=[6, 2, 2])[0],
        "ppt": round(random.uniform(50, 5000), 1),
        "gpsX": round(lat0 + random.uniform(-0.05, 0.05), 6),
        "gpsY": round(lon0 + random.uniform(-0.07, 0.07), 6),
        "assignedTechnician": TECHS[i % len(TECHS)], "managerOverrideType": None, "status": "ACTIVE",
    })

# cluster: make two POS share the same spot (micro-cluster test)
pos_rows[5]["gpsX"], pos_rows[5]["gpsY"] = pos_rows[4]["gpsX"], pos_rows[4]["gpsY"] + 0.0004


def _write(name, headers, rows):
    wb = openpyxl.Workbook(); ws = wb.active
    ws.append(headers)
    for r in rows:
        ws.append([r.get(h) for h in headers])
    path = os.path.join(OUT, name); wb.save(path)
    print(f"  {name}: {len(rows)} řádků")
    return path


POS_HEADERS = ["posId", "terminalId", "nazev", "street", "houseNumber", "city", "area",
               "posArea", "category", "market", "classification", "terminalType", "ppt",
               "gpsX", "gpsY", "assignedTechnician", "managerOverrideType", "status"]

# SalesApp visits — only ~30 of the 40 POS get visits (the rest test "never visited")
today = datetime.date.today()
visits = []
uid = 1
for p in pos_rows[:30]:
    for _ in range(random.randint(1, 4)):
        d = today - datetime.timedelta(days=random.randint(1, 42))
        start_h = random.randint(8, 15); start_m = random.choice([0, 15, 30, 45])
        dur = random.randint(8, 40)
        st = datetime.datetime(d.year, d.month, d.day, start_h, start_m)
        fin = st + datetime.timedelta(minutes=dur)
        visits.append({
            "UID": uid, "Store UID": p["terminalId"], "Store": p["nazev"],
            "Store address": f'{p["street"]}, {p["city"]}', "Agency region": p["city"],
            "Executor": p["assignedTechnician"], "Executor UID": 100 + (uid % 7),
            "Date": d.isoformat(), "Started at": st.isoformat(sep=" "),
            "Finished at": fin.isoformat(sep=" "), "Real duration (h)": round(dur / 60.0, 3),
            "Účel návštevy - Technik - MCHD - Náběh kampaně": 1,
        })
        uid += 1
random.shuffle(visits)
SA_HEADERS = ["UID", "Store UID", "Store", "Store address", "Agency region", "Executor",
              "Executor UID", "Date", "Started at", "Finished at", "Real duration (h)",
              "Účel návštevy - Technik - MCHD - Náběh kampaně"]

BULK = [{"POS": p["posId"], "Počet kusů": random.randint(10, 200), "Poznámka": "várka A"}
        for p in pos_rows[:12]]

if __name__ == "__main__":
    print("Generuji ukázková data do sample_data/ …")
    _write("POS_master.xlsx", POS_HEADERS, pos_rows)
    _write("SalesApp_export.xlsx", SA_HEADERS, visits)
    _write("Bulk_vouchers.xlsx", ["POS", "Počet kusů", "Poznámka"], BULK)
    print("Hotovo. Naimportuj v pořadí: POS_master → SalesApp_export, pak Bulk_vouchers.")
