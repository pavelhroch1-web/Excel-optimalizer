# Tour Planning — kompletní audit + finální návrh po obrazovkách

Cíl: 100% dokončit Tour Planning tak, aby využil **veškeré** schopnosti
backendu a byl nejlepší možnou aplikací pro tvorbu týdenních plánů. Bez nové
business logiky. Vše ověřeno proti běžícímu backendu.

---

## Část A — Audit: schopnost backendu × využití v Tour Planningu

Legenda: **ANO** plně v Planneru · **ČÁST** částečně / jinde · **NE** backend
umí, Planner nevyužívá.

| Schopnost | Backend endpoint | V TP? | Kde / co chybí |
|---|---|---|---|
| Plánovací pravidla (business) | `/api/rules/business` | NE | jen v Nastavení, ne v Planneru |
| Filtry terminál/partner/kategorie | `/api/model` | ČÁST | Wizard řeší jen partnery; terminály+kategorie ne |
| Kadence | `/api/cadence` | ANO | Wizard |
| Priority / override (FORCE IN/EX) | `/api/priority`,`/exclusions`,`/reassignments` | ANO | fáze Úpravy |
| Partnerství (markety) | `/api/model` partners | ANO | Wizard |
| **Segmenty + riziko kadence** | `/api/planner/segments` | **NE** | cadence-cíl, business_weight, min_coverage, riziko — nevyužito |
| Kampaně | `/api/campaigns` + ACTIVITY_PLAN | ČÁST | chipy v okně; chybí stav dokončení kampaně v review |
| Kapacita | `visits_per_tech_week` | ANO | Wizard |
| **Learned capacity** | `/api/planner/capacity` | ANO | Wizard (nově) |
| **Forward sweep** | `/api/planner/sweep` | ANO | Wizard (nově) |
| **Advisor** | `/api/planner/advise` | ANO | Wizard kontrola (nově) |
| Planner assess | `/api/planner/assess` | ANO | Wizard/scénáře |
| **Planner unserved** | `/api/planner/unserved` | ANO | Review (nově) |
| Duration model | `/api/planner/duration` | ČÁST | ve feasibility; ne per-POS u řádku návrhu |
| Travel model | `travel_model` | ANO | feasibility |
| **Clustering (micro-clustery)** | `/api/planner/clusters` | **NE** | 1921 clusterů; návrh je nesdružuje |
| **GIS / mapa plánu** | `/api/gis/network`,`/gis/pos` | **NE** | mapa je jen v Dashboardech, ne v Planneru |
| Route planning | `/api/planner/route` | ČÁST | karta v Publish, ne vizuál trasy |
| Review | `/api/draft` | ANO | fáze Review |
| Publish | `/api/publish` | ANO | fáze Publish |
| Export | `/api/draft/download` | ANO | tlačítko |
| **Historie běhů** | `/api/history/planner-runs`, `/memory/planner-run` | **NE** | běhy s config fingerprintem — nevyužito v Planneru |
| **Memory / config-diff** | `/api/memory/config-diff` | **NE** | „co se změnilo od minula" — nevolá se |
| **Audit změn** | `/api/history/events` | **NE** | timeline (import/publish/config/run) — nevolá se |
| **Skutečnost vs plán** | `/api/reality/fulfillment` | **NE** | v detailu technika; v Planneru chybí retrospektiva |
| SalesApp data | (podklad) | ANO | napříč |
| **Historické / GPS trasy** | `/api/gis/technician/{}/day`, `/route/actual` | **NE** | detail technika; ne v Planneru |
| Kilometráž / čas na POS / přejezdů | `plan_feasibility`, `route_actual` | ČÁST | agregát ve feasibility; ne vizuál per technik v Planneru |
| Efektivita OZ / obchodníků | `team_analytics`, `diagnostics` | NE | v Analytice; ne v Planneru |
| Vizualizace tras | `gis`, `_tdDaySeq` | NE | detail technika; ne v Planneru |

**Souhrn:** silně nevyužité v Tour Planningu jsou **segmenty (riziko kadence),
clustering, GIS/vizualizace tras v plánu, historie běhů, audit, memory/config-diff
a celá retrospektiva (plán vs skutečnost)**. Vše hotové v backendu.

---

## Část B — Pokrytí 10krokového workflow

| Krok | Stav | Backend | Poznámka |
|---|---|---|---|
| 1. Nastavení plánu | ANO | Wizard | doplnit terminály+kategorie do filtrů |
| 2. Kontrola vstupních dat | ČÁST | data-health, segments, freshness | chybí pre-flight kvality dat + riziko sítě |
| 3. Generování | ANO | `generate-runtime` | — |
| 4. Review | ANO | `/api/draft` | — |
| 5. Ruční úpravy | ANO | add/remove/change + override | — |
| 6. Kontrola kvality | ČÁST→ANO | feasibility+unserved+advisor+candidates | roztříštěné, sjednotit |
| 7. Publish | ANO | `/api/publish` | — |
| 8. Export | ANO | `/api/draft/download` | — |
| **9. Retrospektiva** | **NE** | reality, tech_score, history/planner-runs | v Planneru chybí |
| **10. Plán vs skutečnost** | **NE** | `/api/reality/fulfillment` | v Planneru chybí |

**Největší mezera:** kroky **9–10** (a pre-flight dat, krok 2) — backend hotový,
Tour Planning je nenabízí. Bez nich Tour Planning nekončí, jen generuje.

---

## Část C — „Proč" transparence (pro skutečného plánovače)

| Otázka plánovače | Odpověď v backendu | Kde dnes |
|---|---|---|
| Proč engine něco udělal | `candidates` — rozpad skóre + reason tags | Review (ANO) |
| Proč něco nevyšlo | `unserved` — důvod enginu | Review (ANO) |
| Proč je den přetížený | `feasibility` — čas na POS + přejezd vs hodiny | Review (ANO) |
| Proč technik nestíhá | `feasibility` per-tech + `team_analytics` loadPct/overdue | ČÁST — nepropojené |
| Proč POS zůstaly mimo plán | `unserved` (kapacita/hold-back/rozestup/filtr) | Review (ANO) |
| Jaké kompromisy engine udělal | `advisor` (nejslabší článek, binding constraint) + unserved | ANO |

Většina „proč" odpovědí **existuje** — jen je potřeba je mít **pohromadě v
Review cockpitu** a propojit „technik nestíhá" (feasibility) s jeho vytížením
(team_analytics).

---

## Část D — Finální podoba Tour Planningu, obrazovka po obrazovce

Šest obrazovek = 10 kroků workflow. Každá: účel · backend · zjednodušit ·
chybí · schované · viditelné.

### 1) Start / Přehled plánu
- **Účel:** kde uživatel je — poslední plán, čerstvost dat, jedno tlačítko
  „Vytvořit plán".
- **Backend:** `/api/status`, `/api/data/summary`, `/api/history/planner-runs` (poslední běh).
- **Zjednodušit:** místo 18 karet jedna vstupní obrazovka se 3 fázemi.
- **Chybí:** poslední plánovací běh (kdy, jaký mód, kolik naplánováno) — z
  `planner-runs`, dnes nevyužito.
- **Schované:** historie běhů.
- **Viditelné:** velké „Vytvořit plán", stav dat, poslední běh.

### 2) Kontrola vstupních dat (krok 2)
- **Účel:** než plánuju, je vstup v pořádku? A kde je síť v riziku?
- **Backend:** `data-health`/`_freshness`, **`/api/planner/segments`** (riziko
  kadence per segment: kolik po termínu, business_weight, min_coverage),
  `/api/campaigns` (aktivní kampaně v okně).
- **Zjednodušit:** jeden panel „stav vstupu + riziko sítě".
- **Chybí:** **segmentové riziko** (backend hotový, nevyužité) — „Velké terminály:
  22 % po termínu, cíl kadence 3 týdny".
- **Schované:** segmenty úplně.
- **Viditelné:** čerstvost feedů + top rizikové segmenty před generováním.

### 3) Průvodce plánem (kroky 1+3) — jediná hlavní cesta
- **Účel:** nastavit obchodní pravidla, backend rozhodne.
- **Backend:** technici `/api/technicians`, **filtry `/api/model` (doplnit
  terminály+kategorie)**, kadence `/api/cadence`, kapacita + **learned
  `/api/planner/capacity`** + **sweep `/api/planner/sweep`**, priorita
  (strategy-modes), pre-flight **`/api/planner/advise`**, generování
  `generate-runtime`.
- **Zjednodušit:** filtry inline (žádný skok do Nastavení); zrušit soupeřící
  karty „Strategie/Simulace/Generovat".
- **Chybí:** terminály+kategorie ve filtrech (dnes jen partneři).
- **Schované:** —
- **Viditelné:** sweep „při 40 pokryješ X, síť za Y týdnů" + advisor verdikt u tlačítka.

### 4) Review cockpit (kroky 4+6) — vše o vygenerovaném plánu na jednom místě
- **Účel:** je plán dobrý a proč engine rozhodl takto?
- **Backend:** návrh `/api/draft`, **coverage+verdikt `/api/planner/advise`**,
  **čas `/api/plan/feasibility`**, **kritické POS `/api/planner/unserved`**,
  **kandidáti `/api/draft/candidates`**, **clustering `/api/planner/clusters`**
  (sdružené POS), **mapa `/api/gis/network`** (plán na mapě).
- **Zjednodušit:** 7 stohovaných karet → záložky: Návrh · Pokrytí & verdikt ·
  Čas & vytížení · Kritické POS · Kandidáti (proč) · Mapa.
- **Chybí:** **mapa plánu** (GIS umí, Planner nevyužívá), **cluster-hint**
  („3 POS v jednom centru"), propojení „technik nestíhá" (feasibility) s jeho
  vytížením (team_analytics).
- **Schované:** GIS, clustering.
- **Viditelné:** 6 „proč" odpovědí (viz Část C) přímo v záložkách.

### 5) Ruční úpravy (krok 5)
- **Účel:** dořešit výjimky, které engine nemohl znát.
- **Backend:** `/api/draft/add-pos`,`/remove-pos`,`/change-technician`,
  `/api/priority`,`/exclusions`,`/reassignments`.
- **Zjednodušit:** úpravy přímo z tabulky Návrhu v Review (inline), ne
  samostatná fáze.
- **Chybí:** po úpravě přepočet feasibility/unserved (dnes se rozejde).
- **Schované:** —
- **Viditelné:** dopad úpravy na čas dne hned vedle řádku.

### 6) Publikace & Export (kroky 7+8) + Vyhodnocení (kroky 9+10)
- **Účel:** zmrazit plán, exportovat — a **po týdnu vyhodnotit vs skutečnost**.
- **Backend:** publish `/api/publish`, export `/api/draft/download`, historie
  `/api/versions`; **retrospektiva `/api/reality/fulfillment`** (plán vs
  skutečnost: splněno/skluz/nesplněno per technik), **`/api/reality/technicians`**,
  Technician Score/SLA, **historické trasy `/api/gis/technician/{}/day`**,
  **historie běhů `/api/history/planner-runs`**, **audit `/api/history/events`**,
  **config-diff `/api/memory/config-diff`**.
- **Zjednodušit:** po publikaci nabídnout „Vyhodnotit minulý plán" jako přirozený
  konec cyklu, který krmí příští plánování.
- **Chybí:** **celá retrospektiva v Planneru** — plán vs skutečnost, kdo nestíhal,
  které POS se opakovaně minuly, porovnání běhů, audit. Vše backend umí.
- **Schované:** reality, planner-runs, events, config-diff, GPS trasy.
- **Viditelné:** „minulý plán splněn na X %" jako vstupní signál pro krok 1.

---

## Priorita dokončení (nejvyšší hodnota / nejnižší riziko)

Vše jen zapojení existujících endpointů, žádná nová logika:

1. **Review cockpit** — sloučit 7 review karet do záložek + doplnit **mapu plánu
   (GIS)** a **cluster-hint**. (přeskupení + 2 existující endpointy)
2. **Retrospektiva / plán vs skutečnost** (kroky 9–10) — nová záložka
   „Vyhodnocení" z `reality/fulfillment` + Technician Score + GPS trasy. Uzavře
   plánovací smyčku. (jen zapojení)
3. **Kontrola vstupních dat + segmentové riziko** (krok 2) — panel z
   `segments` + data-health před generováním.
4. **Filtry ve Wizardu kompletně** (terminály+kategorie z `/api/model`) +
   zrušit soupeřící karty Strategie/Simulace/Generovat.
5. **Historie běhů + audit** — z `planner-runs` a `history/events` (backend
   hotový, dnes nevolané).

Po těchto pěti krocích Tour Planning pokrývá **všech 10 fází workflow** a
zpřístupní **veškeré relevantní schopnosti backendu** (segmenty, clustering,
GIS, reality, historie, audit), přičemž každý „proč" má odpověď přímo v Review.
Žádný nový algoritmus — jen nejlepší možný produkt nad tím, co backend už umí.
