# Testovací checklist — první reálná vlna testování

Tento dokument tě provede **všemi hlavními scénáři** aplikace krok za krokem.
U každého kroku je uvedeno, **co udělat** a **co má být výsledek**. Cílem je, abys
na svém PC prošel celý pracovní tok jednoho dne a dal zpětnou vazbu na
**použitelnost (UX)** i **byznys logiku** — ještě než budeme přidávat nové funkce.

> **Testovací data:** ve složce `sample_data/` jsou tři připravené soubory
> (`POS_master.xlsx`, `SalesApp_export.xlsx`, `Bulk_vouchers.xlsx`). Jsou malé a
> vzájemně konzistentní — 40 POS, 4 technici, ~77 návštěv za posledních 6 týdnů.
> Vygeneruješ/obnovíš je kdykoli přes `python3 tools/make_sample_data.py`.
>
> **Doporučení:** testuj na **prázdné** `FieldForceData/` složce (nebo si stávající
> zazálohuj), ať výsledky sedí s očekáváními níže.

---

## 0. Start aplikace

| # | Krok | Očekávaný výsledek |
|---|------|--------------------|
| 0.1 | Rozbal ZIP a spusť `FieldForceOptimizer.exe` | Otevře se okno (nebo prohlížeč) s aplikací, vlevo je menu: Přehled, Import dat, TourPlan, Analytika, Měsíční souhrn, Nastavení |
| 0.2 | Podívej se, že vedle `.exe` vznikla složka `FieldForceData/` | Obsahuje `fieldforce.db` (prázdná databáze) |

---

## 1. Import dat (pořadí je důležité)

Otevři **Import dat**. Importuj v tomto pořadí — POS master jako první, ať se
návštěvy mají na co navázat.

| # | Krok | Očekávaný výsledek |
|---|------|--------------------|
| 1.1 | Nahraj `sample_data/POS_master.xlsx` (drag-drop nebo výběr souboru) | Typ souboru se pozná sám (POS master). Hlášení: **40 nových POS**. |
| 1.2 | Nahraj `sample_data/SalesApp_export.xlsx` | Typ se pozná (SalesApp). Hlášení: **77 návštěv** naimportováno. |
| 1.3 | Zkontroluj přehled po importu | Technici: **4** (Jan Novák, Petr Svoboda, Eva Dvořáková, Tomáš Král), všichni role **TECHNIK**. Návštěvy jsou navázané na POS (0 nenavázaných). |

**Na co se dívat (UX):** poznal typ souboru sám? Bylo jasné, co se stalo? Nešlo
naimportovat ve špatném pořadí bez varování?

---

## 2. Přepočet (recompute)

Po importu se mají automaticky přepočítat analytické vrstvy. Ověř, že proběhly.

| # | Krok | Očekávaný výsledek |
|---|------|--------------------|
| 2.1 | Otevři **Přehled** | KPI dlaždice (počet POS, návštěv, technici) sedí s importem |
| 2.2 | Otevři **TourPlan** → sekce Kapacita | Zobrazí se učený denní standard pro roli TECHNIK (produktivní minuty p50/p70/p90) |
| 2.3 | TourPlan → Predikce trvání | Národní p50 trvání návštěvy ~ **20–25 min** |
| 2.4 | TourPlan → Mikro-clustery | Nalezen alespoň **1 cluster** (dvě POS sdílejí místo — cluster test) |

---

## 3. Segmenty

| # | Krok | Očekávaný výsledek |
|---|------|--------------------|
| 3.1 | TourPlan → Segmenty (nebo Coverage podle segmentů) | Vidíš přednastavené segmenty (Velké terminály, Malé terminály, LI, klasifikace B…) |
| 3.2 | Rozklikni **Velké terminály** | Ukáže počet POS v segmentu a coverage % |
| 3.3 | Ověř, že POS bez návštěvy nejsou falešně „po termínu" | 10 z 40 POS nebylo nikdy navštíveno → jsou v **grace období** (od first_seen), ne červené |

**Na co se dívat (byznys):** dávají segmenty a jejich coverage smysl? Je jasné,
proč je POS zelený/žlutý/červený?

---

## 4. Coverage & riziko

| # | Krok | Očekávaný výsledek |
|---|------|--------------------|
| 4.1 | Otevři coverage panel (Měsíční souhrn nebo TourPlan) | Vidíš coverage podle segmentů, rozdělení rizika high/medium/low |
| 4.2 | Zkontroluj nikdy nenavštívené POS | Počítají se od `first_seen`, ne jako okamžitě prošvihnuté |

---

## 5. Task Engine — hromadný import úkolů (bulk)

| # | Krok | Očekávaný výsledek |
|---|------|--------------------|
| 5.1 | Import dat → karta **Hromadné úkoly** | Nahraj `sample_data/Bulk_vouchers.xlsx` (sloupce POS, Počet kusů, Poznámka) |
| 5.2 | Potvrď import | Vznikne **12 úkolů** navázaných na 12 POS. Neznámé POS by aplikace odmítla s hláškou. |
| 5.3 | Ověř urgenci | Každý úkol má spočítaný `daysToDeadline` a urgenci (overdue/urgent/normal) |

**Na co se dívat (UX):** je jasné, které POS se nenašly? Sedí čištění ID (např.
`82000000.0` → `82000000`)?

---

## 6. Detail POS

| # | Krok | Očekávaný výsledek |
|---|------|--------------------|
| 6.1 | Klikni na konkrétní POS (z mapy nebo seznamu) | Otevře se detailní karta POS |
| 6.2 | Zkontroluj obsah karty | Vidíš: základní údaje, historii návštěv, predikci trvání, cluster, otevřené úkoly ke stejnému POS |
| 6.3 | POS s bulk úkolem | V kartě je vidět založený úkol (počet kusů, poznámka, deadline) |

---

## 7. Detail technik / den + mapa

| # | Krok | Očekávaný výsledek |
|---|------|--------------------|
| 7.1 | Měsíční souhrn → rozklikni technika (např. Jan Novák) | Otevře se detail s návštěvami po dnech |
| 7.2 | Vyber konkrétní den | Na mapě se vykreslí **skutečná trasa po silnicích** (při internetu), jinak přímky |
| 7.3 | Zkontroluj čas a km za den | Čas na POS, čas jízdy, km — odpovídají realitě (pauzy/office se nepočítají jako čas na POS) |
| 7.4 | Gap validace | Mezery mezi návštěvami klasifikované zeleně/žlutě/červeně |
| 7.5 | Manažerský verdikt dne | Krátké shrnutí, jestli byl den efektivní |

---

## 8. GIS síťová mapa (Měsíční souhrn)

| # | Krok | Očekávaný výsledek |
|---|------|--------------------|
| 8.1 | Měsíční souhrn → mapa sítě | Vykreslí se mapa s POS (města Praha/Brno/Ostrava) |
| 8.2 | Přepínej vrstvy | Navštívené/nenavštívené POS, plánované, heat, regiony, technici, trasy… |
| 8.3 | Klikni na region | Celý dashboard se přefiltruje na daný region |
| 8.4 | Klikni na POS na mapě | Otevře se stejná detailní karta POS jako v bodě 6 (jednotné) |

---

## 9. Měsíční souhrn (analytika)

| # | Krok | Očekávaný výsledek |
|---|------|--------------------|
| 9.1 | Otevři **Měsíční souhrn** | KPI s deltami vůči minulému období, TOP žebříčky, trendy |
| 9.2 | Filtruj podle období / regionu / technika | Všechny metriky se propojeně přefiltrují |
| 9.3 | Klikni na libovolné číslo | Prokliká se až na konkrétního technika |
| 9.4 | Zkontroluj graf plnění plánu a trend nevysvětlené mezery | Zobrazí se časové řady |

---

## 10. Exporty

| # | Krok | Očekávaný výsledek |
|---|------|--------------------|
| 10.1 | Vyexportuj data z některé obrazovky (pokud tlačítko existuje) | Vznikne Excel/soubor s aktuálně zobrazenými daty |
| 10.2 | Otevři export | Data sedí s tím, co je na obrazovce |

---

## 11. Aktualizace (data zůstávají)

| # | Krok | Očekávaný výsledek |
|---|------|--------------------|
| 11.1 | Zavři aplikaci, „přepiš" `.exe` + `_internal/` novou verzí, `FieldForceData/` nech být | — |
| 11.2 | Spusť novou verzi | Data (POS, návštěvy, segmenty, konfigurace) **zůstala zachovaná**; případné nové tabulky/sloupce se doplnily samy |

---

## Jak dávat zpětnou vazbu

U každého scénáře si poznač:

1. **Fungovalo to?** (ano / ne / částečně)
2. **UX:** bylo jasné, co dělat? Kolik kliknutí? Chybělo něco na obrazovce?
3. **Byznys logika:** dávají čísla/verdikty smysl proti realitě?
4. **Konzistence:** chovala se obrazovka stejně jako ostatní (filtry, tabulky,
   tlačítka, dialogy)?

Zapiš to volně (klidně k číslům scénářů výše). Nové funkce přidáme až po vyhodnocení
této první vlny.
