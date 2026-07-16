# Tvrdý technický audit — stav backend vs frontend

Metoda: přímá inspekce kódu. **Backend** = existuje modul + endpoint v `backend/main.py`.
**Frontend** = endpoint je reálně volaný z `web/app.js` a má obrazovku/prvek.
Stav: **Hotovo** (funkční a v UI) / **Částečně** (běží, ale UI neúplné) / **Chybí**.
Bez odhadů — u každé položky důkaz (soubor:řádek / endpoint / modul).

Počty: **115 API endpointů**, **57 backend modulů**, frontend volá **82** z nich.

---

## Úvodní přiznání (bez obhajoby)

Návrhy a mockupy, které jsem ukazoval (artefakty „Tour Plán Cockpit", poslední
„návrh UI/UX"), byly **HTML koncepty, ne reálná aplikace**. Vypadaly jako hotový
produkt. Skutečný frontend jim **neodpovídá** — část logiky je jen v backendu bez
obrazovky. Tenhle dokument říká, kde přesně ten rozdíl je.

---

## Tabulka po funkcích

| # | Funkce | Backend | Frontend | Jen FE? | Vyžaduje BE? | Důkaz |
|---|--------|---------|----------|---------|--------------|-------|
| 1 | **Dashboard (Přehled)** | Hotovo | **Částečně** | Ano | Ne | BE: `insights.py`, `/api/insights`, `/api/insights/company`. FE: `app.js loadCockpitBrief()` ~ř.2458, `renderBrief()`. Cockpit existuje (brief, KPI, health), ale **není** plná provozní vize (vytížení techniků, doporučení, plán tras jsou slabé/roztroušené). |
| 2 | **Health Score** | Hotovo | Hotovo | — | Ne | BE: `diagnostics.py`, `/api/insights/health`. FE: `loadHealth()` ~ř.2506, „Kritické případy" s kolečky (viditelné na tvých screenech). |
| 3 | **Technik Score** | Hotovo | Hotovo | — | Ne | BE: `team_analytics.py`, `/api/analytics/team`. FE: „Dashboard techniků" — TOP/nejslabší se skóre 99/58, `#team-table`. |
| 4 | **Detail technika** | Hotovo | **Částečně** | Ano | Ne | BE: `tech_detail.py`, `/api/technician/{name}`. FE: `openTechDetail()` ~ř.2858, taby Přehled/Anomálie/Dny&trasy/Trendy (`td-tabs`). Existuje, ale ne plně dotažené. |
| 5 | **Denní plán technika (trasa/den/odchylky)** | Hotovo | **Částečně** | Ano | Ne | BE: `route_actual.py`, `/api/analytics/day`, `/api/gis/technician/{name}/day/{date}`. FE: Analytika „Analýza techniků" (`route-actual-form` → `/api/analytics/day` ř.1434), mapa dne (`gis/technician…` ř.3534), gap/odchylky. Je tam, ale schované a nepropojené s grafy. |
| 6 | **TourPlan** | Hotovo | Hotovo | — | Ne | BE: `pipeline.py`, `/api/draft/{upload,generate,view,publish}`, `candidates.py`, `route_planner.py`. FE: celá sekce TourPlan (nahrát→pravidla→generovat→kandidáti→návrh→publikovat). Funkční, ale UI je těžké (12 karet). |
| 7 | **Detail POS** | Hotovo | Hotovo | — | Ne | BE: `pos_insights.pos_card()`. FE: `openPosDetail()` ř.845–856 — načte card + predikci trvání + cluster + úkoly, `renderPosCard()`. |
| 8 | **Historie POS** | Hotovo | **Chybí** | Ano | Ne | BE: `/api/pos/{id}/history`, `history.record_pos_changes()`, tabulka `pos_master_history`. FE: **0 volání** (`grep '/api/pos/…history'` = 0). Historie se ukládá, ale nikde se nezobrazuje. |
| 9 | **Kampaně** | Hotovo | **Chybí** | Ano | Ne | BE: `/api/campaigns`, `/api/campaigns/{id}`, `importer.import_activity_plan()`. FE: **0 volání** `/api/campaigns`. (TourPlan má tabulku kampaní jako *plánovací pravidlo*, ne seznam naimportovaných kampaní.) |
| 10 | **Materiály** | Hotovo | **Částečně** | Ano | Ne | BE: `tasks.py` `category='material'` (Kotouče/Letáky/Stojánky/Poukázky, seed). FE: hromadný import úkolů (`initBulkTasks`), v detailu POS se úkoly zobrazí. **Chybí** UI pro správu typů/kategorií (service/campaign/material). |
| 11 | **Bundling úkolů** | Hotovo | **Částečně** | Ano | Ne | BE: `tasks.bundle_for_pos()`, `plan_io` přidává balík ke stopě, export sloupec `ÚKOLY`. FE: balík je vidět jen v **detailu POS** (ř.850–854) a v exportu. **Není** vidět přímo u zastávek v TourPlanu/Route Planneru. |
| 12 | **Priority** | Hotovo | **Částečně** | Ano | Ne | BE: `tasks.priority`, `pos_priority`, řazení balíku podle priority. FE: FORCE_INCLUDE priorita (`prio-form`) hotová; **priorita úkolů** se v UI jen zobrazuje, nedá se editovat mimo bulk import. |
| 13 | **Deadline** | Hotovo | **Částečně** | Ano | Ne | BE: `tasks.deadline`, urgency, `needsDedicated`, `task_bridge` (≤14 dní). FE: deadline se nastaví při bulk importu (`#bulk-deadline`), urgency se zobrazí; **chybí** deadline-orientovaný pohled. |
| 14 | **FORCE_INCLUDE / FORCE_EXCLUDE** | Hotovo | Hotovo | — | Ne | BE: `db_state._apply_priority/_apply_exclusions`, `task_bridge`, engine je čte. FE: TourPlan formuláře `#excl-form` (`/api/exclusions`), `#prio-form` (`/api/priority`). |
| 15 | **Analytika** | Hotovo | **Částečně** | Ano | Ne | BE: `team_analytics`, `route_analytics`, `trends`, `tech_trends`, `plan_reality`. FE: sekce Analytika (tým, den technika, plán vs realita, nově grafy techniků). Existuje, ale roztříštěné. |
| 16 | **Grafy techniků** | Hotovo | Hotovo *(nové)* | — | Ne | BE: `tech_trends.all_series()`, `/api/analytics/technicians/series` (~100 ms/30 techniků). FE: `initTechGraphs()`, multi-line graf + filtry (v61). |
| 17 | **KPI** | Hotovo | Hotovo | — | Ne | BE: `summary.py`, `team_analytics`. FE: dlaždice na Přehledu + Měsíční souhrn s deltami (viditelné na screenech). |
| 18 | **Exporty** | Hotovo | Hotovo | — | Ne | BE: MANAGER_PLAN `.xlsx` + sloupec `ÚKOLY`, `/api/versions/{id}/manager-plan`, `/api/draft/download`. FE: `downloadFile()`, tlačítka (ř.466, 964). |
| 19 | **Importy** | Hotovo | Hotovo | — | Ne | BE: `importer.py`, `auto_import.py`, `/api/import/{auto,{kind},template,sample}`. FE: drag-drop + šablony + explicitní typ + ukázková síť (v60). |
| 20 | **Reporty** | Hotovo | **Částečně** | Ano | Ne | BE: `summary.py`, `plan_reality.py`, `insights.py`. FE: Měsíční souhrn (bohatý, líbí se ti), plán vs realita. Chybí sjednotný „report center". |
| 21 | **Konfigurace** | Hotovo | **Částečně** | Ano | Ne | BE: `settings`, `cadence_config`, `model_config`, `business_rules`, `engine_config`, `task_types`. FE: Nastavení (kadence, model, technici, engine inventura, business pravidla). **Chybí** UI pro typy úkolů/kategorie. |

### Bonus (backend hotový, ve FE slabé/žádné)

| Funkce | Backend | Frontend | Důkaz |
|--------|---------|----------|-------|
| Predikce trvání | Hotovo | Částečně | `duration.py`, `/api/planner/duration/*`; FE jen v detailu POS (ř.848). Chybí přehled/rebuild UI. |
| Kapacita (učený standard) | Hotovo | **Chybí** | `capacity.py`, `/api/planner/capacity`; FE `/api/planner/capacity` = 0 volání. |
| Mikro-clustery | Hotovo | Částečně | `clustering.py`, `/api/planner/clusters/*`; FE jen v detailu POS (ř.849). |
| Segmenty & coverage | Hotovo | **Chybí/slabé** | `segments.py`, `/api/planner/segments`; FE `/api/planner/segments` = 0 volání. |
| GIS síťová mapa | Hotovo | Hotovo | `gis.py`, `/api/gis/network`; FE v Měsíčním souhrnu. |

---

## Objektivní zhodnocení

### Kolik % backendu je skutečně hotových
**~90 %** funkcionality, kterou jsme domlouvali, má funkční backend + endpoint,
ověřený (PoC prošel end-to-end na reálných datech, 115 endpointů, 57 modulů).
Skutečně **nedotažené v BE**: coverage kampaní podle activity-plan scope
(`task #59`, pending). Jinak je backend solidní.

### Kolik % frontendu odpovídá navrženému produktu
**Poctivě ~55–60 %.** Frontend volá 82 ze 115 endpointů, ale „volá" ≠ „dobře
zobrazuje". Proti **produktové vizi** (bohatý provozní cockpit z mockupů):
- **Hlavní obrazovka neodpovídá konceptu** — je to spíš stručný přehled než
  plný provozní cockpit (vytížení techniků, doporučení, plán tras chybí/slabé).
- **Úplně chybí v UI:** Historie POS, Kampaně (seznam), Kapacita, Segmenty/coverage.
- **Jen backend, ve FE slabé:** Bundling na zastávkách TourPlanu, správa typů
  úkolů/materiálů/kategorií, predikce trvání a clustery (jen v detailu POS).

### Co lze dokončit POUZE doprogramováním frontendu
**Naprostá většina.** Vše výše má hotový backend + endpoint — chybí obrazovka:
- Historie POS (endpoint `/api/pos/{id}/history` existuje)
- Kampaně (`/api/campaigns`)
- Kapacita (`/api/planner/capacity`)
- Segmenty & coverage (`/api/planner/segments`)
- Bundling viditelně u zastávek TourPlanu (`plan_io` už balík přidává)
- Správa typů úkolů/kategorií (`/api/planner/task-types`)
- Obohacení hlavního cockpitu (health/insights/team endpointy existují)
- Detail technika + den + odchylky dotáhnout a propojit s grafy

### Co bude ještě vyžadovat zásah do backendu
**Málo:**
- Coverage kampaní podle activity-plan scope (`task #59`) — dodělat logiku.
- (Volitelně) drobné agregační endpointy pro bohatší cockpit, kdyby stávající
  `insights/team` nestačily na konkrétní dlaždice.

---

## Shrnutí jednou větou
Backend je z ~90 % hotový a ověřený; frontend odpovídá navržené vizi asi z 55–60 %
a **hlavní obrazovka neodpovídá mockupům**. Drtivá většina zbytku je „jen
doprogramovat UI nad existujícími endpointy", jen minimum vyžaduje zásah do backendu.
