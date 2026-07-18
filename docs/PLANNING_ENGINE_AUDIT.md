# Architektonický audit Planning Engine

Hodnocení **kvality algoritmu**, ne hledání bugů. Každé tvrzení je doloženo
konkrétním místem v kódu (`soubor:řádek`). Jádro algoritmu žije ve dvou
souborech:

- `desktop_client/engines/planning_engine.py` — adaptér: čtení dat, parsování
  configu, sestavení kandidátů, orchestrace fází, zápis výstupu.
- `desktop_client/engines/core_logic.py` — čisté funkce: scoring, výběr,
  geo-clustering, hold-back, urgency, day-assignment, vzdálenost.

Vstupní data pro engine staví `backend/runtime_state.py` (POS_MASTER z SQLite).

---

## Datový tok (přehled fází)

```
SQLite (pos_master + salesapp_visits)
  └─ runtime_state.build_pos_master()   → odvození weeksSinceLastVisit
      └─ pipeline.build_state() → MockWorkbook (sheety)
          └─ planning_engine.run()
              1. Parse configu (CONTROL, SCORE_PROFILES, CADENCE_RULES, …)
              2. Filtr kandidátů (status/blacklist/override/terminál/trh/kategorie)
              3. Cadence tagging (mandatoryRuleId, deadlineWeeks)
              4. Base scoring (compute_score)
              5. Address dedup mandatorních
              6. Proactive urgency boost
              7. Geo-cluster bonus
              8. Premium / Pareto Top-20 %
              9. Týdenní výběr (hold-back → select_week_pos → GPS bonus)
             10. Rozřazení na dny (geo_days)
             11. Zápis MANAGER_PLAN + PLAN_LIFECYCLE
```

---

## Fáze po fázích (10 bodů pro každou)

### Fáze 0 — Vstup a derivace dat
*Kód: `runtime_state.py:45-115`*

1. **Co dělá:** Sestaví POS_MASTER z tabulky `pos_master`; pro každý POS odvodí
   `weeksSinceLastVisit` z `salesapp_visits` (max visit_date → dnes).
2. **Rozhodnutí:** Nikdy nenavštívený POS = „maximálně urgentní" —
   `wsl = never_weeks` = týdny od nejstaršího záznamu v celém datasetu
   (`runtime_state.py:75,91`).
3. **Parametry:** žádné konfigurovatelné; window floor `260` je fallback, když
   dataset nemá žádné návštěvy (`:75`).
4. **Konfigurovatelné?** Ne. Derivace je pevná v kódu.
5. **Hardcoded:** `260` (floor), týden = `days // 7` (`:65`).
6. **Implicitní předpoklad:** „poslední návštěva jakékoli role" reprezentuje
   cadenci (OZ i technik se sčítají — `:50` bez filtru role).
7. **Heuristika:** never-visited = urgentní přes celé okno pozorování.
8. **Kompromis:** Bez skutečné historie před importem se stáří odhaduje z okna
   dat, ne z reálného „naposledy viděno".
9. **Suboptimalita:** Krátké datové okno → nikdy-navštívené POS dostanou nízké
   `wsl`, tedy nižší urgenci, než si reálně zaslouží.
10. **Matematicky lepší:** Bayesovský odhad stáří s priorem na kategorii /
    kadenci místo tvrdého „okno = stáří".

### Fáze 1 — Parse konfigurace
*Kód: `planning_engine.py:125-346`*

1. **Co dělá:** Načte CONTROL (skalární parametry), SCORE_PROFILES (váhy),
   CADENCE_RULES, TERMINAL/MARKET/CATEGORY_RULES, PARETO_GROUPS, ACTIVITY_PLAN,
   CAPACITY_OVERRIDE, BLACKLIST, PLAN_LIFECYCLE.
2. **Rozhodnutí:** Určení `START_WEEK` — řetěz: explicitní CONTROL →
   „týden po posledním v MANAGER_PLAN" → dnešní ISO týden (`:152-158`).
3. **Parametry:** viz kompletní tabulka konstant níže.
4. **Konfigurovatelné?** Ano — vše přes CONTROL / config sheety, které přes
   SQLite editor (`model_config`, `cadence_config`, `config_store`) mění GUI.
5. **Hardcoded:** všechny **fallback defaulty** (`setting(name, fallback)`),
   viz tabulka. Když sheet chybí, platí default z kódu.
6. **Implicitní předpoklad:** názvy CONTROL klíčů a hlaviček sheetů jsou pevné
   (`"CAMPAIGN_START_WEEK"`, `"minGapWeeks"`, …).
7. **Heuristika:** „resume where last run left off" (`:155`).
8. **Kompromis:** Config je klíč-hodnota (ploché), ne strukturovaná politika.
9. **Suboptimalita:** default fallbacky se tiše použijí, pokud config chybí —
   plán může vzniknout s jinými vahami, než uživatel čeká.
10. **Matematicky lepší:** validace configu + explicitní chyba místo tichého
    fallbacku.

### Fáze 2 — Filtr kandidátů
*Kód: `planning_engine.py:381-419`*

1. **Co dělá:** Z POS_MASTER vybere plánovatelné POS.
2. **Rozhodnutí (pořadí):** status ≠ Active → skip (`:385`); blacklist → skip
   (`:389`); `FORCE_EXCLUDE` → skip (`:395`); jinak filtry terminál ∧ trh ∧
   kategorie≠EXCLUDE (`:403-407`); `FORCE_INCLUDE` filtry obchází (`:409`).
3. **Parametry:** aktivní terminály/trhy/kategorie (sheety), override sloupce.
4. **Konfigurovatelné?** Ano — TERMINAL/MARKET/CATEGORY_RULES + BLACKLIST +
   override sloupce v POS_MASTER; vše editovatelné v GUI (Plánovací model).
5. **Hardcoded:** vokabulář `"Active"`, `"FORCE_EXCLUDE"`, `"FORCE_INCLUDE"`,
   `"EXCLUDE"` (`:385,395,399,406`).
6. **Implicitní předpoklad:** `terminal_ok` = **substring** match
   (`any(t in value)`, `:225`), zatímco `market_ok` = přesná rovnost (`:233`) —
   dva různé modely shody.
7. **Heuristika:** substring pro terminály.
8. **Kompromis:** FORCE_INCLUDE obchází i kategorii EXCLUDE — manuální override
   je nadřazený business pravidlům.
9. **Suboptimalita:** substring match terminálu může neúmyslně zahrnout jiný typ
   (`"MALY"` in `"MALYSTREDNI"`).
10. **Matematicky lepší:** jednotný, explicitní model shody (množinová
    příslušnost) pro všechny tři filtry.

### Fáze 3 — Cadence tagging (mandatoryRuleId, deadlineWeeks)
*Kód: `planning_engine.py:448-475`, `core_logic.py:239-253`*

1. **Co dělá:** Označí POS `mandatoryRuleId`, pokud spadá pod HARD cadence
   pravidlo (ONCE_PER_CAMPAIGN, nebo RECURRING+overdue).
2. **Rozhodnutí:** ONCE_PER_CAMPAIGN má přednost před RECURRING (`:448-461`);
   `deadlineWeeks` = maxIntervalWeeks matchnutého HARD pravidla, jinak
   NEGLECTED_AFTER (`:471-475`).
3. **Parametry:** CADENCE_RULES (scope, matchValue, min/maxInterval, priority,
   guaranteeType, intervalType, dedupBy).
4. **Konfigurovatelné?** Ano — CADENCE_RULES + `cadence_config` SQLite overlay.
5. **Hardcoded:** enum stringy `"ONCE_PER_CAMPAIGN"`, `"RECURRING"`, `"HARD"`,
   scope `"CATEGORY"/"CATEGORYPREFIX"/"MARKET"`, `"ADDRESS"` (`:267-276`,
   `core_logic.py:243-245`).
6. **Implicitní předpoklad:** POS spadá **maximálně pod jedno** mandatorní
   pravidlo (první match vyhraje, `break` na `:451,461`).
7. **Heuristika:** overdue = `wsl ≥ maxIntervalWeeks` nebo `wsl is None`
   (`core_logic.py:251`).
8. **Kompromis:** priorita pravidel se v matchingu nepoužívá pro výběr „nejlepšího"
   — bere se první v pořadí seznamu.
9. **Suboptimalita:** pokud POS vyhovuje dvěma pravidlům, tag závisí na pořadí
   řádků v CADENCE_RULES, ne na `priority`.
10. **Matematicky lepší:** výběr matchnutého pravidla podle `priority`, ne podle
    pořadí v tabulce.

### Fáze 4 — Base scoring
*Kód: `core_logic.py:188-203`*

1. **Co dělá:** Spočte skóre POS jako **aditivní** součet.
2. **Rozhodnutí:** `score = core?·W_core + (class=="A")?·W_A + ppt·W_ppt + gap_adj`.
3. **Parametry:** ScoreWeights (core, kategorizaceA, ppt, neglectedBonus),
   min_gap, neglected_after.
4. **Konfigurovatelné?** Váhy ano (SCORE_PROFILES „DEFAULT"). Struktura vzorce
   ne.
5. **Hardcoded:** penalizace `gap_adjustment = -1000000` za návštěvu dřív než
   min_gap (`core_logic.py:193`); default váhy `core=1e8, A=1e7, ppt=1,
   neglected=50000` (`planning_engine.py:296-301`).
6. **Implicitní předpoklad:** klasifikace nabývá literálu `"A"` (`:199`);
   váhové řády (1e8 ≫ 1e7 ≫ 50000 ≫ ppt) tvoří **lexikografickou** prioritu —
   CORE vždy přebije A, A vždy přebije neglect atd.
7. **Heuristika:** velké oddělené řády vah simulují tvrdou prioritu uvnitř
   spojitého skóre.
8. **Kompromis:** aditivní model → PPT nikdy nepřekoná CORE (rozdíl řádů), ale
   uvnitř jedné třídy rozhoduje PPT lineárně.
9. **Suboptimalita:** `-1000000` není absolutní zákaz — dost velký neglect bonus
   + urgency by teoreticky mohl přerůst penalizaci (nestane se při defaultech,
   ale je to křehké).
10. **Matematicky lepší:** explicitní tvrdá omezení (constraint) místo měkkých
    velkých čísel; hierarchické/lexikografické skóre jako tuple, ne suma řádů.

### Fáze 5 — Address dedup mandatorních
*Kód: `planning_engine.py:516-523`, `core_logic.py:319-335`*

1. **Co dělá:** Dva POS na stejné adrese pod stejným HARD pravidlem
   (dedupBy=ADDRESS) → přežije jen vyšší PPT, pro celý běh.
2. **Rozhodnutí:** klíč = `ruleId + "|" + norm(ulice|mesto)` (`core_logic.py:330`).
3. **Parametry:** `dedupBy` v CADENCE_RULES.
4. **Konfigurovatelné?** Zapnutí ano (dedupBy), logika klíče ne.
5. **Hardcoded:** klíč = ulice+město (ne PSČ, ne GPS).
6. **Implicitní předpoklad:** „stejná adresa" = shodný normalizovaný string
   ulice+město.
7. **Heuristika:** vyšší PPT vyhrává.
8. **Kompromis:** provádí se předem (`:516-523`) i uvnitř `pick_mandatory`, aby
   GPS bonus loosera nevrátil zpět (`:506-515` komentář).
9. **Suboptimalita:** překlep/varianta v adrese → dva „různé" klíče → dedup
   selže; naopak dvě reálně různé provozovny na stejné ulici splynou.
10. **Matematicky lepší:** dedup podle GPS blízkosti + fuzzy adresa, ne exact
    string.

### Fáze 6 — Proactive urgency boost
*Kód: `planning_engine.py:529-536`, `core_logic.py:300-316`*

1. **Co dělá:** Lineárně zvyšuje skóre, jak se `wsl` blíží `deadlineWeeks`.
2. **Rozhodnutí:** ramp od `ramp_start_ratio·deadline` do `deadline`, max
   `URGENCY_BOOST_MAX` (`core_logic.py:311-316`).
3. **Parametry:** URGENCY_BOOST_MAX (default 20000), RAMP_START_RATIO (0.5).
4. **Konfigurovatelné?** Ano (CONTROL).
5. **Hardcoded:** defaulty 20000 / 0.5 (`planning_engine.py:189-190`).
6. **Implicitní předpoklad:** urgence roste **lineárně**.
7. **Heuristika:** lineární ramp.
8. **Kompromis:** boost 20000 je pod řádem A-bonusu (1e7) → nikdy nepřekoná
   třídu, jen řadí uvnitř.
9. **Suboptimalita:** lineární ramp podceňuje „na poslední chvíli" — riziko
   propásnutí deadline neroste lineárně.
10. **Matematicky lepší:** konvexní (exponenciální) ramp nebo penalizace
    očekávaného zmeškání.

### Fáze 7 — Geo-cluster bonus
*Kód: `planning_engine.py:542-547`, `core_logic.py:213-228`*

1. **Co dělá:** Přičte bonus za sousedy do `radiusKm` (odměna za trasovou
   hustotu).
2. **Rozhodnutí:** `bonus = Σ(soused.score · bonusFactor)`, cap `maxBonus`
   (`core_logic.py:226-228`).
3. **Parametry:** GEO_CLUSTER_RADIUS_KM (3), BONUS_FACTOR (0.01), MAX_BONUS
   (5000).
4. **Konfigurovatelné?** Ano (CONTROL).
5. **Hardcoded:** defaulty 3 / 0.01 / 5000 (`planning_engine.py:178-181`).
6. **Implicitní předpoklad:** GPS `(0,0)` = „neznámá poloha" → bonus 0
   (`core_logic.py:220`).
7. **Heuristika:** hustota sousedů ≈ efektivita trasy.
8. **Kompromis:** bonus se počítá z **base skóre** sousedů (dvoufázově), aby
   neleakoval do sebe (`:538-541` komentář).
9. **Suboptimalita:** bonus je O(n²) v rámci technika a odměňuje hustotu, ne
   skutečnou délku trasy — hustý shluk daleko od zbytku dne dostane bonus, i
   když zvyšuje km.
10. **Matematicky lepší:** vážit skutečnou marginální úsporou km na trase, ne
    počtem sousedů.

### Fáze 8 — Premium / Pareto Top-20 %
*Kód: `planning_engine.py:550-551`, `core_logic.py:231-236`*

1. **Co dělá:** Označí top X % POS technika jako `premium`.
2. **Rozhodnutí:** `limit = ceil(n · percent/100)` nejlepších podle skóre.
3. **Parametry:** PREMIUM_TOP20 boundaryValue (PARETO_GROUPS).
4. **Konfigurovatelné?** Ano (procento), ale jen jeden tier „TOP20".
5. **Hardcoded:** default 20 %, tierId literál `"PREMIUM_TOP20"`
   (`planning_engine.py:287-289`).
6. **Implicitní předpoklad:** premium = per-technik (ne globálně).
7. **Heuristika:** Paretův princip (20/80).
8. **Kompromis:** premium se uplatní jen když `hold_premium` (blízká změna
   kampaně, `:595`) — jinak nemá vliv na řazení.
9. **Suboptimalita:** práh je fixní percentil, ne přirozený zlom v distribuci
   skóre.
10. **Matematicky lepší:** detekce „knee" v distribuci skóre místo pevných 20 %.

### Fáze 9 — Týdenní výběr (hold-back → select → GPS)
*Kód: `planning_engine.py:566-598`, `core_logic.py:278-297,338-389`*

1. **Co dělá:** Pro každý týden vybere do kapacity: odfiltruje hold-back,
   `select_week_pos`, přidá GPS bonus návštěvy.
2. **Rozhodnutí:**
   - **Hold-back** (`should_hold_back`): odloží ne-mandatorní/ne-force POS,
     pokud brzy začíná kampaň a stihne se po ní (`core_logic.py:289-297`).
   - **select_week_pos:** nejdřív všechny mandatorní (`pick_mandatory`), pak
     doplní podle klíče `(force, premium?, -score)` do kapacity
     (`core_logic.py:342-358`).
   - **GPS bonus:** k vybraným přidá blízké POS do rádiusu, max N (`:368-389`).
3. **Parametry:** kapacita (Fáze 1), HOLDBACK_* , GPS_EXTRA_*.
4. **Konfigurovatelné?** Ano (CONTROL + CAPACITY_OVERRIDE).
5. **Hardcoded:** GPS default vypnuto (`GPS_EXTRA_ENABLED=0`), radius 300 m,
   maxVisits 5 (`planning_engine.py:169-173`); hold-back tolerance A=1/jiné=3,
   lookahead=3 (`:184-188`).
6. **Implicitní předpoklad:** kapacita je počet návštěv (ne čas/km); mandatorní
   se **nikdy** neodkládají a nezapočítávají do kapacity před doplněním.
7. **Heuristika:** greedy výběr podle skóre; GPS „přibal, co je po cestě".
8. **Kompromis:** výběr je **greedy sort**, ne optimalizace — nejlepší skóre
   nejdřív, žádný lookback.
9. **Suboptimalita:** GPS bonus může kapacitu **překročit** (přidává nad rámec
   `select_week_pos` výběru, `:598`); greedy nezohlední, že dvě lehce horší POS
   blízko sebe by daly lepší trasu než jedno top vzdálené.
10. **Matematicky lepší:** kapacitně omezená optimalizace (max skóre při limitu
    km/času), ne čistý greedy sort.

### Fáze 10 — Rozřazení na dny
*Kód: `core_logic.py:406-474`*

1. **Co dělá:** Rozdělí týdenní výběr na pracovní dny.
2. **Rozhodnutí:** kotvy = top-scoring POS (jedna/den), zbytek přiřazen
   **globálně kapacitovaným nejbližším-kotvícím** matchingem
   (`core_logic.py:433-453`).
3. **Parametry:** počet pracovních dní (kalendář), skóre.
4. **Konfigurovatelné?** Ne — heuristika je pevná.
5. **Hardcoded:** `per_day_target = ceil(n/dny)`, kapacita `target-1`
   (`:432,438`).
6. **Implicitní předpoklad:** **start technika není znám** → záměrně se
   nepočítá pořadí zastávek v rámci dne (`:407-418` komentář, „ja nevim odkud
   bude vyjizdet").
7. **Heuristika:** kapacitovaný nearest-centroid (kotva = nejvyšší skóre).
8. **Kompromis:** hodnota (skóre) určuje kotvy, ne geografie → kotvy mohou být
   daleko od sebe a shluky se překrývají.
9. **Suboptimalita:** není to optimální rozklad; přetečení jde na „poslední den"
   (`:463-468`); žádné pořadí zastávek uvnitř dne (žádné TSP).
10. **Matematicky lepší:** kapacitované k-means/vyvážené clustery podle
    geografie + intra-day TSP (start point lze doplnit jako parametr).

### Fáze 11 — Zápis a lifecycle
*Kód: `planning_engine.py:600-687`*

1. **Co dělá:** Zapíše řádky MANAGER_PLAN + přidá nové týdny do PLAN_LIFECYCLE
   jako „Draft".
2. **Rozhodnutí:** zamčené týdny (Published/Active/Closed) se přenesou beze
   změny (`:206,558-561,566-570`).
3. **Parametry:** locked_weeks z PLAN_LIFECYCLE.
4. **Konfigurovatelné?** Stavy jsou dané enumem.
5. **Hardcoded:** statusy `"Published"/"Active"/"Closed"/"Draft"` (`:206,678`).
6. **Implicitní předpoklad:** MANAGER_PLAN má pevné pořadí 17 sloupců
   (`:625-629,668`).
7. **Heuristika:** immutabilita zamčených týdnů.
8. **Kompromis:** regeneruje se jen odemčené okno.
9. **Suboptimalita:** — (jde o zápis, ne rozhodování).
10. **Matematicky lepší:** — (I/O fáze).

---

## Souhrnná tabulka

| Fáze | Současný algoritmus | Klíčové parametry | Konfigurovatelné? | Riziko | Možné zlepšení |
|---|---|---|---|---|---|
| 0 Vstup/derivace | never-visited = okno dat; wsl = (dnes−last)/7 | — (floor 260) | ❌ pevné | Krátké okno podhodnotí urgenci | Bayes odhad stáří |
| 1 Config parse | CONTROL/sheety s kódovými fallbacky | všechny CONTROL klíče | ✅ (tichý fallback) | Chybějící config → tiché defaulty | Validace + tvrdá chyba |
| 2 Filtr kandidátů | řetěz filtrů; terminál=substring, trh=exact | rules sheety, override | ✅ | Substring match terminálu | Jednotný množinový match |
| 3 Cadence tagging | první matchnuté HARD pravidlo | CADENCE_RULES | ✅ | Tag dle pořadí řádků, ne priority | Výběr dle `priority` |
| 4 Base scoring | aditivní suma řádů (1e8/1e7/50000/1·ppt) | ScoreWeights | ⚠️ jen váhy, ne struktura | Měkký −1e6 „zákaz" | Lexikografické skóre + constraints |
| 5 Address dedup | exact ulice+město, vyšší PPT | dedupBy | ⚠️ jen zapnutí | Překlep → dedup selže | GPS/fuzzy dedup |
| 6 Urgency boost | lineární ramp k deadline | MAX 20000, RAMP 0.5 | ✅ | Lineární podceňuje pozdní riziko | Konvexní ramp |
| 7 Geo-cluster | Σ score sousedů·0.01, cap 5000 | radius/factor/max | ✅ | Odměna hustoty, ne km | Marginální úspora km |
| 8 Premium tier | top 20 % percentil | boundaryValue | ⚠️ jen procento | Fixní percentil | „Knee" detekce |
| 9 Týdenní výběr | greedy sort (force,premium,−score) | kapacita, holdback, GPS | ✅ | GPS překročí kapacitu; greedy | Kapacitní optimalizace |
| 10 Day assignment | kotva=skóre + nearest-anchor | — | ❌ pevné | Bez TSP, přetok na poslední den | Balanced clustering + TSP |
| 11 Zápis/lifecycle | immutabilní zamčené týdny | PLAN_LIFECYCLE | enum | — | — |

---

## Kompletní seznam hardcoded konstant, magických čísel a pevných vah

### Váhy skóre (fallback defaulty)
*`planning_engine.py:296-301`* — přepsatelné SCORE_PROFILES „DEFAULT":
- `core = 100 000 000` (1e8)
- `kategorizaceA = 10 000 000` (1e7)
- `ppt = 1`
- `neglectedBonus = 50 000`

### CONTROL parametry (fallback defaulty)
*`planning_engine.py:159-190`* — přepsatelné CONTROL sheetem:
- `CAMPAIGN_LENGTH = 4`
- `TARGET_VISITS_DAY = 8`
- `STANDARD_VISIT_GAP = 8`
- `NEGLECTED_AFTER_WEEKS = 26`
- `SYNC_WINDOW_WEEKS = 1`
- `GPS_EXTRA_ENABLED = 0`, `GPS_EXTRA_RADIUS_METERS = 300`, `GPS_EXTRA_MAX_VISITS = 5`
- `GEO_CLUSTER_RADIUS_KM = 3`, `GEO_CLUSTER_BONUS_FACTOR = 0.01`, `GEO_CLUSTER_MAX_BONUS = 5000`
- `HOLDBACK_LOOKAHEAD_WEEKS = 3`, `HOLDBACK_TOLERANCE_A_WEEKS = 1`, `HOLDBACK_TOLERANCE_OTHER_WEEKS = 3`
- `URGENCY_BOOST_MAX = 20 000`, `URGENCY_BOOST_RAMP_START_RATIO = 0.5`

### Skutečně pevné magické konstanty (bez configu)
- **Gap penalizace `−1 000 000`** — návštěva dřív než min_gap (`core_logic.py:193`, mirror `planning_engine.py:499`).
- **Fallback CORE min-gap `2`** — když `core_rule.minGapWeeks` chybí (`planning_engine.py:478`).
- **Fallback premium `20.0 %`** (`planning_engine.py:279,289`).
- **Distance model `111` / `72`** km-na-stupeň (`core_logic.py:44`) — Euklidovská aproximace pro **středoevropskou zeměpisnou šířku** (~50° N). Pro ČR OK, pro jinou šířku zkreslí špatně.
- **Held-Karp práh `n > 13`** → nearest-neighbor fallback (`core_logic.py:90`).
- **Rok = `52` týdnů** ve `weeks_between` (`core_logic.py:520`, dokumentované omezení).
- **Never-visited floor `260`** týdnů (`runtime_state.py:75`).
- **GPS sentinel `(0,0)` = neznámá poloha** (`core_logic.py:220,224`).
- **Day capacity `per_day_target − 1`** (`core_logic.py:438`).

### Pevný vokabulář (string enumy = pevná business pravidla)
- Klasifikace `"A"` jako jediná bonusová třída (`core_logic.py:199`, `planning_engine.py:494`).
- Category rules: `"CORE"`, `"EXCLUDE"`, `"NORMAL"`, `"STARTS_1"`, `"*"` (`core_logic.py:119-123`).
- Cadence: `intervalType ∈ {ONCE_PER_CAMPAIGN, RECURRING}`, `guaranteeType = HARD`, `scope ∈ {CATEGORY, CATEGORYPREFIX, MARKET}`, `dedupBy = ADDRESS`, override `YES` (`planning_engine.py:250-276`, `core_logic.py:243-245,326`).
- Override typy `FORCE_INCLUDE` / `FORCE_EXCLUDE` (`planning_engine.py:395,399`).
- Aktivita `"LOS"` / `"LOT"` (`planning_engine.py:320-323`).
- Lifecycle `Draft/Published/Active/Closed` (`planning_engine.py:206,678`; `core_logic.py:557-565`).
- Status `"Active"` (`planning_engine.py:385`).
- Filtr aktivní = hodnota `"YES"` (`planning_engine.py:220,229,250`).

### Pevné struktury schématu
- POS_MASTER hlavička = 39 pevných názvů sloupců (`runtime_state.py:POS_MASTER_HEADER`).
- MANAGER_PLAN = 17 pevných sloupců v pevném pořadí (`planning_engine.py:625-629`).

---

## Odpovědi na klíčové otázky

### Lze přes konfiguraci měnit prakticky celé chování planneru?
**Chování ANO, strukturu NE.** Konfigurovatelné je: **které** POS se plánují
(filtry, blacklist, override), **jak silně** se co váží (SCORE_PROFILES),
**kadence** (CADENCE_RULES + SQLite overlay), **kapacita** (per-technik/týden),
**hold-back**, **GPS bonus**, **geo-cluster**, **urgency**, **premium práh**,
**okno a délka** kampaně. To je široký prostor.

**Neměnitelné configem** je **tvar algoritmu**: aditivní scoring vzorec se
čtyřmi termíny (`core_logic.py:197-202`), greedy výběr podle skóre
(`core_logic.py:355-358`), day-assignment heuristika (`geo_days`), distance
model, −1e6 penalizace, a všechny string enumy. Uživatel může měnit
**koeficienty**, ne **rovnice**.

### Které části jsou stále pevně zakódované?
1. **Scoring rovnice** — 4 aditivní termíny, řády vah jako implicitní priorita (`core_logic.py:188-203`).
2. **−1 000 000 gap penalizace** jako „měkký zákaz" (`core_logic.py:193`).
3. **Distance model 111/72, Euklid** — bez skutečných silnic (`core_logic.py:42-45`).
4. **Day-assignment heuristika** — kotva=skóre + nearest-anchor, bez TSP (`core_logic.py:406-474`).
5. **Greedy týdenní výběr** — sort, ne optimalizace (`core_logic.py:338-358`).
6. **Held-Karp práh 13, rok=52 týdnů** (`core_logic.py:90,520`).
7. **Derivace weeksSinceLastVisit** (`runtime_state.py:45-115`).
8. **Celý string vokabulář** (třídy, statusy, scope, typy).
9. **Column mapping** POS_MASTER / MANAGER_PLAN.

### Co parametrizovat pro jiné firmy než SalesApp?
- **Mapování sloupců** importu (dnes SalesApp-specifické názvy → `pos_master` a POS_MASTER hlavička; `import_engine.py`, `runtime_state.POS_MASTER_HEADER`).
- **Zdroj a definice `weeksSinceLastVisit`** (dnes `salesapp_visits`, jakákoli role; `runtime_state.py:50`).
- **Vokabulář klasifikace** — „A" jako prémiová třída je SalesApp konvence (`core_logic.py:199`).
- **Category / cadence vokabulář** a scope modely.
- **Distance konstanty** podle zeměpisné šířky trhu (`core_logic.py:44`).
- **Model regionu / střediska** (RSA/RSC) — dnes implicitní.
- **Koncept „kampaně" (LOS/LOT)** jako plánovací horizont.

### Co by bylo potřeba, aby planner fungoval jako obecný Route Optimization Engine?
1. **Skutečné silniční vzdálenosti/časy** — v repu už je `backend/osrm.py` a
   `travel_model.py`, ale **planning core je nepoužívá** (počítá Euklid,
   `core_logic.py:42`). Napojit routing do scoringu i day-assignmentu.
2. **Sekvenování zastávek (intra-day TSP)** — dnes se **záměrně nedělá**
   (neznámý start, `core_logic.py:407-418`). Přidat start-point technika jako
   parametr → skutečné pořadí trasy.
3. **Kapacita jako čas, ne počet návštěv** — dnes `capacity = návštěvy`
   (`core_logic.py:496-514`). Přejít na časové/km rozpočty + doba návštěvy
   (model `duration.py` existuje).
4. **Časová okna a tvrdá omezení** (otevírací doby, priority) jako **constraints**,
   ne měkké velké váhy.
5. **Vícekriteriální optimalizace** (hodnota vs km vs čas vs férovost) místo
   jednoho skalárního skóre a greedy sortu.
6. **Vozidlo/technik jako zdroj** s domovskou lokací, směnami, dovednostmi
   (VRP model), ne jen `groups[tech]` bucket.
7. **Řešič** (OR-Tools / metaheuristika) místo greedy — s existujícím scoringem
   jako počáteční řešení.

---

## Kapitola: Návrhy pro verzi 2 (seřazeno dle ROI)

ROI = (dopad na kvalitu plánu) / (náročnost). Řazeno od nejvyššího.

### 🟢 Vysoký ROI (nízká náročnost, velký dopad)

1. **Napojit reálné vzdálenosti do geo-cluster bonusu a day-assignmentu.**
   `osrm.py`/`travel_model.py` už existují; core počítá Euklid
   (`core_logic.py:42`). Náhrada distance funkce = okamžité zpřesnění hustoty
   i rozřazení na dny. *Nízká náročnost, vysoký dopad.*

2. **Validace configu místo tichých fallbacků.** `setting(name, fallback)`
   tiše dosadí kódový default (`planning_engine.py:125-130`). Explicitní chyba/
   log, když sheet chybí → uživatel ví, s jakými vahami plán vznikl. *Triviální,
   velký dopad na důvěru.*

3. **Cadence match dle `priority`, ne dle pořadí řádků.** Dnes první match
   vyhraje (`planning_engine.py:448-461`). Řadit kandidátní pravidla podle
   `priority` (pole už existuje, `core_logic.py:177`). *Malá změna, odstraní
   skrytou závislost na pořadí.*

4. **Jednotný model shody filtrů.** Terminál=substring vs trh=exact
   (`planning_engine.py:225,233`). Sjednotit na množinovou příslušnost →
   předvídatelnost. *Malá změna.*

5. **GPS bonus respektuje kapacitu.** Dnes přidává **nad** limit
   (`planning_engine.py:598`) → týden může překročit deklarovanou kapacitu.
   Přidat cap na výslednou velikost. *Malá změna, opravuje rozpočet dne.*

### 🟡 Střední ROI

6. **Kapacita jako čas/km, ne počet návštěv.** Model `duration.py` (predikce
   trvání) existuje; `resolve_capacity` vrací počet (`core_logic.py:496-514`).
   Přechod na časový rozpočet zreálný denní plán. *Střední náročnost, velký
   dopad na realističnost.*

7. **Intra-day TSP sekvenování.** Přidat start-point technika (parametr) →
   pořadí zastávek, ne jen shluk (`core_logic.py:407-418`). Held-Karp/NN už v
   `core_logic.py:54-110` existuje pro odhad — použít i pro reálné pořadí.
   *Střední náročnost.*

8. **Konvexní urgency ramp.** Lineární ramp (`core_logic.py:311-316`)
   podceňuje „na poslední chvíli". Konvexní/riziková funkce. *Malá změna,
   střední dopad na dodržení kadence.*

9. **Geo-cluster vážit marginální úsporou km, ne počtem sousedů.** Dnes
   odměňuje hustotu (`core_logic.py:213-228`) i když shluk zvyšuje trasu.
   *Střední náročnost.*

10. **GPS/fuzzy address dedup.** Exact string ulice+město selže na překlepech
    (`core_logic.py:330`). Dedup podle GPS blízkosti. *Střední náročnost.*

### 🔴 Nízký ROI / velká investice (strategické V2)

11. **Nahradit greedy výběr kapacitně-omezenou optimalizací** (OR-Tools/
    metaheuristika), se stávajícím skóre jako počáteční řešení
    (`core_logic.py:338-358`). *Vysoká náročnost, vysoký strop kvality —
    ale mění jádro; nasadit až s A/B srovnáním proti greedy.*

12. **Vícekriteriální skóre místo sumy řádů.** Lexikografické tuple + tvrdé
    constraints místo 1e8/1e7 vah a −1e6 penalizace (`core_logic.py:188-203`).
    *Vysoká náročnost — refactor scoringu i všech verifikačních harnessů.*

13. **Plná parametrizace pro multi-tenant** (mapování sloupců, vokabulář tříd/
    kategorií, distance konstanty dle šířky, zdroj cadence). Umožní nasazení
    mimo SalesApp. *Vysoká náročnost, ROI závisí na plánu prodeje produktu.*

14. **VRP model zdrojů** (domovská lokace, směny, dovednosti, vozidla) místo
    `groups[tech]` bucketů. *Nejvyšší náročnost — jádro obecného route
    optimizeru.*

---

### Poznámka o kvalitě algoritmu (celkové hodnocení)

Engine je **transparentní pravidlový skórovací systém s greedy výběrem**, ne
matematický optimalizátor. Jeho síla: **předvídatelnost, vysvětlitelnost,
konfigurovatelnost chování** a bohaté observability háky (`candidates_out`/
`rejected_out`, `_assert_breakdown` na `planning_engine.py:48-67`). Jeho strop:
kvalita je omezena tam, kde **greedy + Euklid + počet-návštěv** nahrazují
**routing + optimalizaci + časový rozpočet**. Největší ROI leží v napojení už
existujících komponent (`osrm`, `travel_model`, `duration`) do jádra, které je
dnes od nich odříznuté.
