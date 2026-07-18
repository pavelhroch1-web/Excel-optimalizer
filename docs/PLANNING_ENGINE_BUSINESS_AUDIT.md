# Audit rozhodovací logiky Planning Engine

Druhý audit. Nedívá se na routing („jak tam pojedu"), ale na **rozhodovací
logiku** („proč tam vůbec pojedu"). Cílem je kriticky zpochybnit samotné
předpoklady plánování a ověřit, jestli neoptimalizujeme špatný problém. Každé
tvrzení je doloženo kódem. Žádný kód se zatím nepíše.

---

## Ústřední teze

> **Planner dnes optimalizuje POKRYTÍ PRAVIDEL a HUSTOTU TRASY, ne NÁVRATNOST
> času v terénu.**

Skóre návštěvy je (`core_logic.py:197-202`):

```
score = CORE?·100 000 000        ← příslušnost do CORE (flag)
      + (class=="A")?·10 000 000  ← kategorizační flag
      + ppt · 1                   ← jediný hodnotový signál, NEJNIŽŠÍ řád
      + neglectedBonus (50 000 pokud wsl ≥ 26)  ← skoková funkce času
      + urgencyBoost + geoBonus
```

Z toho plyne tvrdý závěr: **hodnota zákazníka neřídí rozhodnutí.** Řídí ho
*členství ve skupině* (CORE, A). PPT — jediná veličina, která nese obchodní
hodnotu — má váhu `1` (`planning_engine.py:299`), tedy je o **8 řádů** pod
CORE flagem. PPT je fakticky **rozhodčí při shodě**, nikdy hnací síla.

Nejsilnější důkaz, že hodnota není v centru: schéma POS_MASTER má sloupec
**`businessScore`** (`runtime_state.py:38`, `pipeline.py:59`,
`import_engine.py:180`), ale **engine ho nikdy nepočítá ani nečte** — je to
prázdný placeholder. Návrh datového modelu s obchodním skóre počítal, ale
rozhodovací logika k němu nikdy nedošla. Místo toho žije na statickém PPT.

Planner tedy odpovídá na otázku *„které POS jsme podle pravidel povinni
navštívit a jsou blízko sebe"*, ne *„která návštěva tento týden přinese
nejvíc obchodní hodnoty"*. Tyto dvě otázky se shodují jen tam, kde flag CORE/A
náhodou koreluje s marginální hodnotou — což pro dlouhý ocas zákazníků a pro
přeservisované CORE **neplatí**.

---

## 10 slabých míst rozhodovací logiky

Každé: současné chování → důkaz → business kritika → lepší přístup → jak
zůstat vysvětlitelné/deterministické.

### 1. Hodnota návštěvy je statická (PPT), ne marginální (uplift)
- **Dnes:** `ppt` je pevné číslo na POS; skóre ho použije lineárně
  (`core_logic.py:200`). `businessScore` sloupec existuje, ale je nevyužitý.
- **Kritika:** Obchodní přínos není *hodnota zákazníka*, ale *přírůstek*, který
  návštěva způsobí: `uplift = E[výsledek | návštěva] − E[výsledek | bez
  návštěvy]`. Vysoký-PPT POS, který byl právě navštíven a šlape, nepotřebuje
  nic; střední-PPT POS v propadu je přesně tam, kde je euro. Engine ten rozdíl
  nevidí — nemá model návštěva→výsledek.
- **Lepší:** Odhad upliftu z historie (reakce na minulé návštěvy, trend prodejů,
  fit na kampaň). I hrubý model bije statické PPT.
- **Vysvětlitelné?** ANO — uplift počítat jako **transparentní** funkci
  několika historických veličin (např. „klesající trend + dlouho bez návštěvy
  → vysoký očekávaný přínos"), každý vstup zobrazený jako číslo. Učí se
  *vstupy*, ne černá skříňka rozhodnutí.

### 2. Zanedbání je binární práh, ne spojité riziko
- **Dnes:** `neglectedBonus` = +50 000 pokud `wsl ≥ 26`, jinak 0
  (`core_logic.py:194-196`). „Overdue" = `wsl ≥ maxIntervalWeeks`, jinak ne
  (`core_logic.py:251`).
- **Kritika:** Riziko ztráty zákazníka je **spojité a heterogenní**. Volatilní
  zákazník s vysokou hodnotou ve 20. týdnu může být v mnohem větším riziku než
  stabilní malý ve 30. Engine řídí „due" jako **skokovou funkci samotného
  času** a ignoruje, jak rychle *tento* zákazník bez návštěvy upadá i jakou to
  má cenu.
- **Lepší:** `riziko = P(ztráta | wsl) × hodnota_zákazníka`, spojitě. Práh 26
  týdnů je jeden parametr pro celou síť — nahradit per-zákazník křivkou decay.
- **Vysvětlitelné?** ANO — spojitá riziková funkce je stále deterministická a
  auditovatelná; jen nahradí `if wsl ≥ 26` za `risk(wsl, decay_i)`.

### 3. Kadence je pevná per pravidlo, ne naučená per zákazník
- **Dnes:** `maxIntervalWeeks` je konstanta pravidla (CORN/GECO/CORE) z
  CADENCE_RULES; všechny POS pod jedním pravidlem sdílí stejný interval
  (`planning_engine.py:471-475`, `core_logic.py:249-253`).
- **Kritika:** Historie návštěv (`salesapp_visits`) obsahuje skutečný
  „response interval" každého zákazníka — jak rychle po návštěvě klesá aktivita.
  Engine ho ignoruje a použije globální konstantu. Dva GECO zákazníci s úplně
  jinou dynamikou dostanou stejný interval.
- **Lepší:** Odhadnout per-zákazník optimální interval z jeho historie;
  pravidlo drží jen **mez** (garanci), ne přesný takt.
- **Vysvětlitelné?** ANO — naučený interval je jedno číslo per POS s doložením
  („medián reakce 6 týdnů"), pravidlo zůstává jako tvrdý strop.

### 4. Binární flagy dusí dlouhý ocas
- **Dnes:** CORE = 1e8, A = 1e7 (`planning_engine.py:296-301`). Jakýkoli CORE
  POS přebije jakýkoli ne-CORE bez ohledu na hodnotu i zanedbání. Neglect bonus
  (50 000) je **2000× menší** než CORE flag.
- **Kritika:** Ne-CORE zákazník může být zanedbaný 50 týdnů a přesto prohraje s
  právě navštíveným CORE. Rozhoduje **členství, ne potřeba**. To, že „CORE > vše
  vždy", je nezkoumaný předpoklad. Pro smluvní minimum legitimní — jako
  *neomezená dominance skóre* těžko.
- **Lepší:** Oddělit **garanci** (tvrdé omezení: CORE musí být navštíven do X
  týdnů) od **optimalizace hodnoty** (zbytek kapacity alokovat dle přínosu). Viz
  ústřední doporučení níže.
- **Vysvětlitelné?** ANO — a *lepší* než dnes: „constraint + objective" je
  čitelnější než souboj magických řádů 1e8 vs 50 000.

### 5. Engine neumí říct „tuto návštěvu není třeba"
- **Dnes:** Nad `min_gap` neexistuje klesající výnos. CORE POS dostává svých
  1e8 každý způsobilý týden; jediná brzda je penalizace −1e6 za návštěvu dřív
  než `min_gap` (`core_logic.py:192-193`).
- **Kritika:** Engine ochotně naplánuje návštěvu do prosperujícího POS
  navštíveného před dvěma týdny, když má vysoký flag. **Přeservisování silných,
  podservisování ocasu.** Chybí pojem „mezní užitek této návštěvy je nízký".
- **Lepší:** Klesající výnos podle času od poslední návštěvy *a* stavu zákazníka
  — po nedávné návštěvě u zdravého zákazníka blízko nule.
- **Vysvětlitelné?** ANO — křivka mezního užitku je deterministická funkce
  dvou vstupů.

### 6. Kapacita je počet návštěv, odpojený od hodnoty a úsilí
- **Dnes:** `capacity = dny · 8` návštěv (`core_logic.py:496-514`,
  `TARGET_VISITS_DAY=8` `planning_engine.py:160`). Hluboká konzultační návštěva
  i pětiminutový restock = „1".
- **Kritika:** Plán maximalizuje **počet návštěv vážený skóre**, ne
  **hodnotu za hodinu**. Nelze obchodovat „méně hlubokých vs více mělkých".
  Model trvání návštěvy (`duration.py`) existuje, ale kapacita ho neužívá.
- **Lepší:** Kapacita jako **čas**; cíl = hodnota za hodinu v terénu.
- **Vysvětlitelné?** ANO — časový rozpočet je srozumitelnější než počet.

### 7. Per-technik greedy = lokální optimum, žádná globální alokace hodnoty
- **Dnes:** Každý technik má vlastní pool `groups[tech]`, skóruje a vybírá
  nezávisle (`planning_engine.py:504,566`); kapacita je pevná per osoba
  (`core_logic.py:509`). Výběr je greedy sort (`core_logic.py:355-358`).
- **Kritika:** Terénní kapacita je **sdílený, zaměnitelný zdroj**. Systém se
  nikdy nezeptá: „kde v celé síti leží N nejhodnotnějších návštěv?" Technik se
  slabým územím zaplní kapacitu nízkohodnotnými návštěvami, zatímco hodnotný
  shluk v přetíženém sousedním území zůstane neobsloužen. **Hranice území jsou
  tvrdé omezení, kapacita fixní per osoba — žádné vyvažování dle hodnoty.**
  Tohle je největší *strukturální* slabina.
- **Lepší:** Globální alokace hodnoty s územím jako **měkkou preferencí** +
  rebalancing přetížení, ne tvrdý oddíl.
- **Vysvětlitelné?** ANO, pokud alokace zůstane deterministická a každé přeřazení
  „přes hranici" se zdůvodní („POS X převeden k technikovi Y: +Z hodnoty,
  původní technik přetížen").

### 8. Kampaň řídí pokrytí, ne hodnotu
- **Dnes:** Hold-back odloží ne-mandatorní POS, aby padl do okna kampaně, s
  tolerancí dle klasifikace (A=1 týden, jiné=3, `core_logic.py:291`). Pokrytí
  kampaně se měří jako **počet POS** v okně (`brain.py` campaign coverage).
- **Kritika:** Odklad se řídí třídou POS, ne **očekávaným přínosem kampaně pro
  ten konkrétní POS**. Kampaň může být pro část POS irelevantní, přesto se
  drží. Úspěch = počet navštívených, ne realizovaný potenciál kampaně.
- **Lepší:** Hold-back a priorita kampaně dle **fitu POS × kampaň** (očekávaný
  přínos), ne dle třídy.
- **Vysvětlitelné?** ANO — fit skóre je transparentní tabulka POS×kampaň.

### 9. Plán je otevřená smyčka — neučí se z výsledků
- **Dnes:** Compliance sleduje, *zda* se plánovaná návštěva stala
  (`compliance_engine`), ale **nic nevrací** zpět do rozhodování: „navštívili
  jsme X, stalo se Y, takže X-podobné POS mají hodnotu Z". Historie se využije
  jen na výpočet `wsl` (`runtime_state.py:45-115`).
- **Kritika:** Nejbohatší aktivum — historie návštěv + výsledky — pohání jedinou
  skalární veličinu (týdny od návštěvy). Planner nikdy nezjistí, jestli jeho
  rozhodnutí přinesla hodnotu.
- **Lepší:** Uzavřít smyčku: (compliance + výsledek) → aktualizace odhadu
  hodnoty/decay per zákazník.
- **Vysvětlitelné?** ANO, pokud se učí **offline a deterministicky** —
  parametry přepočítané dávkově na plánovací periodu, ne online černá skříňka.
  Vstup je auditovatelný, rozhodovací pravidlo zůstává čitelné.

### 10. Rozhodnutí jsou lokální i v čase (per týden), ne přes horizont
- **Dnes:** V rámci běhu se plánuje týden po týdnu (`planning_engine.py:568`);
  jediná cross-týdenní logika je campaign hold-back. Není optimalizace *kdy* v
  horizontu zákazníka navštívit.
- **Kritika:** Pro zákazníka s intervalem 6 týdnů v 4týdenním okně je jedno, zda
  padne do týdne 1 nebo 4 — ale engine to řeší greedy hned v prvním způsobilém
  týdnu podle skóre, ne rozprostřením zátěže/hodnoty přes horizont.
- **Lepší:** Alokace přes celý horizont (kdy, ne jen zda), vyvažující týdenní
  kapacitu a načasování dle decay.
- **Vysvětlitelné?** ANO — horizontová alokace může být deterministická.

---

## Odpovědi na tvé konkrétní otázky

**Kde jsou příliš jednoduché obchodní heuristiky?**
PPT jako jediná a statická hodnota (`core_logic.py:200`); neglect jako práh 26
týdnů (`core_logic.py:194`); premium jako pevných 20 % (`planning_engine.py:279`);
hodnota flagu > vše (1e8 vs 50 000).

**Která rozhodnutí jsou binární a mohla by být adaptivní?**
Neglect (ano/ne dle 26 tý.), overdue (ano/ne dle maxInterval), CORE/A členství
(plný flag / nic), hold-back (drž/nedrž dle třídy), premium (v top 20 % / ne).
Všechna jsou dnes skoková, přitom podklad (historie) umožňuje spojité verze.

**Kde využít historii místo pevných pravidel?**
Per-zákazník kadence/decay místo `maxIntervalWeeks` konstanty (bod 3); uplift
místo statického PPT (bod 1); riziko zanedbání jako naučená křivka (bod 2);
klesající výnos po nedávné návštěvě (bod 5).

**Kde lépe pracovat s rizikem zanedbání?**
Nahradit binární práh 26 týdnů za `P(ztráta | wsl, decay_i) × hodnota_i` — riziko
vážené hodnotou a pravděpodobností, ne čistý čas (bod 2, 4).

**Kde planner neumí odhadnout obchodní přínos návštěvy?**
Všude — nemá žádný uplift model; `businessScore` sloupec je prázdný
(`runtime_state.py:38`). Rozhodnutí stojí na členství a stáří, ne na
očekávaném přínosu (bod 1, teze).

**Pracujeme správně s prioritami/kadencí/kampaněmi/kapacitou jako celkem?**
Ne jako s celkem — jsou to **oddělené aditivní členy a filtry**, ne společná
optimalizace pod jedním rozpočtem. Priorita = magické řády; kadence = tvrdé
konstanty; kampaň = hold-back dle třídy; kapacita = fixní počet per technik. Nic
z toho spolu neobchoduje trade-offy (bod 4, 6, 7, 8).

**Nejsou rozhodnutí příliš lokální?**
Ano, na třech úrovních: per-technik (ne globálně, bod 7), per-týden (ne přes
horizont, bod 10), per-den greedy nearest-anchor (audit 1). Chybí globální
alokace hodnoty.

---

## Zpochybnění základních předpokladů

| # | Předpoklad, na kterém planner stojí | Realita | Verdikt |
|---|---|---|---|
| P1 | Hodnota POS = statické PPT | Hodnota návštěvy je marginální a dynamická (uplift) | ❌ principiálně špatně |
| P2 | Potřeba = čas od návštěvy překročí práh | Potřeba = riziko ztráty × hodnota, spojité, per zákazník | ❌ principiálně špatně |
| P3 | CORE/A členství = trvalá neomezená priorita | Legitimní jako *garance (constraint)*, ne jako *dominance skóre* | ⚠️ zaměněná role |
| P4 | Kadence je vlastnost pravidla | Je to vlastnost zákazníka, odvoditelná z historie | ❌ podhodnocené |
| P5 | Návštěva = návštěva (kapacita = počet) | Návštěvy se liší úsilím i přínosem | ⚠️ hrubé |
| P6 | Území technika = nezávislá úloha | Kapacita je sdílený zaměnitelný zdroj | ❌ zdroj lokálnosti |
| P7 | Plán je otevřená smyčka (bez učení) | Máme historii i compliance k uzavření smyčky | ⚠️ promarněné aktivum |
| P8 | „Optimalizuj pokrytí + hustotu" | Byznys chce návratnost času v terénu | ❌ špatný cíl |

**Nejdůležitější závěr:** dva předpoklady jsou principiálně špatně a jsou
kořenem ostatních — **P8 (optimalizujeme pokrytí místo ROI času)** a **P1
(hodnota je statická místo marginální)**. Vše ostatní jsou jejich projevy.

---

## Ústřední architektonické doporučení (bez kódu)

Sladit „nejlepší ve své kategorii" s „vysvětlitelný, auditovatelný,
deterministický" jde přes **jasné oddělení dvou vrstev**:

1. **Vrstva omezení (tvrdá, smluvní, rule-based — zůstává jak je):**
   cadence garance (CORE/CORN/GECO musí do X týdnů), blacklist, FORCE_EXCLUDE/
   INCLUDE, filtry. Deterministické, čitelné, beze změny.

2. **Vrstva cíle (co maximalizovat se zbytkem kapacity):** *očekávaný přírůstek
   obchodní hodnoty*, kde hodnota = `uplift_i × hodnota_i × riziko_i`, s
   **naučenými, ale plně zobrazenými** per-zákazník parametry (decay, uplift,
   trend). Optimalizace je deterministická (stejný vstup → stejný plán) a každý
   parametr má doložení.

Klíčová myšlenka: **učit se VSTUPY (decay, hodnota, uplift), ne rozhodovací
pravidlo.** Rozhodnutí zůstane transparentní optimalizace nad čitelnými čísly —
tím se udrží auditovatelnost i determinismus, a přitom se odemkne hodnotová
adaptivita. Zároveň to *odstraní* křehké magické řády (1e8/1e7/−1e6) z auditu 1,
protože garance se stanou constraints a hodnota se stane objektivem.

---

## Prioritizace slabin (business ROI × náročnost × riziko změny)

Riziko změny = riziko pro determinismus/vysvětlitelnost/důvěru uživatele.
Řazeno od nejlepšího poměru.

| # | Slabina → zlepšení | Business ROI | Náročnost | Riziko změny | Verdikt |
|---|---|---|---|---|---|
| A | **Naplnit `businessScore` transparentním hodnotovým skóre** (trend prodejů + hodnota + stáří) a zobrazit ho — zatím jen jako info vrstvu vedle PPT | 🟢 Vysoký | 🟢 Nízká | 🟢 Nízké | **Udělat první** — nemění rozhodnutí, jen zviditelní hodnotu a připraví data |
| B | **Spojité riziko zanedbání** místo prahu 26 tý. (bod 2) — `risk(wsl, hodnota)` do neglect členu | 🟢 Vysoký | 🟢 Nízká | 🟡 Střední | Vysoký poměr; jeden člen skóre, snadno A/B |
| C | **Per-zákazník kadence z historie** (bod 3) — pravidlo drží strop, interval se učí | 🟢 Vysoký | 🟡 Střední | 🟡 Střední | Velký přínos pro „proč jet"; strop chrání smluvní jistotu |
| D | **Klesající mezní užitek** po nedávné návštěvě (bod 5) — přestat přeservisovávat silné | 🟢 Vysoký | 🟢 Nízká | 🟡 Střední | Uvolní kapacitu pro ocas |
| E | **Kapacita jako čas** (bod 6) — `duration.py` už existuje | 🟡 Střední | 🟡 Střední | 🟡 Střední | Realističtější dny |
| F | **Fit POS×kampaň** místo hold-back dle třídy (bod 8) | 🟡 Střední | 🟡 Střední | 🟡 Střední | Lepší kampaně |
| G | **Uplift model** (bod 1) — návštěva→výsledek | 🟢 Vysoký | 🔴 Vysoká | 🟡 Střední | Nejvyšší strop hodnoty, ale potřebuje data o výsledcích |
| H | **Uzavřít smyčku učení** (bod 9) — outcome → parametry | 🟢 Vysoký | 🔴 Vysoká | 🟡 Střední | Násobí přínos B/C/G; dávkově, deterministicky |
| I | **Constraint + objective refactor** (doporučení výše) — garance jako omezení, hodnota jako cíl | 🟢 Vysoký | 🔴 Vysoká | 🔴 Vysoké | Strategické jádro V2; nasadit až po A/B srovnání, mění celý scoring |
| J | **Globální alokace hodnoty přes techniky** (bod 7) — území měkké | 🟢 Vysoký | 🔴 Vysoká | 🔴 Vysoké | Největší strukturální strop, ale mění „kdo kam jezdí" — citlivé na důvěru; až po I |
| K | **Horizontová alokace v čase** (bod 10) | 🟡 Střední | 🔴 Vysoká | 🟡 Střední | Až s I/J |

**Doporučené pořadí rozhodování:** nejdřív **A → B → D → C** (nízké riziko,
vysoký poměr, nemění strukturu, jen zpřesní vstupy a jeden člen skóre). Ty samy
posunou planner od „pokrytí" k „potřebě/hodnotě" bez ztráty determinismu.
Teprve po ověření na reálných datech zvážit strategické **I → J** (přestavba
scoringu na constraint+objective a globální alokaci), které mají nejvyšší strop,
ale nejvyšší riziko pro důvěru a auditovatelnost.

---

## Shrnutí jednou větou

Planner je vynikající **auditovatelný systém pro dodržování pravidel a hustotu
tras**, ale je to **špatná odpověď na otázku „proč tam jet"**: rozhoduje podle
členství a stáří, ne podle očekávaného přínosu — a nejcennější aktivum
(historie + výsledky) používá jen na jednu skalární veličinu. Největší business
ROI neleží v dalších 2 % ušetřených kilometrů, ale v tom naučit planner
**odhadnout hodnotu a riziko každé návštěvy** a alokovat podle nich — při
zachování determinismu tím, že se učí *vstupy*, ne rozhodovací pravidlo.
