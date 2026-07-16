# Proof of Concept — celý workflow end-to-end

Důkaz, že backend a plánovací logika fungují **od začátku do konce na reálných
datech**: import → úkoly (servis/kampaň/materiál) → Planning Engine → bundling →
TourPlan → zobrazení úkolů u zastávek → export pro techniky.

Runner: **`tools/poc_end_to_end.py`**. Spouští **skutečné** moduly backendu
(žádná atrapa), na dočasné databázi (tvá reálná data se nedotkne).

---

## Jak spustit

### A) Na reálné datové sadě, která je v repu (scaffold = 11 605 reálných POS)
```
python3 tools/poc_end_to_end.py
```
Naimportuje reálný workbook (`workbook/FieldForceOptimizer_V11_scaffold.xlsx`),
sám založí demonstrační úkoly na reálných POS a ověří celý řetězec.

### B) Na VLASTNÍCH datech
```
python3 tools/poc_end_to_end.py \
    --workbook  muj_export.xlsx \      # workbook s POS + návštěvami + konfigurací
    --week 35 \                        # otevřený (nepublikovaný) týden
    --tasks-excel moje_ukoly.xlsx \    # Excel: POS + počet (+ poznámka)
    --type-id 7                        # typ aktivity pro tyto úkoly
```
`--type-id` zjistíš ze seznamu typů (po importu vypíše `tasks.types()` v appce /
DB). Bez `--tasks-excel` PoC založí demonstrační úkoly sám.

Parametry: `--mode` (default `dojezd`), `--capacity` (návštěv/technik/týden,
default 40), `--length` (počet týdnů, default 1), `--out-dir` (kam uložit export).

---

## Postup krok za krokem a očekávaný výsledek

| Krok | Co se děje | Očekávaný výsledek |
|------|-----------|--------------------|
| **1. Import** | `importer.import_workbook` → SQLite (POS, návštěvy, konfigurace) + sestavení plánovacího stavu | POS > 0, návštěvy naimportovány, konfigurace > 0, RAW_DATA sestaveno |
| **baseline** | Planning Engine BEZ úkolů (pro srovnání) | vygeneruje se plán pro zvolený týden |
| **2. Úkoly** | založení úkolů (servis/kampaň/materiál) z Excelu nebo demonstrační | úkoly založené, evidované jako otevřené |
| **3+5. Engine → TourPlan** | `db_state.configure` (aplikuje most úkolů) + `run_planning` | MANAGER_PLAN vygenerován |
| **4. Slučování + rozhodnutí** | ověření chování enginu a bundlingu | viz 4 klíčová chování níže |
| **6. Zastávky** | `plan_io.read_enriched_draft` | zastávky nesou přibalené úkoly (balík) |
| **7. Export** | MANAGER_PLAN + sloupec **ÚKOLY** | export .xlsx se souhrnem úkolů u zastávek |

Na konci: `VÝSLEDEK PoC: N/N kontrol prošlo` a cesta k exportu.

### Ověřená klíčová chování (reálná data, poslední běh: 17/17)
- **Urgentní úkol → samostatný výjezd.** POS, který engine normálně tento týden
  nenavštíví, se po urgentním úkolu (deadline ≤ 14 dní) objeví v plánu
  (`FORCE_INCLUDE`). *(ověřeno: baseline False → plán True)*
- **Nekombinovatelný úkol → samostatný výjezd.** *(ověřeno)*
- **Kombinovatelný úkol s daleko deadlinem → žádný výjezd navíc.** Zůstane čekat,
  přibalí se, až technik na POS pojede. *(ověřeno: v plánu False)*
- **Kombinovatelný úkol na už plánovaném POS → přibalí se k té návštěvě.**
  *(ověřeno: v plánu True, úkolů = 2)*
- **Slučování + priorita + deadline.** Víc úkolů na jednom POS se sloučí a seřadí
  podle priority (Kotouče prio 3 před Letáky prio 4), s množstvím a prioritou.
  *(ověřeno: pořadí ['Kotouče','Letáky'], summary „Materiál: Kotouče 50× | …")*
- **Export.** MANAGER_PLAN má sloupec **ÚKOLY** s vyplněným souhrnem u zastávek.

---

## Výkon (orientačně, 11 605 POS na tomto stroji)
- import workbooku do SQLite: ~30 s
- sestavení plánovacího stavu (`build_upload_draft`): ~20–30 s
- běh Planning Engine (1 týden): ~2 s
- celý PoC (2× plán kvůli baseline + čtení/export): ~2–3 min

Není to instantní, ale jde o dávkový výpočet jednou za plánovací cyklus.

---

## Známá omezení

1. **`FORCE_INCLUDE` nepřidá druhý výjezd na už naplánovaný POS.** Když je POS
   už v plánu (i v dřívějším/uzamčeném týdnu), urgentní úkol nevytvoří duplicitní
   návštěvu — úkol se přibalí k té existující. To je **správné chování** (neděláme
   výjezd navíc), ale znamená: urgentní úkol **nepřetáhne** POS z pozdějšího týdne
   dopředu. Pokud to bude potřeba, je to samostatná funkce (re-prioritizace).
2. **POS musí být v obou vrstvách.** Úkol se zakládá jen na POS známém v databázi
   (`pos_master`), a do plánu se dostane jen POS, který je v plánovací síti
   (`RAW_DATA`). Úkol na POS mimo síť se nezaplánuje. V desktopu obojí plní import.
3. **Urgence = deadline/kombinovatelnost, ne priorita.** O samostatném výjezdu
   rozhoduje `combinable` a deadline (≤ 14 dní), ne číslo priority. Priorita řadí
   úkoly v rámci zastávky (co dřív), neurčuje, zda vznikne výjezd.
4. **Sloupec ÚKOLY v Excelu je jednořádkový souhrn.** Plný rozpis (poznámka,
   deadline, minuty) je v aplikaci u zastávky; v jedné buňce Excelu je zkrácený
   souhrn „Kategorie: Typ množství×".
5. **Kapacita a režim se musí nastavit.** Bez `configure` (režim + kapacita)
   engine nenaplánuje nic nového. PoC používá `dojezd` + 40; uprav dle potřeby.
6. **Engine se nemění.** Úkoly ovlivňují plán jen přes overlay (`FORCE_INCLUDE`)
   a přes prezentační bundling — algoritmus zůstává beze změny (core testy 120/0).

## Známé chyby
- **Žádná v enginu ani v mostu úkolů.** Všech 17 kontrol prošlo.
- Během ladění PoC se ukázala jedna chyba **v testu, ne v produktu**: první verze
  vybírala „nenaplánované" POS jen podle cílového týdne, ale některé už byly
  naplánované v uzamčeném týdnu 29 — engine je (správně) znovu nenaplánoval.
  Opraveno: PoC teď vybírá POS nenaplánované v **žádném** týdnu. *(Poučení, ne
  produktová chyba.)*

---

## Co PoC dokazuje

Cíl aplikace není jen optimalizace trasy, ale **minimalizace počtu návštěv**:
při jedné návštěvě se udělá maximum práce (servis + kampaň + materiál), a
samostatný výjezd vznikne jen tam, kde to deadline/nekombinovatelnost vyžaduje.
Tenhle mechanismus je nyní **ověřený end-to-end na reálných datech** — než se
pustíme do dalších funkcí nebo UX.
