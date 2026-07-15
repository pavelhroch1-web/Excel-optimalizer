# TourPlan Planner — Architektura

> **Stav:** v0.1 — návrh k připomínkování. Vznikl ze společné diskuze
> (management + engineering). Slouží jako závazný podklad **před** implementací.
> Kód se začne psát až po schválení tohoto dokumentu.

---

## 0. Filozofie — co stavíme a co ne

Nestavíme optimalizátor tras. Stavíme systém, který **přemýšlí jako zkušený
regionální manažer**: ví, co je obchodně nejdůležitější, postaví logický den
kolem povinných návštěv, volnou kapacitu využije na nejhodnotnější okolní POS a
vytvoří TourPlan, který jde reálně odjet v pracovní době.

Tři věty, které drží celý návrh:

1. **Neoptimalizujeme kilometry.** Kilometry a jízdní časy jsou pouze *kontrola
   proveditelnosti* — ověřují, že den jde odjet a tvoří jeden souvislý okruh.
   Nejsou cílem.
2. **Nejdřív CO, potom JAK.** Nejdřív se spočítá obchodní hodnota a povinnosti
   (co má smysl dělat), teprve pak se skládá den (jak to poskládat do okruhu).
3. **Publikovaný plán je závazný.** Nové vstupy nikdy automaticky nepřepíšou
   publikovaný TourPlan — mohou jen vytvořit nový návrh, který potvrdí člověk.

### Akademické zařazení
Formálně jde o **Team Orienteering Problem s časovými okny a sběrem odměn**
(prize-collecting) — *nemusíš* navštívit všechno, vybíráš nejhodnotnější
podmnožinu, která se vejde do reálného dne. Řešíme ho ale **vysvětlitelnou
heuristikou „jako manažer"**, ne exaktním solverem (viz §12).

---

## 1. Rozhodnutá pravidla (výstup diskuze)

| # | Rozhodnutí |
|---|------------|
| **Území** | Každý technik plánuje **pouze svoje vlastní POS** (`pos_master.technician`). Planner mezi techniky nic nepřehazuje. Přesuny jsou manažerské rozhodnutí mimo engine. |
| **Priorita** | Vzniká z kombinace všech business vstupů (PPT, kadence, must-visit, kampaně, historie SalesApp, další existující pravidla) — ne jen z PPT. Business logiky už v systému existují, znovu se nevymýšlejí. |
| **Učení** | **Kolektivní** — z dat celé ČR, ne z jednotlivce. Cíl = běžný až mírně nadprůměrný technik, **bez extrémů**. Personalizace regionu/technika až při dostatku kvalitních dat, nikdy ne jako základ. |
| **Predikce trvání** | Stejná filozofie — z historie celé ČR (typ POS × řetězec × druh práce). Výchozí model = běžný až mírně nadprůměrný technik. |
| **Kotva dne** | **První povinný POS** (typicky GECO dle PPT / must-visit), ne oblast. Kolem něj se staví okruh. |
| **Horizont** | Jeden plán pro **celé plánovací období** (konfigurovatelné: 2 / 3 / 4 týdny, default ~měsíc). Povinnosti se rozloží do dní. Delší den není problém; cíl = splnit všechny povinnosti v rámci celého období. |
| **Publikace** | Publikovaný TourPlan je **immutable**. Nová data → jen nový draft. Žádné automatické přepočítání. |
| **AI** | Žádná závislost na LLM. Desktop, běží samostatně. ML jen volitelně pro **predikce** (trvání, odhad hodnoty). Rozhodovací logika **deterministická, transparentní, auditovatelná**. |

---

## 2. Vstupy

**Business vstupy (od uživatele / konfigurace):**
- PPT hodnoty každého POS,
- Activity Plan (kampaně, jejich týdenní okna),
- konfigurační parametry (váhy priority, délka horizontu, pracovní doba…),
- stávající business pravidla (kadence GECO/CORN, must-visit, kategorie…).

**Operační data (SalesApp, už v systému):**
- poslední návštěva, historie návštěv,
- reálné trvání návštěv, GPS průběh,
- historický výkon, další operační data.

Vstupy se **nemění** — jsou to zdroje. Vše ostatní je odvozené.

---

## 3. Vrstvy A–G (+ E0)

```
Vstupy (PPT, Activity Plan, config, business rules)
   +  SalesApp historie (poslední návštěva, trvání, GPS, výkon)
                     │
        ┌────────────┴─────────────┐
        ▼                          ▼
[A] Vrstva učení            [B] Vrstva pravidel
  - predikce trvání           (STÁVAJÍCÍ engine — respektujeme)
  - kolektivní priory          must-visit, kadence GECO/CORN…
        │                          │
        └────────────┬─────────────┘
                     ▼
[C] Business priorita každého POS  (0–100, plně vysvětlitelná)
                     ▼
[D] Mikro-clustering  (stejné centrum / pár desítek metrů = 1 jednotka)
                     ▼
[E0] Kostra období    (rozřež POVINNÉ POS do dní tak, aby každý den
                       tvořil souvislý okruh a všechny povinnosti se stihly)
                     ▼
[E] Stavba dne        (kotva = první povinný POS → okruh → dofill kapacity)
                     ▼
[F] Kontrola / publikace   (immutable — už máme lifecycle)
                     ▼
[G] Měření reality    (plán vs realita, gapy, near-missed — už máme)
                     ▲──────── zpětná vazba jen do [A], kolektivně ────────┘
```

Každá vrstva se dá ladit a testovat samostatně. **[C] rozhoduje CO, [E0]+[E]
rozhodují JAK to rozložit do období a dní.**

---

## 4. [C] Business priorita POS

Aditivní, transparentní skóre (žádný black-box) — manažer musí u každého POS
vidět *proč*:

```
priorita(POS) =  w_ppt   · PPT_norm
              +  w_kad   · urgence_kadence        (týdny po termínu vs pravidlo)
              +  w_must  · must_visit             (tvrdé pravidlo → prakticky ∞)
              +  w_kamp  · kampaň_aktivní         (Activity Plan okno běží teď)
              +  w_vis   · visibilita_dluh        (náběh kampaně nesplněn)
              +  w_hist  · potenciál              (historický výkon POS)
              −  penalizace                       (např. nedávno navštíveno)
```

- Váhy `w_*` **konfigurovatelné z UI** (využijeme stávající config-overlay vzor).
- Jednotlivé složky = **existující business logiky** (§1) — nevymýšlejí se znovu.
- Výstupem je i **rozhodovací zdůvodnění** (uloží se, viz §14) pro auditovatelnost.
- Povinné POS (must-visit, kadence po termínu, běžící kampaň) dostanou tak
  vysoké skóre, že vždy vytvoří **kotvu** — vstup do [E0].

> **Otevřené (doptat se u konkrétních pravidel):** přesné prahy kadence per
> kategorie a přesná definice „must-visit" napojíme na stávající `business_rules`
> — kde bude pravidlo nejednoznačné, ověříme na konkrétním POS.

---

## 5. [A] Kolektivní učení (ne z jednotlivce)

Model reprezentuje **kolektivní zkušenost celé firmy**, cílí na běžného až mírně
nadprůměrného technika a **ignoruje extrémy**.

**Metoda — empirical Bayes / shrinkage s ořezem extrémů:**

```
1. Ořež extrémy: z rozdělení vyhoď dolní i horní chvost
   (lajdáci i nereálně rychlí) — např. winsorizace na [p10, p90].
2. Cíl = ~p50–p60 ořezaného rozdělení  → „běžný až mírně nadprůměrný".
3. Shrinkage k nadřazené úrovni, dokud není dost dat:
     odhad = (n·vlastní + k·nadřazený) / (n + k)
   Hierarchie priorů:  ČR → typ POS → řetězec → region → (technik).
```

- Málo dat → věříš národnímu / typovému průměru.
- Dost **kvalitních** dat → jemná personalizace regionu/technika (nikdy ne základ).
- **Technik jako úroveň učení je poslední a volitelný** — přesně aby se
  nekopírovaly zlozvyky jednoho člověka.

> **Poznámka k feedback-loopu:** protože se učíme *kolektivně* z reality (ne z
> plánu, který jsme sami vynutili) a publikovaný plán je immutable, riziko
> zakonzervování je nízké. Případný malý **explorační rozpočet** (plánovat občas
> nejistý, ale potenciálně hodnotný POS) necháváme jako *volitelný, konfigurovatelný*
> doplněk — ne jako jádro.

---

## 6. [A] Predikce trvání návštěvy

- Vstup do **kapacity dne** — kolik práce se reálně vejde.
- Predikce z historie **celé ČR** podle (typ POS × řetězec × druh práce), stejná
  shrinkage hierarchie a ořez extrémů jako v §5.
- **Kvantily, ne průměr:** plánuj na **p50**, kapacitní rezervu drž na **p75**
  (trvání je pravostranně zešikmené — průměr by den přeplňoval).
- Důsledek přesně dle zadání: kde GECO trvá 5–7 min, vejde se jich víc; kde jiný
  typ trvá 35 min, kapacita to automaticky zohlední.

---

## 7. [D] Mikro-clustering

- **Mikro-cluster** = POS ve stejném obchodním centru / do několika desítek metrů
  → **jedna logická jednotka**. Plánují se vždy spolu (téměř nulový mezizastávkový
  čas). Předpočítané a uložené (viz §14).
- Pravidlo „už tam jedu, co ještě přibalím?" je zabudované na dvou úrovních:
  mikro-cluster (pěší docházka) a dofill dne (blízké vysoko-hodnotné POS v [E]).

---

## 8. [E0] Kostra plánovacího období

Kritická vrstva. Bez ní by se dny stavěly nezávisle a povinné POS (které nechodí
hezky pohromadě) by buď roztrhaly dny, nebo zůstaly nesplněné.

```
1. Vezmi VŠECHNY povinné POS technika za celé období.
2. Geograficky je slož do denních kotev (blízké povinnosti = stejný den).
3. Přiřaď kotvy jednotlivým dnům období tak, aby:
      - každý den tvořil jeden souvislý, objetelný okruh,
      - všechny povinnosti se stihly v rámci celého období,
      - respektovala se týdenní/denní okna kampaní.
```

Toto je „kostra týdne/období", kterou zkušený manažer dělá v hlavě: *tyhle tři
na severu spojím v úterý, tyhle dva na jihu ve čtvrtek.* Délka jednotlivých dní
může kolísat — důležité je splnění povinností v období.

---

## 9. [E] Stavba dne kolem povinné kotvy

```
1. Kotva dne = první povinný POS dne (z [E0]).
2. Postav logický okruh kolem kotvy (a případných dalších povinností dne).
3. Dofill volné kapacity nejhodnotnějšími OKOLNÍMI POS
   (priorita [C] / vložený čas), vždy přibal mikro-clustery [D].
4. Plň do p75 kapacity, ne přes.
5. Kontrola proveditelnosti přes OSRM (reálné silniční časy) — den musí být
   jeden souvislý okruh odjetelný v pracovní době.
```

- **Greedy podle poměru hodnota / vložený čas** — chová se jako manažer, ne jako
  solver.
- U každého POS je vidět zdůvodnění (kotva / dofill / mikro-cluster).
- km se **neminimalizují** — jen se kontroluje souvislost a proveditelnost okruhu.

---

## 10. Horizont a kapacita

- **Horizont = celé plánovací období**, konfigurovatelné (2 / 3 / 4 týdny, default
  ~měsíc). Ne týdenní přeplánování.
- Kapacita dne = pracovní doba − predikovaná jízda (OSRM) − predikované trvání
  návštěv (p50, rezerva p75).
- Delší den je OK; metrika úspěchu = **splnění všech povinností v období**, ne
  rovnoměrnost dní.

---

## 11. Životní cyklus a immutabilita publikace

```
Draft → Optimalizace → Kontrola → Publikace → Monitoring → Nový Draft
                                      │
                                      ▼
                          IMMUTABLE (nikdy se nepřepisuje)
```

- Publikovaný TourPlan = **závazný, neměnný pracovní plán**. Jakmile je publikován
  technikovi, nové PPT ani nová data ze SalesApp ho **nikdy** automaticky nezmění —
  za žádných okolností.
- Planner po publikaci pouze: hlídá plnění (plan vs. realita, už máme), upozorňuje
  na nové povinnosti/rizika, a **na vyžádání připraví nový Draft TourPlanu**.
- O publikaci nové verze **vždy rozhoduje uživatel**. Nový Draft nikdy nepřepíše
  běžící publikovaný plán — vzniká vedle něj jako nová verze. (Navazuje na stávající
  immutable snapshot + `plan_lifecycle` — publikace je append-only.)

---

## 12. Determinismus, transparentnost, auditovatelnost

- Rozhodovací jádro ([C], [D], [E0], [E]) je **deterministické a vysvětlitelné** —
  žádný black-box na výběr a stavbu. Manažer musí plánu důvěřovat a rozumět mu.
- **ML jen jako volitelná predikce** ([A]: trvání, odhad hodnoty). I bez ML musí
  planner fungovat (fallback na jednoduché kolektivní statistiky).
- **Žádná závislost na LLM / cloudu.** Desktop, běží samostatně. (OSRM je
  volitelný — vlastní/hostovaný; bez něj fallback na odhad času, viz stávající
  `travel_model` + cache `route_geometry`.)

---

## 13. Vztah ke stávajícímu enginu

- Stávající ověřený Planning Engine (Python port, testy 120/0) zůstává jako
  **vrstva pravidel [B]** — tvrdá business pravidla, must-visit, kadence.
- Nová **rozhodovací vrstva** ([C]–[E]) stojí **nad ním**, čte jeho pravidla,
  nepřepisuje jeho logiku. Čistá separace „pravidla" vs. „hodnotové rozhodování".
- Vše přes stávající config-overlay vzor — engine se nemodifikuje, jen konfiguruje.

---

## 14. Datový model (nové append-only tabulky)

| Tabulka | Účel |
|---------|------|
| `duration_model` | Hierarchické odhady trvání (p50/p75) per typ × řetězec × region × (tech), přepočítávané z historie. |
| `pos_priors` | Naučené hodnotové priory + objem/jistota dat (pro shrinkage). |
| `pos_clusters` | Předpočítané mikro-clustery (stejné centrum / pěší docházka). |
| `plan_rationale` | Proč byl každý POS vybrán (kotva / dofill / cluster) — vysvětlitelnost + audit. |
| (rozšíření [G]) | Vazba realizované hodnoty návštěvy zpět do učení (připravit teď, plnit později). |

Vše append-only, v souladu se zbytkem operační paměti.

---

## 15. Mapa jako hlavní pracovní nástroj

- Klasický **OSM podklad** (města, obce, silnice) — už nasazeno.
- Hover/klik na POS → okamžitě název, město, adresa, řetězec, plán vs. realita a
  další základní info — už nasazeno (rich popup + POS karta).
- Plánovací režim nad mapou (návrh dne, přesuny, ruční doladění) — dostaví se
  během implementace planneru.

---

## 16. Rizika a otevřené otázky

1. **Definice povinnosti / prahy kadence** — napojit přesně na `business_rules`;
   doptat se u nejednoznačných případů (per kategorie / řetězec).
2. **Přetečení povinností v období** — planner má **primárně rozplánovat všechny
   povinnosti v rámci celého horizontu** (proto plánujeme na celé období, ne po
   dnech). Automatický posun **není** výchozí chování. Teprve když skutečně
   neexistuje proveditelné řešení, planner navrhne změnu nebo upozorní manažera —
   nikdy nerozhodne sám.
3. **Kvalita GPS / párování POS** — ~71 % návštěv má GPS; učení a clustering na
   tom stojí. Průběžně zlepšovat pokrytí.
4. **Realizovaná hodnota** — v1 běží na proxy (PPT + recency + pravidla); datový
   model připravit na pozdější učení z výsledků (prodej / instalace visibility).

---

## 17. Fázování implementace (návrh)

1. **[A] Predikce trvání** — kolektivní model p50/p75 + hierarchie. (Samostatně
   testovatelné, hned viditelná hodnota v kapacitě.)
2. **[D] Mikro-clustering** — předpočet clusterů.
3. **[C] Business priorita** — skóre + zdůvodnění nad existujícími pravidly.
4. **[E0] Kostra období** — rozvržení povinností do dní.
5. **[E] Stavba dne** — kotva + okruh + dofill + OSRM proveditelnost.
6. **[F] Lifecycle** — draft → publikace (immutable) → monitoring → nový draft.
7. **Mapa** — plánovací režim nad GIS vrstvou.

Každá fáze má vlastní testy a nechává engine pravidel [B] beze změny.

---

*Konec v0.1. Připomínkuj přímo v tomto dokumentu — po schválení začneme fází 1.*
