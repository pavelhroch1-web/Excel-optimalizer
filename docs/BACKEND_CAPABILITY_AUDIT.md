# Audit business schopností backendu vs. využití frontendem

Cíl: co backend **skutečně umí** a k čemu se dnes uživatel (zejména v Planner
Studiu) **nedostane**. Ne kód, ale schopnosti. Metoda: 129 API endpointů
zkříženo s tím, co `web/app.js` volá a KDE to vykresluje. Žádné návrhy nových
funkcí — jen existující, dnes nevyužitá funkcionalita.

Legenda využití: **ANO** = dostupné a použitelné tam, kde dává smysl ·
**ČÁSTEČNĚ** = volá se, ale zahrabané / neúplné / ne v Planneru, kde by
rozhodovalo · **NE** = endpoint existuje, frontend ho nevolá vůbec.

---

## A) Planning Engine a rozhodovací opora

| Funkce | Kde | Co umí | FE? | Jak zpřístupnit |
|---|---|---|---|---|
| **Forward scénáře / sweep** | `planner_sweep.py` · `POST /api/planner/sweep` | Kolik POS se obslouží při 35/40/45 návštěvách/technik, **za kolik týdnů se pokryje celá obsloužitelná síť**, co se změní se změnou kapacity/počtu techniků | ČÁSTEČNĚ (volá se v predikčním panelu, **ne ve Wizardu/Planneru**, kde se kapacita volí) | Vložit do Wizardu kroku „Kapacita": u slideru rovnou „při 40 pokryješ X POS, celou síť za Y týdnů" |
| **Co zůstane neobslouženo a proč** | `planner_unserved.py` · `POST /api/planner/unserved` | Které POS se nenaplánovaly, seskupené **důvodem, který dal sám engine** (nevešlo do kapacity / hold-back / min. rozestup / vyřazeno filtrem); důležité POS (CORE / vysoké PPT / kadence) první | ČÁSTEČNĚ (existuje panel, ne v Review po generování) | Panel do Review fáze: „Kritické POS, které nevyšly" hned u návrhu |
| **Advisor – odpovědi, ne čísla** | `planner_advisor.py` · `POST /api/planner/advise` | Dává **odpovědi**: dává plán obchodní smysl, kde je nejslabší článek, co limituje růst, co změnit pro dosažení cíle | ČÁSTEČNĚ | Vedle assess scorecardu v Kontrola kroku Wizardu ukázat 2–3 věty „doporučení" |
| **Decision support – recommend / what-if** | `decision.py` · `GET /api/draft/what-if`, `POST /api/planner/whatif` | Per-POS „doporučit / zatím ne, protože…" z jeho vlastních skóre složek; dopad manažerských páček (partner off, terminál, kategorie) z jednoho běhu | ČÁSTEČNĚ | Rozšířit „Co kdyby" v Planneru o per-POS recommend |
| **Kandidáti POS + rozpad skóre** | `candidates.py` · `GET /api/draft/candidates` | U každého kandidáta přesné skóre a **proč byl/nebyl vybrán** (PPT/CORE/A/gap/neglect/urgency/GPS) | ANO (panel Kandidáti POS) | — |
| **Segmentový model + riziko kadence** | `segments.py` · `GET/POST /api/planner/segments` | Libovolný segment nad atributy POS s vlastní **cílovou kadencí, prioritou, obchodní vahou, min. pokrytím**; stav pokrytí: kolik je v kadenci, kolik po termínu / trendově po termínu, výsledné **obchodní riziko** | ČÁSTEČNĚ (jen seed, riziko nevykresleno) | Panel „Riziko podle segmentů" — kde síť nejvíc padá z kadence |
| **Strategické módy** | `brain.py` · `GET /api/strategy-modes` | 3 režimy (dojezd/kampaň/vyvážený) | ANO (Wizard priorita) | — |

## B) Kapacita, doba návštěvy, trasy, čas

| Funkce | Kde | Co umí | FE? | Jak zpřístupnit |
|---|---|---|---|---|
| **Naučený kapacitní standard** | `capacity.py` · `GET /api/planner/capacity` | Firemní standard produktivních minut/den (p60/p70 z historie, per role) — **nekopíruje slabý výkon**, tlačí všechny stejným směrem | ČÁSTEČNĚ (ukáže se ve Správě modelů, ale planner/Wizard ho NEbere — kapacita je natvrdo 40) | Předvyplnit Wizard kapacitu z tohoto čísla + „doporučeno z historie" |
| **Predikce doby návštěvy** | `duration.py` · `GET /api/planner/duration/*` | Odhad délky návštěvy per POS (kategorie→síť→region→technik) | ČÁSTEČNĚ (POS karta + nově feasibility) | Ukázat u POS v návrhu „odhad ~22 min" |
| **Reálný silniční čas** | `travel_model.py` | Přímá km → silniční čas (nelineární rampa rychlosti) | ANO (nově feasibility) | — |
| **Časová proveditelnost** | `plan_feasibility.py` · `GET /api/plan/feasibility` | Reálná časová zátěž dne/týdne vs. hodiny, přeplněné/napjaté dny | ANO (Review) | — |
| **Micro-clustery POS** | `clustering.py` · `GET /api/planner/clusters/*` | POS ve stejném centru / pár metrů = jedna jednotka (skoro nulový přejezd navíc) | ČÁSTEČNĚ (POS karta) | V návrhu vizuálně sdružit clustery („3 POS v jednom centru") |

## C) Import a historie / paměť

| Funkce | Kde | Co umí | FE? | Jak zpřístupnit |
|---|---|---|---|---|
| **Import (auto/typovaný/workbook/šablony)** | `auto_import.py`, `importer.py` · `/api/import/*` | Drag-drop autodetekce typu, celý workbook, šablony, ukázková data | ANO (Import Center) | — |
| **Historická paměť** | `history.py` · `GET /api/history/events`, `/history/metrics`, `/history/planner-runs` | **Vývoj PPT v čase**, churn sítě (otevírání/zavírání POS), jednotná **audit timeline** (import/publish/plánování/config), KPI snapshoty týden/měsíc/kvartál/rok | NE (events/metrics nevolány; planner-runs jen 1× v ranním briefu) | Sekce „Historie & audit": timeline změn + graf vývoje PPT/velikosti sítě |
| **Operační paměť** | `memory.py` · `GET /api/memory/*` | Evoluce jednoho POS v čase, detail a **porovnání plánovacích běhů**, config-diff (co se od minula změnilo v nastavení), trend | ČÁSTEČNĚ (jen trend v briefu; evolution/config-diff/run-compare NE) | „Co se změnilo od posledního plánu" (config-diff) před generováním |

## D) SalesApp historie, analytika, statistiky

| Funkce | Kde | Co umí | FE? | Jak zpřístupnit |
|---|---|---|---|---|
| **Route analytics technika** | `route_analytics.py` · `/api/analytics/day`, `/trends` | Den technika: km, vrstvy mapy, nálezy neefektivity, trendy | ANO (detail technika) | — |
| **Team cockpit** | `team_analytics.py` · `/api/analytics/team` | Přetížení vs. rezerva, únik km/času, kdo potřebuje pozornost | ANO (Analytika) | — |
| **Plán vs. skutečnost** | `plan_reality.py` · `/api/reality/fulfillment`, `/reality/technicians` | Splněno včas/pozdě/nesplněno, mimo plán, jiným technikem, reálná aktivita | ČÁSTEČNĚ (detail technika, ne jako zpětná vazba v Planneru) | Po publikaci ukázat „minulý plán: splněno X %" jako vstup do dalšího |
| **Diagnostika příčin** | `diagnostics.py` · `/api/insights/diagnose`, `/health`, `/company` | Proč je technik neefektivní (z-skóre vs. peers), Health Score, firemní ztracené hodiny podle regionů | ANO (detail technika, Ops Center) | — |
| **Insight vrstva** | `insights.py` · `/api/insights` | Anomálie / neefektivita / příležitosti, které by manažer neviděl | ČÁSTEČNĚ (Ops Center) | — |
| **Automatické alerty** | `alerts.py` · `/api/alerts`, `/alerts/recompute` | Neobvykle dlouhý/krátký čas na POS, nízká aktivita, **chronicky neobsloužené POS** | ČÁSTEČNĚ (Ops feed; chronicky-neobsloužené je ale plánovací signál, ne jen alert) | Přivést chronicky-neobsloužené do Planneru jako prioritní kandidáty |
| **Long-term hodnocení** | `tech_trends.py`, `tech_score.py` · `/api/analytics/technicians/series`, `/api/trends` | Trendy per technik/region, Technician Score, SLA | ANO (detail technika Dlouhodobě) | — |

## E) GIS / mapové vrstvy

| Funkce | Kde | Co umí | FE? | Jak zpřístupnit |
|---|---|---|---|---|
| **Síťová mapa** | `gis.py` · `/api/gis/network` | Heatmapa hustoty návštěv, centroidy regionů/techniků, filtry | ANO (Mapa) | — |
| **Den technika po silnicích** | `gis.py`, `osrm.py` · `/api/gis/technician/{}/day/{}` | Reálná trasa dne po silnicích (OSRM), projeté vs. plánované | ANO (detail technika) | — |
| **POS-level GIS** | `gis.py` · `/api/gis/pos/{pos_id}` | Detail jednoho POS na mapě s okolím | **NE** (nevolá se) | Proklik z POS karty na mapu okolí |

## F) Reporting, POS explorer, engine inventář

| Funkce | Kde | Co umí | FE? | Jak zpřístupnit |
|---|---|---|---|---|
| **Měsíční souhrn** | `summary.py` · `/api/summary`, `/summary/dimensions` | Manažerský přehled za období s filtry a drill-down | ANO (Měsíční souhrn) | — |
| **POS Explorer** | `pos_insights.py` · `/api/pos/*` | Seznam/hledání/karta/historie/návštěvy POS, filtry | ANO (POS) | — |
| **Inventář parametrů enginu** | `engine_config.py` · `/api/engine/inventory` | Každá obchodní konstanta/váha, kterou engine používá — výchozí vs. aktuální + **co v algoritmu řídí** | ANO (Nastavení – pokročilé) | — |
| **Task engine** | `tasks.py`, `task_bridge.py` · `/api/planner/tasks*`, `task-types` | Typy úkolů/materiálů, kombinovatelnost s návštěvou, hromadné akce | ČÁSTEČNĚ (Nastavení) | Ukázat úkoly u POS přímo v návrhu plánu |

---

## Seznam podle hodnoty: Backend umí → Frontend neumí → Vysoký přínos

Seřazeno podle obchodního přínosu × snadnost zpřístupnění. Vše je **existující**
funkcionalita, jen k ní uživatel dnes nemá cestu z místa, kde by ji potřeboval.

### 🟢 Nejvyšší přínos, nízká náročnost zpřístupnění
1. **Forward sweep u volby kapacity** (`planner_sweep`) — „při 40/technik pokryješ X POS, celou síť za Y týdnů". Přímá odpověď na hlavní plánovací otázku; dnes zahrabané mimo Wizard. *Stačí zavolat existující endpoint v kroku Kapacita.*
2. **Naučený kapacitní standard jako default** (`capacity`) — místo natvrdo 40 nabídnout p60/p70 z historie s vysvětlením. *Předvyplnění z existujícího endpointu.*
3. **„Co nevyšlo a proč" v Review** (`planner_unserved`) — po generování ukázat důležité POS, které se nevešly, s důvodem enginu. *Existující endpoint, chybí panel u návrhu.*
4. **Advisor odpovědi u scorecardu** (`planner_advisor`) — „nejslabší článek / co změnit pro cíl" místo jen čísel. *Existující endpoint.*

### 🟡 Vysoký přínos, střední náročnost
5. **Riziko podle segmentů** (`segments`) — kde síť trendově padá z kadence, s obchodní vahou; dnes jen seedováno. *Vykreslit coverage-risk, který backend počítá.*
6. **Chronicky neobsloužené POS do Planneru** (`alerts`) — plánovací signál dnes jen v Ops feedu. *Přenést do kandidátů/priorit.*
7. **Config-diff „co se změnilo od minula"** (`memory`) — před generováním ukázat rozdíl konfigurace/plánu proti poslednímu běhu. *Existující endpoint, nevolá se.*
8. **Vývoj PPT a churn sítě v čase** (`history`) — obchodní trend hodnoty sítě; endpointy existují, nevolají se. *Sekce Historie.*

### 🟠 Střední přínos
9. **Per-POS doba návštěvy a recommend v návrhu** (`duration`, `decision`) — u řádku plánu „~22 min, doporučeno protože…".
10. **Micro-clustery vizuálně v návrhu** (`clustering`) — „3 POS v jednom centru = jedna zastávka".
11. **Plán vs. skutečnost jako zpětná vazba do Planneru** (`plan_reality`) — „minulý plán splněn na X %" jako vstup.
12. **Porovnání plánovacích běhů** (`memory` planner-run compare) — co se mezi verzemi změnilo.

### 🔵 Nižší přínos / dokončení
13. **POS-level GIS mapa** (`gis/pos`) — nevolá se vůbec.
14. **Úkoly u POS přímo v návrhu** (`tasks`).
15. **Route/actual raw endpoint** — kapacita je pokrytá přes detail technika, samotný endpoint nevyužit.

---

## Klíčové zjištění

Backend je **výrazně bohatší než frontend ukazuje** — zejména **rozhodovací
opora pro plánování** (sweep, unserved, advisor, segmentové riziko, naučená
kapacita) existuje a je hotová, ale **Planner Studio a Wizard ji nenabízejí**;
žije rozptýleně v analytických panelech, nebo se nevolá vůbec. Největší
nevyužitá hodnota není v nových funkcích, ale v **přivedení už hotové plánovací
inteligence tam, kde uživatel plán skutečně tvoří.**
