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

## Týdenní rituál, krok za krokem

### 1. Import dat ze SalesApp

1. Vlož čerstvý export ze SalesApp do listu `RAW_DATA` (přepiš celý obsah,
   včetně hlavičkového řádku na řádku 2).
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

### 4. Rozeslání publikovaného týdne technikům (ruční, bez appky)

List `TECHNICIAN_PLAN` má živé vzorce a AutoFilter na sloupci `TECHNIK`:

1. Otevři `TECHNICIAN_PLAN` (přepočet vzorců proběhne automaticky, jsi v
   Excelu).
2. AutoFilter → vyber jednoho technika.
3. Vyber viditelné řádky → zkopíruj → vlož do nového sešitu (jen hodnoty),
   nebo File → Print → vyber rozsah → Export do PDF.
4. Pojmenuj soubor stejnou konvencí jako appka:
   `<Technik>_<Rok>_W<Tyden>.xlsx` (nebo `.pdf`), např. `Novak_2026_W32.pdf`.
5. Zopakuj pro každého technika, nebo pošli screenshot/PDF podle zvyklostí.

Tohle je přesně ta ruční práce, kterou `desktop_client/` (V1, export) dělá
jedním kliknutím — v čistě-Excelové variantě ji děláš takhle.

### 5. Vyhodnocení skutečných návštěv (další cyklus)

1. Nový export ze SalesApp → list `SALESAPP_IMPORT`.
2. Automatizace → `office-scripts/ComplianceEngine.ts` → Spustit. Porovná
   se jen proti `MANAGER_PLAN_PUBLISHED` (nikdy proti Draft), posune
   `PLAN_LIFECYCLE` (Published → Active → Closed), doplní `POS_MASTER`.
3. Automatizace → `office-scripts/AdvisorEngine.ts` → Spustit — diagnostická
   upozornění (zanedbané POS, přetížení techniků, rozjetý publikovaný plán
   proti aktuálním datům…) do `ADVISOR_LOG`.
4. Automatizace → `office-scripts/ReportingEngine.ts` → Spustit — aktuální
   `DASHBOARD`.

## Pořadí, které nesmíš přehodit

`ImportEngine → PlanningEngine → (ruční review) → PublishEngine → rozeslání
→ [další týden] ComplianceEngine → AdvisorEngine → ReportingEngine`

Planning Engine nikdy nepřepíše uzamčený (Published/Active/Closed) týden —
je bezpečné ho spouštět opakovaně. Publish Engine publikuje vždy jen
jeden, nejbližší Draft týden za běh.

## Co se v téhle variantě neděje

Žádný soubor mimo workbook, žádná záloha appkou, žádná druhá implementace
logiky v Pythonu — to všechno patří jen k `desktop_client/` V2. Tady je
jediné místo, kde vzniká a mění se plán, list `office-scripts/*.ts` v
Excelu — přesně jak to bylo od začátku myšlené.
