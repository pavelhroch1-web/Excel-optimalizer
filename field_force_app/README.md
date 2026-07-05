# Field Force Optimizer — nová appka (jednoduchý tok, vlastní reporty)

Samostatná appka, oddělená od `desktop_client/`'s Distribution Client (ten
zůstává beze změny). Mentální model, 3 kroky místo 8:

1. **POS/PPT report** → vyber soubor, klikni "Zpracovat" — appka soubor
   sama přečte (najde hlavičkový řádek, nemusíš nic kopírovat) a spustí
   Import + Planning. Vznikne/aktualizuje se Draft tour plan.
2. **Publikovat & sledovat** — schválně samostatné tlačítko (Publish +
   Start Tracking), aby sis Draft mohl/a před odesláním technikům
   zkontrolovat. Nezmizí to sloučením do jednoho kliku - je to záměrná
   manažerská kontrola, ne zbytečný krok navíc.
3. **SalesApp report** → vyber soubor, klikni "Zpracovat" — appka spustí
   Compliance + Advisor + Performance + Reporting.

Kdykoliv pak **📄 Otevřít report** vygeneruje jeden HTML soubor (tour plan
po technicích, KPI přehled, "kdo fláká", dlouhodobý trend po měsících) a
otevře ho v prohlížeči — nikdy nemusíš otevírat Excel.

## Proč "nová appka", ne rozšíření `desktop_client/`

Produkt owner (2026-07-06) chtěl (a) přímé načtení souborů bez ručního
kopírování a (b) vlastní obrazovky s reporty místo dívání se do Excelu -
obojí je dost velká změna toku, že si zaslouží čistý, jednoduchý start
místo dalšího rozšiřování už poměrně nabité appky `desktop_client/`.

**Nic se ale neduplikuje**: veškerá business logika (Import/Planning/
Publish/StartTracking/Compliance/Advisor/Performance/Reporting) běží ve
stejném, už otestovaném kódu jako `desktop_client/engines/` - tahle appka
ho jen jinak krmí (přímo ze souborů) a jinak zobrazuje výsledky (vlastní
HTML, ne Excel). Proto musí `field_force_app/` a `desktop_client/` zůstat
vedle sebe ve stejné nadřazené složce - viz `build_exe.bat`.

## Soubory

- `report_import.py` — najde hlavičkový řádek v surovém exportu (POS/PPT
  nebo SalesApp) a zapíše ho do workbooku přesně tak, jak to ImportEngine/
  ComplianceEngine očekávají.
- `pipeline.py` — spouští `desktop_client/engines/*` ve 3 skupinách (viz
  výše), nic nepočítá jinak.
- `report_view.py` — čte už spočítaná data (TECHNICIAN_PERFORMANCE_LOG/
  SUMMARY, MANAGER_PLAN_PUBLISHED, DASHBOARD) a vygeneruje jeden statický
  HTML soubor - žádné nové výpočty, jen zobrazení.
- `app.py` — Tkinter okno, které tohle všechno spojuje.

## Spuštění ze zdrojového kódu (bez sestavení .exe)

```
pip install openpyxl ttkbootstrap
python3 field_force_app/app.py
```

(spouštět z kořenové složky repozitáře, ne zevnitř `field_force_app/`, ať
funguje `from desktop_client...` import)

## Sestavení do `.exe` (Windows)

Viz `build_exe.bat` v této složce — stejný postup jako u
`desktop_client/build_exe.bat`, jen navíc vyžaduje, aby `desktop_client/`
zůstal vedle `field_force_app/` (kvůli sdílené business logice).

## Testováno

- `report_import.py`: parsování syntetického POS/PPT + SalesApp reportu,
  zápis do reálné kopie produkčního workbooku, ověřeno že
  `desktop_client.engines.import_engine`/`compliance_engine` přečtou
  data přesně tak, jako by byla ručně vložena.
- `pipeline.py`: všech 8 enginů proběhlo bez chyby na reálné kopii
  workbooku (11 605 POS).
- `report_view.py`: vygenerovaný HTML report vizuálně zkontrolován
  (screenshot přes headless Chromium) - KPI, tour plan, "kdo fláká" a
  dlouhodobý trend se zobrazují správně, žádné `None`/prázdné hodnoty
  neprosakují do zobrazení.
- Po celém běhu (POS report → Publikovat → SalesApp report → report):
  listy se vzorci (`HOME`, `TECHNICIAN_SCORECARD`, `PERFORMANCE`,
  `TECHNICIAN_PLAN`, `MAP`, `WEEK_DASHBOARD`, `ACTIVITY_PLAN`) i
  konfigurační listy (`CONTROL`, `CADENCE_RULES`, `CATEGORY_RULES`)
  zůstaly bit-přesně nedotčené.

**Netestováno** (žádné GUI prostředí s Tkinterem v tomhle sandboxu):
samotné okno appky nebylo fyzicky spuštěné a proklikané - jen jeho
logika (import, pipeline, report) přímým voláním funkcí, které GUI
tlačítka volají. Doporučeno vyzkoušet `build_exe.bat` a proklik appky na
kopii workbooku, než se použije ostro.
