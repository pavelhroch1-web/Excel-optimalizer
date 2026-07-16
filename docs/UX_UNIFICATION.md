# Sjednocení UX — návrh (jeden produkt, ne 6 obrazovek)

Cíl: **když otevřu libovolnou část aplikace, okamžitě vím, jak funguje, protože
všechny ostatní fungují stejně.** Tenhle dokument není o CSS — je o jednotném
chování: navigace, struktura obrazovky, ovládací prvky, názvosloví, akce,
dialogy, filtrování a tabulky.

Formát u každé oblasti: **Co je dnes špatně → Cílový standard → Jak to sjednotit.**

---

## 0. Uživatelský workflow (jak appku čte běžný uživatel)

Ráno, běžný den vedoucího:

1. **Otevře „Přehled"** → chce vidět *co dnes hoří* (nikdy nenavštívené, po termínu).
2. **Import dat** → nahraje čerstvý SalesApp export, případně hromadné úkoly.
3. **TourPlan** → zkontroluje/vygeneruje plán, vyřeší výjimky, publikuje.
4. **Analytika / Měsíční souhrn** → zpětně vyhodnotí, kdo potřebuje pozornost.
5. **Nastavení** → jen občas, když ladí pravidla.

Tenhle tok je správný a necháváme ho. Problém není *pořadí sekcí*, ale že
**každá sekce se ovládá jinak**. To řešíme níže.

---

## 1. Navigace

**Dnes:** 6 položek v levém menu (Přehled, Import dat, TourPlan, Analytika,
Měsíční souhrn, Nastavení). To je dobré. Problém je *uvnitř*: **TourPlan má 12
naskládaných karet** (kroky 1–9 + Route Planner + Cloud + Co kdyby), zatímco
Analytika má 3. Uživatel se v dlouhém sloupci karet ztrácí a neví, co je hlavní
tok a co pokročilá výjimka.

**Cílový standard:**
- Levé menu (6 sekcí) zůstává jako **primární navigace**.
- Uvnitř těžkých sekcí zavést **sekundární navigaci = záložky (taby)** místo
  nekonečného sloupce karet. Stejný vizuál jako už existující `.td-tabs` /
  `.cfg-level-tabs` (Nastavení už to tak má: „Plánovací model / Pokročilé").
- Každá sekce má **max 1 obrazovku „na první dobrou"** a zbytek schová za tab
  „Pokročilé".

**Návrh tabů uvnitř sekcí:**

| Sekce | Primární tab | Další taby |
|-------|--------------|-----------|
| **TourPlan** | **Plán** (nahrát → generovat → návrh → publikovat) | Pravidla a výjimky · Predikce a scénáře · Historie · Pokročilé (Cloud, Co kdyby) |
| **Analytika** | **Tým** (dashboard techniků) | Den technika · Plán vs. realita |
| **Nastavení** | Plánovací model | Pokročilé nastavení enginu *(už hotovo)* |
| Přehled / Import / Měsíční souhrn | beze změny (jsou dost fokusované) | — |

Tím se z 12-kartového TourPlanu stane 5 přehledných tabů, kde primární tab je
lineární tok 1→publikace.

---

## 2. Struktura obrazovky (jeden skelet)

**Dnes:** Dashboard nemá `view-head`, ostatní ano; Měsíční souhrn je celý
dynamický bez karet; karty někde mají číslované kroky, jinde ne.

**Cílový standard — každá obrazovka má stejnou kostru:**

```
┌ view-head ─────────────────────────────┐
│  H1  Název sekce                        │
│  p   Jedna věta: k čemu sekce je        │
├ (sub-tabs, nepovinné) ─────────────────┤
├ card ──────────────────────────────────┤
│  H2  Název karty     [badge]  [toolbar] │   ← toolbar = akce vpravo
│  hint  Jedna věta kontextu              │
│  [filter-bar]                           │   ← jednotný, viz §7
│  ...obsah (tabulka / mapa / graf)...    │
│  [feedback]                             │   ← jednotný toast/řádek, viz §6
└─────────────────────────────────────────┘
```

**Karta = jednotka.** Vždy: nadpis + (badge) + (toolbar akcí vpravo) + (hint) +
(filtr) + obsah + zpětná vazba. Žádná karta nemá vlastní layout.

---

## 3. Ovládací prvky (toolbar karty)

**Dnes:** akce jsou různě — někde `<form class="row">` s tlačítkem uprostřed,
někde tlačítko dole, někde nahoře. Primární akce není konzistentně poznat.

**Cílový standard:**
- Každá karta má **jeden toolbar vpravo nahoře** (vedle H2).
- **Jedna primární akce** (`.primary`), zbytek **sekundární** (`.ghost`),
  destruktivní `.danger`. Nikdy 2 primární akce v jedné kartě.
- Pořadí zleva: sekundární … sekundární · **Primární**.
- Formulářové vstupy (týden, technik…) jdou do **filter-baru** (§7), ne
  wperemíchané s akčními tlačítky.

---

## 4. Názvosloví (jeden slovník)

**Dnes (skutečné nálezy):** `TourPlan` × `Tour Plán` × `tour plán`; **`Draft`
× `Návrh`** ve stejné sekci; „Realita techniků" (podstatné jméno jako tlačítko).

**Cílový kanón — používat všude tyto tvary:**

| Používej | Nepoužívej |
|----------|-----------|
| **TourPlan** (jedno slovo, velké P) | Tour Plán, tour plán, Tourplan |
| **Návrh** (rozpracovaný plán) | Draft |
| **Publikovaná verze** (immutable) | snapshot (v UI), verze × publikace mix |
| **POS** | prodejna/terminál střídavě (POS je entita) |
| **Technik** | pracovník, TECHNIK (jen role interně) |
| **Návštěva** | visit |
| **Kampaň** | Activity plan (to je jen zdroj) |
| **Úkol** (Task Engine) | task |
| **Segment** | — |
| **Pokrytí** | coverage (v UI česky) |

**Entity mají vždy stejný název** napříč tabulkami, filtry i detaily.

---

## 5. Akce (jedna sada sloves)

**Dnes:** Načíst / Obnovit / Zobrazit (3 slovesa pro totéž = načíst data);
Spočítat / Vyhodnotit / Analyzovat / Spustit / Generovat (5 pro „spusť výpočet");
Stáhnout návrh (.xlsx) × Stáhnout Excel.

**Cílový kanón — každá akce má JEDNO sloveso a jedno chování:**

| Akce | Sloveso v UI | Kdy | Vzhled |
|------|-------------|-----|--------|
| Vytvořit záznam | **Nový** | přidat POS/úkol/kampaň | primary |
| Upravit | **Upravit** | inline nebo dialog | ghost |
| Odstranit | **Smazat** | vždy s potvrzením (§6) | ghost danger |
| Načíst z Excelu | **Import** | drag-drop / soubor | primary |
| Uložit do Excelu | **Export** | vždy „Export (.xlsx)" | ghost |
| Přepočítat metriky | **Přepočítat** | po importu / ručně | ghost |
| Znovu načíst pohled | **Obnovit** | refresh dat na obrazovce | ghost |
| Spustit výpočet enginu | **Spustit** | scénář, predikce, analýza | primary |
| Vytvořit plán | **Generovat** | Planning Engine | primary |
| Zmrazit verzi | **Publikovat** | immutable | primary danger |

Pravidlo: **„Načíst" a „Zobrazit" jako akce mizí.** Když karta potřebuje jen
osvěžit data → **Obnovit**. Když počítá engine → **Spustit** / **Generovat**.

---

## 6. Dialogy, potvrzení a zpětná vazba

**Dnes:** nativní `confirm()` / `alert()` (nehodí se k vyladěnému UI), a
**dva systémy hlášek** — inline `.result` (~30 míst) + `toast` (3 místa).
Některé destruktivní akce se ptají, jiné ne.

**Cílový standard:**
- **Jeden potvrzovací dialog** (vlastní modal, ne nativní `confirm`).
  - Vždy pojmenuje **objekt i následek**: „Smazat úkol pro POS 71001302? Nelze
    vrátit zpět."
  - Destruktivní tlačítko `.danger`, neutrální „Zrušit" vlevo.
- **Pravidlo potvrzování:** potvrzení **jen** u nevratných/hromadných akcí
  (Smazat, Publikovat, Zrušit všechna vyřazení, přepsání dat). Běžné akce ne.
- **Jeden systém hlášek = toast** (úspěch/chyba/info), jednotné umístění a barvy.
  Inline `.result` se ponechá jen tam, kde je výsledek *součástí obsahu*
  (např. „nalezeno 42 kandidátů"), ne pro success/error.

---

## 7. Filtrování (jeden filter-bar)

**Dnes:** Přehled má **živé pilulky** (klik = hned filtr); analytické karty mají
**formulář** (vyplň týden/technika → klikni tlačítko → objeví se výsledek);
Měsíční souhrn má vlastní dynamický `sum-filters`. Tři různé modely.

**Cílový standard — rozlišit dvě situace a pro každou jeden vzor:**

1. **Filtr nad už načtenými daty** → **živý filter-bar** (`.live-filters` styl:
   pilulky + `.fsel` selecty). Klik = okamžitá změna, žádné tlačítko.
   Použít v: Přehled, Měsíční souhrn, tabulky kandidátů/návrhu, Analytika-tým.
2. **Dotaz, který spouští těžký výpočet enginu** (predikce, scénář, generování)
   → **query-bar + tlačítko „Spustit"**. Tady je tlačítko správně (výpočet je
   drahý), ale bar má stejný vzhled jako živý (stejné `.fsel`, stejné rozložení).

Vizuálně jsou **oba bary identické**; liší se jen tím, jestli je na konci
tlačítko „Spustit". Uživatel pozná „tady se počítá" podle tlačítka, ne podle
jiného vzhledu.

---

## 8. Tabulky a detaily

**Tabulky:** ✅ *už sjednoceno* — všechny datové mřížky sdílí jeden vzhled
(velká tlumená záhlaví, jednotné odsazení, tabular-nums). Udržet: každá nová
tabulka = `<table>` bez vlastního stylu, obalená scrollovatelným wrapperem.

**Detaily entit (drill-in):** dnes **dva různé modely** — POS = boční panel
(`pos-detail-overlay`), technik = celoobrazovkový tabovaný overlay
(`tech-detail-overlay`).

**Cílový standard:**
- **Jeden vzor otevírání detailu:** boční panel zprava pro *rychlý náhled*
  (POS), celoobrazovkový tab-overlay pro *hloubkovou analýzu* (technik). To je
  OK **rozdíl podle hloubky**, ale musí být:
  - **stejná hlavička** (název + ✕ vpravo, případně taby),
  - **stejné zavírání** (✕ i klik mimo i Esc),
  - **stejné názvosloví akcí** uvnitř.
- Klik na entitu **kdekoli** (mapa, tabulka, KPI) otevře **tentýž** detail té
  entity. (Už teď platí pro POS — rozšířit důsledně.)

---

## Shrnutí priorit (návrh pořadí implementace)

Seřazeno podle poměru *dopad na pocit „jeden produkt"* / *riziko*:

| # | Krok | Dopad | Riziko | Ověřitelné tady |
|---|------|-------|--------|-----------------|
| A | **Názvosloví + slovesa** (§4, §5) — sjednotit texty tlačítek a názvy | vysoký | nízké | ano (text) |
| B | **Jeden toast + potvrzovací dialog** (§6) — nahradit `confirm`/`alert` | vysoký | střední | částečně |
| C | **Toolbar karty** (§3) — akce vpravo, 1 primární | střední | nízké | vizuálně ne |
| D | **Taby v TourPlan/Analytika** (§1) — z 12 karet 5 tabů | vysoký | střední | vizuálně ne |
| E | **Filter-bar sjednocení** (§7) — stejný vzhled, live vs Spustit | střední | střední | vizuálně ne |
| F | **Detail drill-in** (§8) — stejná hlavička/zavírání | nízký | nízké | vizuálně ne |

**Doporučení:** začít **A + B** (nejvyšší dopad, nejmíň rizika, dají se ověřit
i bez renderu), pak D (taby) jako největší skok v přehlednosti. C/E/F doladit
podle tvých reálných připomínek při testování.

> Nic z toho nemění Planning Engine ani byznys logiku — jde čistě o prezentační
> a interakční vrstvu. Core testy zůstávají 120/0.

---

## Provedeno (kanón pro další moduly)

Tohle je už v kódu a **každý nový modul to má používat** místo vlastního řešení:

- **Tabulky** → prostý `<table>` (žádná vlastní třída) obalený scrollovatelným
  wrapperem; vzhled je jednotný globálně.
- **Zpětná vazba** → `toast(msg, "ok" | "err" | "info")`. Žádné nativní `alert`.
- **Potvrzení nevratných akcí** → `await confirmDialog({ title, body,
  confirmText, danger })` (Promise<bool>, zavírá Esc/Enter/klik mimo). Žádné
  nativní `confirm`. Pravidlo: potvrzuj **jen** nevratné/hromadné akce.
- **Stavy panelu** → `showState(el, "loading" | "empty" | "error", msg)` nebo
  `stateHTML(...)`. Jeden vzhled pro „načítám / prázdno / chyba" všude.
- **Zavírání detailů** → ✕ i klik mimo i **Esc** (platí pro POS i technik detail).
- **Názvosloví + slovesa** → viz §4 a §5 (TourPlan, Návrh, Obnovit/Spustit/…).

### Zbývá (další kola)
- C (toolbar karet), D (taby), E (filter-bary), F (sjednocení hlaviček detailů).
- Drobnost: 1× nativní `prompt()` (přehození POS na technika z fix-banneru) →
  nahradit malým input-modalem ve stylu `confirmDialog`.
