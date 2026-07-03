# Field Force Optimizer — týdenní provoz jen v Excelu (bez Distribution Clienta)

Tahle varianta nepoužívá `desktop_client/` vůbec. Vše běží v Excelu na webu
(Excel Online) přes Office Scripts. Je to pomalejší na rozeslání plánů
technikům (ruční filtrování/export místo jednoho kliknutí), ale nemá žádnou
druhou implementaci business logiky — jediný zdroj pravdy je 100 % v
`office-scripts/*.ts`, nic se nikam nekopíruje.

Kdy tuhle variantu použít: když z jakéhokoli důvodu nechceš instalovat/
spouštět desktopovou appku (jiný počítač, IT politika, nedůvěra k nové
komponentě), nebo jako záložní postup, kdyby appka měla problém.

## Předpoklad

Workbook (`FieldForceOptimizer_V11_scaffold.xlsx` nebo jeho aktuální kopie)
musí být uložený na OneDrive/SharePoint a otevřený v **Excelu na webu**
(office.com), ne v desktopové aplikaci — Office Scripts běží jen tam.

## Jednorázové nastavení: tlačítko pro každý skript (doporučeno, ~2 min/skript)

Bez tohohle musíš pokaždé jít do Automatizace, najít skript a dát Spustit.
S tlačítkem stačí jeden klik přímo v listu. Udělej to jednou, pro každý
skript zvlášť:

1. Automatizace → otevři konkrétní skript (např. "Import Engine").
2. V horní liště editoru skriptu najdi **"..."** (tři tečky) nebo přímo
   ikonu **"Přidat tlačítko"** (Add button), obvykle vedle tlačítka
   "Spustit".
3. Klikni na ni — Excel vloží do aktuálně otevřeného listu tlačítko
   navázané na tenhle skript. Přesuň ho třeba do `IMPORT_HUB`, vedle
   popisu příslušného kroku.
4. Zopakuj pro `PlanningEngine.ts`, `PublishEngine.ts` (a později
   `ComplianceEngine.ts`, `AdvisorEngine.ts`, `ReportingEngine.ts`).

Tohle nejde předpřipravit v souboru zvenčí (je to servisní vazba, kterou
umí vytvořit jen Excel Online interaktivně), ale jde to udělat jen jednou
— tlačítko zůstává v listu napořád.

## Týdenní rituál, krok za krokem

### 1. Import dat o POS/terminálech (NENÍ SalesApp — to přijde až v kroku 5)

`RAW_DATA` je export **seznamu POS/terminálů** (odjinud, ne ze SalesApp) —
GPS, kategorie, přiřazený technik atd. SalesApp export (návštěvy) sem
nepatří, ten jde až do `SALESAPP_IMPORT` v kroku 5.

1. Vlož čerstvý export POS/terminálů do listu `RAW_DATA` (přepiš celý
   obsah, včetně hlavičkového řádku na řádku 2).
2. Pokud máš seznam uzavřených/otevřených POS, vlož ho do
   `POS_STATUS_IMPORT`.
3. Karta **Automatizace** (Automate) → otevři/vytvoř skript → vlož **celý**
   obsah `office-scripts/ImportEngine.ts` → **Spustit**.
4. Zkontroluj log dole ("Import Engine: N POS_MASTER rows upserted…") a
   list `POS_MASTER` — měl by mít jeden řádek na POS.

### 2. Generování/aktualizace plánu (Draft týdny)

1. Automatizace → vlož obsah `office-scripts/PlanningEngine.ts` → **Spustit**.
2. Zkontroluj `MANAGER_PLAN` — nové Draft týdny se objeví, uzamčené
   (Published/Active/Closed) týdny zůstanou beze změny.
3. Pokud chceš něco ručně upravit (vyloučit POS, přeřadit technika),
   uprav příslušné sloupce v `POS_MASTER`
   (`managerOverrideType`/`managerOverrideTechnician`) a spusť
   `PlanningEngine.ts` znovu — jen Draft týdny se přegenerují.

### 3. Publikace týdne

1. Až je Draft týden hotový k odeslání technikům: Automatizace → vlož obsah
   `office-scripts/PublishEngine.ts` → **Spustit**.
2. Publikuje se vždy jen **nejbližší** Draft týden (ne všechny najednou).
   Zkontroluj `PLAN_LIFECYCLE` — daný týden má teď status `Published`.

### 4. Rozeslání TOUR PLANu technikům (list `TECHNICIAN_PLAN`)

List `TECHNICIAN_PLAN` je od 2026-07-06 filtrovaný pohled (dropdown výběr
technika), ne syrová tabulka — žádné ruční AutoFilter/copy-paste. Zobrazí
rovnou celou aktuální kampaň (všechny týdny v `MANAGER_PLAN`, seskupené po
týdnech), přesně to, co se technikovi reálně posílá cca jednou za kampaň:

1. Otevři `TECHNICIAN_PLAN`.
2. V dropdownu "TECHNIK" vyber technika — trasa na celou kampaň se hned
   zobrazí (živé vzorce, žádné čekání na engine).
3. File → Print, nebo File → Export → Vytvořit PDF/XPS dokument. Tiskové
   nastavení (na šířku, přizpůsobit šířce stránky, opakovat záhlaví) je
   už přednastavené.
4. Zopakuj pro každého technika (přepni dropdown, znovu Print/Export).

Tohle je přesně ta ruční práce, kterou `desktop_client/` (V1, export) dělá
jedním kliknutím — v čistě-Excelové variantě je teď stejně rychlá (jeden
dropdown + Print), bez appky.

### 5. Vyhodnocení skutečných návštěv (další cyklus)

1. Nový export **návštěv ze SalesApp** (soubor typu "Visit Data", jeden
   řádek = jedna návštěva) → list `SALESAPP_IMPORT`, přepiš od řádku 1
   (hlavička je na řádku 1 zde, ne na řádku 2 jako u RAW_DATA). Vlož ho
   celý, včetně sloupce **"Účel návštevy - Technik - MCHD - Náběh
   kampaně"** — bez něj Compliance Engine nepozná, které návštěvy se
   počítají jako splněná kampaňová návštěva.
2. Automatizace → `office-scripts/ComplianceEngine.ts` → Spustit. Počítá
   jako splněnou návštěvu jen řádek se stavem Completed/Finalized **a**
   "MCHD - Náběh kampaně" = Ano — jiné návštěvy (zásobování, stahování
   losů…) se ignorují úplně, i když v SalesApp reálně proběhly. Porovná
   se jen proti `MANAGER_PLAN_PUBLISHED` (nikdy proti Draft), posune
   `PLAN_LIFECYCLE` (Published → Active → Closed), doplní `POS_MASTER`.
3. Automatizace → `office-scripts/AdvisorEngine.ts` → Spustit — diagnostická
   upozornění (zanedbané POS, přetížení techniků, rozjetý publikovaný plán
   proti aktuálním datům…) do `ADVISOR_LOG`.
4. Automatizace → `office-scripts/PerformanceEngine.ts` → Spustit — aktualizuje
   `TECHNICIAN_PERFORMANCE_LOG` (podklad pro manažerské listy `TECHNICIAN_SCORECARD`/
   `PERFORMANCE`/`WEEK_DETAIL`).
5. Automatizace → `office-scripts/ReportingEngine.ts` → Spustit — aktuální
   `DASHBOARD`.

## Pořadí, které nesmíš přehodit

`ImportEngine → PlanningEngine → (ruční review) → PublishEngine → rozeslání
→ [další týden] ComplianceEngine → AdvisorEngine → PerformanceEngine → ReportingEngine`

Planning Engine nikdy nepřepíše uzamčený (Published/Active/Closed) týden —
je bezpečné ho spouštět opakovaně. Publish Engine publikuje vždy jen
jeden, nejbližší Draft týden za běh.

## Co se v téhle variantě neděje

Žádný soubor mimo workbook, žádná záloha appkou, žádná druhá implementace
logiky v Pythonu — to všechno patří jen k `desktop_client/` V2. Tady je
jediné místo, kde vzniká a mění se plán, list `office-scripts/*.ts` v
Excelu — přesně jak to bylo od začátku myšlené.
