# TourPlan Planner — Architektura

> **Stav:** v0.2 — schválená architektura, Fáze 1 (predikce trvání) hotová.
> Vznikl ze společné diskuze (management + engineering). Slouží jako závazný
> podklad pro implementaci; doplňuje se s každým dalším rozhodnutím.

---

## 0. Filozofie — co stavíme a co ne

Nestavíme optimalizátor tras. Stavíme systém, který **přemýšlí jako zkušený
regionální manažer**: ví, co je obchodně nejdůležitější, postaví logický den
kolem povinných návštěv, volnou kapacitu využije na nejhodnotnější okolní POS a
vytvoří TourPlan, který jde reálně odjet v pracovní době.

**Technici = objednaná dodavatelská kapacita.** Z pohledu businessu si objednávám
kapacitu (technik-dny) u agentury. Zda někdo dnes odpracoval 4 h a jiný 10 h je
odpovědnost **agentury, ne planneru** — planner neoptimalizuje chování
jednotlivců. Planner řeší: **jak s objednanou kapacitou obsloužit co nejvyšší
obchodní hodnotu sítě**, jestli kapacita stačí na cílovou kadenci, kde hrozí
neobsloužené segmenty a kolik kapacity žádá konkrétní kampaň.

**TourPlan není cíl sám o sobě.** Je to *výsledek manažerského rozhodnutí* o tom,
jakou část sítě chceme v daném období obsloužit při dostupné objednané kapacitě
(vrstva [S] níže).

**Optimalizační cíl:** ne využití hodin, ne počet návštěv, ale **co nejvyšší
obchodní hodnota obsloužené sítě při dostupné kapacitě.** Kapacita je *omezení*,
ne cíl. Business Gain, PPT, kadence, geografie, predikce trvání a naučená
kapacita společně určují nejlepší plán.

**Filozofie:**
- **historie** říká, co je *možné* (přesné odhady reality),
- **business pravidla** říkají, co je *důležité*,
- **planner** určuje, *kam se chceme dlouhodobě posouvat* — plánuje mírně
  ambiciózněji než historie, aby dlouhodobě zvyšoval produktivitu a pokrytí,
  ale nikdy ne nereálně nebo demotivujícím způsobem.

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
| **OZ mimo plánování** | Plánovací vrstvy pracují **pouze s techniky**. OZ se v plánování vůbec nevyskytují — jsou jen v **reportovacím režimu**, kde je lze zapnout/vypnout. |
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
[S] COVERAGE & CAMPAIGN PLANNING  (strategie, Velín — rozhoduje SE PRVNÍ):
      - které segmenty/partnery obsloužit / vyloučit
      - cílová kadence per segment · předpověď rizika neobsloužení
      - simulace kampaně · potřebná vs. objednaná kapacita (feasibility)
      → výstup = ROZSAH + cíle, které řídí taktické plánování níže
                     ▼
[C] Business priorita každého POS  (0–100, plně vysvětlitelná)
                     ▼
[D] Mikro-clustering  (stejné centrum / pár desítek metrů = 1 jednotka)
                     ▼
[M] Manažerský pre-load  (PŘED optimalizací):
      - rezervace kapacity (schůzky, školení, inventura, admin, „30 % volné")
      - ruční fixní úkoly (POS, priorita, odhad trvání, časové okno)
                     ▼
[E0] Kostra období    (rozřež POVINNÉ POS + ruční úkoly do dní tak, aby každý
                       den tvořil souvislý okruh, vešel se do VOLNÉ kapacity
                       (po rezervacích) a všechny povinnosti se stihly)
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

## 3b. [S] Coverage & Campaign Planning (strategická vrstva, Velín)

**Rozhoduje se jako první.** Tady manažer rozhodne, *jakou část sítě* chce v
období obsloužit — a systém nejen řekne, jestli na to objednaná kapacita stačí,
ale **aktivně navrhuje obchodní strategii a trade-offy**. Teprve výstup (rozsah +
cíle) řídí taktické plánování [C]–[E]. TourPlan je až výsledek tohoto rozhodnutí.

**A) Segmentový model (konfigurovatelný z Velínu, žádná pravidla v kódu)**
- Segment = konfigurovatelná kombinace existujících dimenzí: typ terminálu
  (velký / malý / B), kategorie (LI, GECO…), partner / řetězec, region…
- Definice segmentů, jejich cílová kadence a priorita se nastavují v administraci,
  protože se mění podle kampaní, partnerů a obchodní strategie.

**B) Coverage stav a riziko**
- **Poslední návštěva podle segmentů** (B terminály, malé terminály, LI, partneři…).
- **Předpověď, kdy segment vypadne z cílové kadence** (z poslední návštěvy +
  cílové kadence segmentu → časová osa rizika neobsloužení).
- Kde vzniká riziko neobsloužených segmentů.

**C) Simulace kampaně**
- Manažer vybere rozsah, např. *„na Vánoce objet velké + malé terminály bez LI"*.
- Systém spočítá **potřebnou kapacitu** a porovná s **objednanou**:

```
poptávka(rozsah)  =  Σ_segment  (počet POS × návštěv/období dle kadence
                                 × predikované trvání [Fáze 1]  +  přejezdy)
nabídka           =  objednané technik-dny × naučená denní kapacita [standard]
feasibility       =  poptávka ≤ nabídka ?
```
- Pokud objednaná kapacita nepokryje zvolený rozsah → **upozornění + velikost
  mezery** (kolik kapacity chybí / co vyřadit / o kolik navýšit objednávku).

**D) Výběr rozsahu z Velínu**
- Zahrnout / vyloučit segmenty, partnery, kategorie.
- Výstup vrstvy [S] = **množina POS v rozsahu + cílová kadence + priority kampaní**,
  které vstupují do [C] (priorita) a [E0] (kostra období).

**E) Strategický poradce / trade-off (rozhodovací podpora, ne jen reporting)**

[S] aktivně upozorňuje a nabízí varianty. Vše jsou **deterministické scénáře nad
modelem feasibility** (poptávka vs. nabídka) — žádná AI:

- *„B terminály nebyly navštíveny 95 dní."* — coverage stav + kadence-riziko.
- *„Malé terminály začínají vypadávat z cílové kadence."* — předpověď rizika.
- *„Vánoce, celá síť kromě LI: potřebuješ 6 týdnů při současné kapacitě, nebo
  5 týdnů při +15 % kapacity."* — invertuj feasibility: řeš délku kampaně při dané
  kapacitě, nebo potřebnou kapacitu při dané délce.
- *„Vyloučíš-li partnera X, uvolníš kapacitu na Y dalších prioritních POS."* —
  delta feasibility po vyřazení segmentu → přepočet, co se vejde navíc (dle [C]).
- *„Zvýšíš-li kadenci segmentu A, segment B už nepůjde udržet."* — přepočet
  poptávky při změně kadence → které jiné segmenty přestanou být feasible.

Poradce běží jako **what-if nad stejným výpočtem** [S]: mění jeden parametr
(rozsah / kadenci / kapacitu / délku) a ukazuje důsledek na zbytek sítě. Manažer
tak dostává **rozhodovací varianty**, ne jen „stihneme / nestihneme".

---

## 3c. Vše konfigurovatelné z Velínu — generic engine, strategie v konfiguraci

**Princip:** kód je **generický engine**; obchodní strategie **žije v konfiguraci**.
Prakticky všechny business parametry se nastavují z administrace (Velínu), ne
v kódu — aby šlo systém dlouhodobě škálovat bez zásahů do implementace. Změna
konfigurace = planner začne optimalizovat podle nové strategie.

Konfigurovatelné z Velínu:
- **definice segmentů** (typ terminálu / kategorie / partner / region),
- **cílové kadence** per segment,
- **priority segmentů**,
- **zahrnout / vyloučit partnery / segmenty**,
- **obchodní kampaně** (okna, rozsah),
- **minimální požadované pokrytí** (floor, který planner musí udržet),
- **ambice planneru** (capacityAmbitionPct, cílový percentil),
- **business váhy** (w_* v prioritě [C]).

Architektonicky to **navazuje na stávající platformu konfigurace** (`setting_definitions`
+ `settings` + config-overlay vzor): engine pravidla jen *čte*, nikdy je nemá
zadrátovaná. Nové business parametry = nové položky konfigurace, ne nový kód.

---

## 3d. [T] Task Engine — generické úkoly nad POS

Planner neplánuje jen pravidelné coverage kampaně. Musí umět pracovat s
**libovolnými úkoly nad POS** — předání poukázek, výměna materiálů, podpis
dodatku, instalace služby, jednorázová akce, inventura, cokoli za rok přijde.

**Generický, ne „poukázky v kódu.** `task_types` je konfigurace (z Velínu);
úkol je instance typu. Žádný typ úkolu není zadrátovaný v kódu.

**Atributy úkolu:**
- seznam POS (na koho se vztahuje),
- datum zadání, **deadline** (např. +2 měsíce),
- odhad trvání, priorita,
- **splnitelné při běžné návštěvě?** (kombinovatelný / vyžaduje vlastní návštěvu),
- volitelně počet kusů, poznámka.

**Integrace do TourPlanu (chování planneru):**
- **Piggyback:** pokud technik na POS **stejně jede**, úkol se u té návštěvy jen
  zobrazí („zároveň předej poukázky") — nulový dodatečný náklad. Toto je
  přednostní režim pro kombinovatelné úkoly.
- **Vlastní návštěva až když je nutná:** samostatnou návštěvu kvůli úkolu planner
  vytvoří **teprve když se blíží deadline** a běžná návštěva už nestačí (nebo je
  úkol nekombinovatelný). Tehdy se úkol chová jako **povinná kotva** [E0]/[E] s
  časovým oknem daným deadlinem.
- Otevřené úkoly per POS vstupují do **Business priority [C]** (blížící se deadline
  zvyšuje prioritu) a do **manažerského pre-loadu [M]** (ruční jednorázové úkoly
  jsou zvláštním případem Task Engine).

**Hromadné vytváření (bulk import) — hlavní režim.** V praxi úkoly nevznikají po
jednom. Typicky přijde Excel se sloupci **POS + počet kusů**. Manažer ho jen
nahraje, jednou nastaví typ aktivity, deadline (např. +2 měsíce), prioritu a
případně odhad času — engine **automaticky založí úkoly pro všechny POS**. Žádné
ruční zakládání stovek úkolů. Stejný princip pro materiály, dodatky, inventury…

Task Engine je **průřezová vrstva**: eviduje úkoly (hromadně i jednotlivě), počítá
jejich naléhavost vůči deadlinu a napojuje je na plánování. Deterministické, plně
konfigurovatelné.

---

## 3f. Activity Plan — dlouhodobý kalendář kampaní (ne seznam úkolů)

Vedle Task Engine stojí **Activity Plan**: dlouhodobý plán business kampaní, ne
seznam úkolů. Typicky **rok dopředu** naplánované **Losy** a přibližně **Loterie**
— v praxi většinou jen 2 řádky (výjimečně třetí ad-hoc), definované **po týdnech
s prioritou**.

Planner z Activity Planu **automaticky počítá** a upozorňuje:
- **za kolik dní vyprší** poslední publikovaný TourPlan,
- **jak dlouhý TourPlan** je teď optimální (doporučená délka horizontu),
- jestli plánovanou kampaň **při objednané kapacitě stihneme** (feasibility),
- případně **jakou kapacitu objednat navíc**, aby se stihla.

Activity Plan tak přímo řídí délku plánovacího horizontu [§10] a vstupuje do
feasibility [S]. Konfigurovatelný z Velínu (týdny × priorita).

---

## 3e. Lifecycle POS — spravedlivá coverage baseline

Coverage se nesmí počítat od „nikdy", ale od okamžiku, kdy POS vstoupilo do sítě:
- **nově importované POS** → *datum první evidence* = datum prvního importu
  (`pos_master.first_seen`),
- coverage a kadence se počítají **od first_seen**, dokud POS nemá první reálnou
  návštěvu (čerstvě přidané POS není hned „po termínu"),
- **po první skutečné návštěvě** se přejde na reálné *datum poslední návštěvy*.

Tím je coverage férová k nově přidaným POS a nevytváří falešná rizika.

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

## 5. [A] Učení = kontinuální firemní standard (ne adaptace na jednotlivce)

**Cíl učení není přizpůsobit se lidem — je vytvořit firemní standard.** Planner se
nesmí učit chování konkrétního technika ani normalizovat slabý výkon nebo
flexibilní pracovní dobu. Místo toho po **každém importu SalesApp** přepočítává
agregované firemní statistiky a postupně zlepšuje své porozumění businessu.

Rodina naučených firemních standardů (všechny z agregovaných dat celé ČR):

- typická **délka návštěvy** podle typu POS (Fáze 1 — hotovo),
- průměrné **jízdní časy** (po silnici, OSRM),
- **doporučená denní produktivní kapacita** (p60/p70, per role — viz upřesnění níže),
- **počet reálně stihnutých POS/den**,
- **sezónní vzorce**,
- **dopad kampaní**,
- **business priority**,
- **frekvence návštěv (kadence)**,
- **efekt přidávání okolních POS** (Business Gain — viz §7c),
- další operační metriky.

Každý nový Draft TourPlanu tak těží ze všeho, co se firma za poslední měsíce
naučila. `duration_model` (Fáze 1) je první instancí tohoto vzoru; ostatní
statistiky používají **stejný mechanismus** (agregace → ořez extrémů → shrinkage
hierarchie → kvantily), jen nad jinou veličinou.

Model reprezentuje **kolektivní zkušenost celé firmy**, cílí na běžného až mírně
nadprůměrného technika a **ignoruje extrémy**.

> **Denní kapacita = mírně ambiciózní učený firemní STANDARD, ne pevných 8 h,
> ne popis historie.** Historie slouží k *přesným odhadům reality*; cíl planneru
> ale není popsat současný stav, nýbrž **firmu dlouhodobě posouvat**. Planner
> proto plánuje **mírně ambiciózněji** než historie — ne nereálně, ale tak, aby
> nekonzervoval dnešní neefektivitu.
>
> Výpočet po každém importu SalesApp (agregovaně, po odstranění extrémů):
>   1. **báze** = naučená distribuce produktivních minut/den, cílový percentil
>      **p60/p70** (konfigurovatelný) — mírně nad mediánem,
>   2. **ambiční navýšení** = báze × (1 + *ambitionPct*), default ~+10 %,
>   3. **strop reálně dosažitelného** = **p90** distribuce (co nejlepší kompetentní
>      dny reálně zvládly) — ambice nikdy nepřekročí strop, aby standard nebyl
>      nereálný ani demotivující.
>   → `kapacita = min( báze × (1+ambition), p90 )`
>
> Počítá se **per role** (technik vs. OZ), nikdy per jednotlivec. Planner tím
> posouvá všechny stejným směrem místo normalizace slabšího výkonu. Konfigurace
> (percentil, ambitionPct, případný pevný mantinel) dává finální slovo.

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

## 7b. [M] Manažerský pre-load — rezervace a ruční úkoly PŘED optimalizací

Manažer musí mít možnost **zarezervovat kapacitu a vložit fixní úkoly ještě
předtím, než planner začne optimalizovat.** Planner pak staví zbytek TourPlanu
*kolem* nich.

**A) Rezervace kapacity** — ubírá dostupný čas dne/týdne dřív, než ho planner
naplní:
- regionální schůzky, školení, inventura, speciální projekty, administrativa,
- nebo jednoduše „nech ~30 % týdne volných".
- Rozsah: konkrétní den/blok, nebo procento kapacity období.

**B) Ruční fixní úkoly** — ad-hoc business úkoly, u kterých manažer předem zná:
- POS ID, prioritu, odhad trvání, preferované časové okno.
- Planner je bere jako **fixní kotvy** (jako povinné POS, ale zadané ručně a
  případně s časovým oknem) a staví den kolem nich.

**C) Absence a schůzky se nepredikují.** Pokud jsou známé před plánováním,
zadá je manažer ručně jako rezervaci. Planner nikdy nehádá dovolené ani budoucí
schůzky — vytvoří nejlepší možný plán z informací dostupných v čase plánování.

Pořadí je klíčové: **[M] běží před [E0].** Nejdřív se odečte rezervovaná kapacita
a umístí ruční úkoly, teprve do zbývajícího prostoru planner rozvrhne povinnosti
a dofilluje hodnotu.

---

## 7c. Business Gain — geografie jako příležitost, ne jen omezení

Primární priorita zůstává PPT / obchodní hodnota POS. Ale **geografie není jen
omezení proveditelnosti — je to příležitost.** Když už technik jede do oblasti,
planner spočítá u okolních POS jejich **Business Gain**: obchodní přínos vůči
dodatečnému času.

```
gain(POS)  = přínos  /  vložený čas

přínos      = navýšení PPT / obchodní hodnota + vliv na kadenci
vložený čas = dodatečný čas návštěvy (predikce trvání, Fáze 1)
            + dodatečný čas přejezdu (OSRM, marginální zajížďka)
            + dopad na zbytek dne (posun ostatních návštěv)
```

- Pokud je poměr **přínos / čas výhodný**, planner POS automaticky přidá.
- Typický případ: hlavní cíl = silný POS, vedle 3–5 slabších; přidání stojí pár
  minut, ale výrazně zvýší pokrytí sítě.
- **Neoptimalizujeme jednotlivé návštěvy, ale celkovou obchodní hodnotu obsloužené
  oblasti.** Mikro-clustery [D] mají gain prakticky „zdarma" (nulový přejezd) →
  téměř vždy se berou společně.
- Efekt přidávání okolních POS se **dlouhodobě učí** (§5) — planner si ověřuje,
  kdy se dofill vyplatil, a kalibruje práh gain/čas.

Business Gain je vlastně kritérium **dofillu** ve stavbě dne [E]: kotva (povinný
POS) je daná, kolem ní se přidávají POS s nejvyšším gain/čas, dokud je volná
kapacita a den zůstává objetelný.

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
3. Dofill volné kapacity okolními POS podle **Business Gain** (§7c,
   přínos / vložený čas), vždy přibal mikro-clustery [D].
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
| `duration_model` | Hierarchické odhady trvání (p50/p75) per typ × řetězec × region × (tech), přepočítávané z historie. **(Fáze 1 — hotovo.)** |
| `learned_stats` | Rodina firemních standardů stejným vzorem (jízdní časy, sezónnost, dopad kampaní, frekvence, efekt dofillu…). `duration_model` je první instancí. |
| `capacity_standard` | Doporučená denní produktivní kapacita (p60/p70) per role, přepočítávaná z historie. |
| `task_types` | Konfigurovatelné typy úkolů (z Velínu) — název, výchozí trvání/priorita, kombinovatelnost. |
| `tasks` | Instance úkolů: POS, zadáno, deadline, trvání, priorita, kombinovatelnost, kusy/poznámka, stav. |
| `segment_definitions` | Konfigurovatelné segmenty (typ terminálu / kategorie / partner / region) + cílová kadence + priorita — z Velínu, ne v kódu. |
| `coverage_scopes` | Uložené rozsahy / simulace kampaní (které segmenty zahrnout/vyloučit) + výsledek feasibility. |
| `pos_priors` | Naučené hodnotové priory + objem/jistota dat (pro shrinkage). |
| `pos_clusters` | Předpočítané mikro-clustery (stejné centrum / pěší docházka). |
| `capacity_reservations` | Manažerské rezervace kapacity (schůzky, školení, inventura, „30 % volné") — den/blok nebo % období. |
| `manual_tasks` | Ruční fixní úkoly (POS, priorita, odhad trvání, časové okno) vkládané před optimalizací. |
| `plan_rationale` | Proč byl každý POS vybrán (kotva / ruční úkol / dofill / cluster) — vysvětlitelnost + audit. |
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
5. **Denní kapacita = učený firemní standard** (rozhodnuto): ~p60/p70 produktivních
   minut z agregovaných dat celé firmy po vyřazení extrémů, per role; konfigurace
   jen jako mantinel/override. Ne pevných 8 h, ne průměr současného chování.

---

## 17. Fázování implementace (návrh)

1. **[A] Predikce trvání** — kolektivní model p50/p75 + hierarchie. **(Hotovo.)**
2. **[D] Mikro-clustering** — předpočet clusterů. **(Hotovo.)**
3. **[A] Naučená kapacita** — mírně ambiciózní standard per role. **(Hotovo.)**
4. **[S] Coverage & Campaign Planning** — segmenty, kadence-riziko, simulace
   kampaně, feasibility poptávka vs. objednaná kapacita, **strategický poradce /
   trade-offy**. Vše konfigurovatelné z Velínu (generic engine).
5. **[M] Manažerský pre-load** — rezervace kapacity + ruční fixní úkoly.
6. **[C] Business priorita** — skóre + zdůvodnění nad existujícími pravidly.
7. **[E0] Kostra období** — rozvržení povinností + ručních úkolů do dní.
8. **[E] Stavba dne** — kotva + okruh + Business Gain dofill + OSRM proveditelnost.
9. **[F] Lifecycle** — draft → publikace (immutable) → monitoring → nový draft.
10. **Mapa** — plánovací režim nad GIS vrstvou.

Každá fáze má vlastní testy a nechává engine pravidel [B] beze změny.

---

*Konec v0.1. Připomínkuj přímo v tomto dokumentu — po schválení začneme fází 1.*
