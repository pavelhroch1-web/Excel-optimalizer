# UX architektura Planner Studia + centrální Administrace

Návrh (ne implementace). Pohled produktového UX architekta: udělat z Planner
Studia **jeden konzistentní produkt**, ne sadu 18 samostatných karet. Vše staví
na **existujícím backendu** — žádná nová business logika.

Řídící otázka pro každou obrazovku: *„Co chce uživatel právě teď udělat a jak
mu backend může maximálně pomoct?"*

---

## Část 1 — Diagnóza současného stavu

Planner Studio dnes = jeden `tourplan` view s **18 kartami** napříč 6 fázemi
(Data/Parametry/Scénáře/Review/Úpravy/Publish) **plus** Wizard (7 kroků). Vznikl
nabalováním (lineární 7 kroků → fáze → wizard → další panely). Výsledek:

**Aktuální karty:** Coverage podle segmentů · Kampaně · Vstupní data ·
2·Plánovací filtry · 3·Override/priority · 4·Strategie a pre-flight ·
5·Predikce a scénáře · Plánovací simulace · 6·Generovat · 7·Kandidáti POS ·
8·Návrh a ruční úpravy · Časová proveditelnost · Kritické POS mimo plán ·
9·Publikovat · Route Planner · Historie · Cloud · Co kdyby.

### 1. Kde je ovládání zbytečně složité
- **Dva soupeřící vstupy do plánování:** Wizard (vede krok po kroku) i fázové
  karty (Plánovací filtry, Strategie, Simulace, Generovat) dělají totéž.
  Uživatel neví, co použít.
- **Zbytkové číslování** (2·, 3·, 4·… 9·) z lineárního modelu se bije s fázovým
  stepperem — dvě navigační metafory naráz.
- **Čtyři cesty jak generovat:** Wizard · „6·Generovat" · „Plánovací simulace"
  · „Cloud generate".

### 2. Kde uživatel hledá odpověď, kterou backend zná
- Historicky: sweep, advisor, unserved (teď už ve Wizardu/Review — dobře).
- Stále roztříštěné: **naučená kapacita** je jen ve Wizardu, ne u „Plánovací
  simulace"; **audit změn** (`/api/history/events`) se nevolá vůbec.

### 3. Duplicity (stejná akce na více místech)
| Akce | Kolikrát | Kde |
|---|---|---|
| Generovat | 4× | Wizard · 6·Generovat · Simulace · Cloud |
| What-if | 3× | 5·Predikce a scénáře · Co kdyby · Simulace whatif |
| Strategie/mód | 3× | 4·Strategie · Simulace sim-mode · Wizard priorita |
| Assess / scorecard | 3× | 4·Strategie pre-flight · Simulace assess · Wizard kontrola |
| Kapacita | 3× | Wizard · Simulace sim-visits · Nastavení |

### 4. Stejné informace na více místech
- **Coverage** na 4 místech: „Coverage podle segmentů" + assess + advise +
  sweep pokrytí %.
- **Kampaně** na 3: karta „Kampaně" + Wizard chipy + Nastavení model.
- **Filtry** na 2–3: „Plánovací filtry" (odkaz do Nastavení) + Wizard partneři
  + Nastavení model.

### 5. Kde backend umí, ale uživatel se nedostane
- `/api/history/events` a `/history/metrics` (audit + vývoj PPT/churn) — nevolá se.
- `/api/memory/config-diff` (co se změnilo od minula) — nevolá se.
- `/api/gis/pos/{id}` (POS na mapě) — nevolá se.
- (Detail v `BACKEND_CAPABILITY_AUDIT.md`.)

### 6. Kde jde použít existující endpoint místo nové logiky
- Sjednotit tři what-if panely na jeden `decision.what_if` / `/api/planner/whatif`.
- Jednotit tři scorecardy na jeden `/api/planner/advise` (advisor = assess + odpovědi).
- „Plánovací filtry" neposílat do Nastavení — číst/zapisovat `/api/model` inline.

### 7. Kde jde workflow zkrátit o kliknutí
- Dnešní ruční cesta: filtry → Nastavení a zpět → strategie → simulace → assess
  → generovat → review (5–6 karet, kontextové přepínání). Wizard to má na
  1 tlačítko + 7 kroků; **stačí udělat Wizard jedinou hlavní cestou** a manuální
  karty schovat pod „Pokročilé".

---

## Část 2 — Cílová UX architektura Planner Studia

**Princip:** jedna páteř o **3 fázích** (místo 6 fází + wizard + 18 karet),
každá odpovídá jedné otázce uživatele. Vše z existujících endpointů.

### ① PŘIPRAVIT & GENEROVAT — „Co chci naplánovat?"
- **Wizard je jediná hlavní cesta.** Absorbuje dnešní roztříštěné karty:
  filtry (partneři/kategorie), technici, kadence, kapacita (+ naučená + sweep),
  priorita (=strategie), pre-flight (=advisor). Většina už je hotová.
- Filtry se editují **inline ve Wizardu** přes `/api/model` (žádný skok do Nastavení).
- Jeden zelený „Vytvořit plán".

### ② ZKONTROLOVAT — „Je plán dobrý a co backend zjistil?" (jeden cockpit)
Místo 7 stohovaných karet **jeden Review se záložkami**:
- **Návrh** — tabulka + ruční úpravy (add/remove/change technik) — *existující*.
- **Pokrytí & verdikt** — sjednocené: coverage + scorecard + advisor odpovědi z
  jednoho `/api/planner/advise`. Nahradí 4 rozházené coverage pohledy.
- **Časová proveditelnost** — `/api/plan/feasibility` (hotové).
- **Kritické POS mimo plán** — `/api/planner/unserved` (hotové).
- **Kandidáti (proč)** — `/api/draft/candidates` (hotové).
Vše o jednom vygenerovaném plánu na jednom místě, žádné duplicity.

### ③ PUBLIKOVAT & EXPORTOVAT — „Hotovo."
- Publikace + export + historie verzí + Route Planner. Cloud generate a raw
  scenario what-if **pod „Pokročilé"** (expertní, ne soupeřící s hlavní cestou).

### Co to řeší z bodů 1–7
- 1 komplexita → jedna cesta (Wizard) + Review cockpit; zrušit zbytkové číslování.
- 3 duplicity → jeden generate, jeden what-if, jeden scorecard.
- 4 stejné info → jeden coverage/verdikt pohled.
- 7 kliknutí → z 5–6 karet na Wizard → Review → Publish.

### Vizuální model
```
Planner Studio
├─ ① Vytvořit plán        (Wizard — hlavní tlačítko)
├─ ② Zkontrolovat         (Review cockpit: Návrh · Pokrytí+verdikt ·
│                          Čas · Kritické POS · Kandidáti)
├─ ③ Publikovat           (Publish · Export · Historie · Route)
└─ ⌄ Pokročilé            (scenario what-if · cloud · manuální generate)
```

---

## Část 3 — Centrální Administrace

Dnes je správa roztříštěná: Nastavení (model, kadence, technici, task types,
views, engine inventory, business rules, settings, model mgmt) + samostatný
Import Center + nic pro audit. Návrh: **jedna sekce „Administrace" se záložkami**,
maximum z existujícího backendu; nové jen tam, kde backend chybí (auth).

| Záložka | Obsah | Backend | Stav |
|---|---|---|---|
| **Lidé** | Technici / OZ / manažeři: role napříč systémem, aktivní/neaktivní, **blacklist** | `/api/technicians` (GET/PUT role, active, excluded) | ✅ existuje — jen povýšit z „Nastavení → Technici" na centrální správu (filtr role, hromadné akce) |
| **Uživatelé & oprávnění** | Systémové účty, role, **oprávnění**, **přístup do modulů**, zákaz celého systému | ❌ **nová auth vrstva** (`users`, `roles`, `module_access`) | viz `USER_ADMIN_DESIGN.md` — Fáze 1–3 |
| **Plánovací model** | Filtry (terminály/partneři/kategorie), kadence, business pravidla, parametry enginu | `/api/model`, `/api/cadence`, `/api/rules/business`, `/api/engine/inventory` | ✅ existuje — sjednotit dnešní rozházené karty Nastavení |
| **Data & importy** | Import Center, stav dat, šablony | `/api/import/*`, `/api/data/summary` | ✅ existuje — přesunout sem z vlastní sekce |
| **Audit změn** | Timeline: import / publish / plánovací běh / změna konfigurace; vývoj PPT a churn sítě | `/api/history/events`, `/api/history/metrics` | ⚠️ **backend hotový, frontend NEVOLÁ** — zpřístupnit (bod „chyba návrhu") |
| **Systém** | Verze, cloud, záloha/seed | `/api/versions`, `/api/cloud/*`, `/api/status` | ✅ existuje |

### Klíčové zjednodušení
- **Osoby vs. systémoví uživatelé** oddělené: technik/OZ/manažer (kdo pracuje v
  terénu, `technicians`) ≠ uživatel systému (kdo se přihlašuje, budoucí `users`).
  Blacklist osoby = `technicians.excluded`; blok účtu = `users.status=blocked`.
- **Audit změn** je dnes největší „chyba návrhu": backend píše do `events`
  (import/publish/config/planner-run), ale žádná obrazovka to nečte. Jedna
  timeline záložka odemkne kompletní historii zdarma.
- Vše ostatní v Administraci je **konsolidace existujícího** — ne nová logika.

---

## Shrnutí

Planner Studio dnes vystavuje endpointy, ne záměry. Cílová architektura má
**3 fáze odpovídající tomu, co uživatel chce udělat** (vytvořit → zkontrolovat →
publikovat), s Wizardem jako jedinou hlavní cestou a jedním Review cockpitem
místo 18 karet a 4 způsobů generování. Administrace sjednotí dnes roztříštěnou
správu do jednoho místa a **odemkne audit změn**, který backend už píše, ale
nikdo nezobrazuje. Žádná nová business logika — jen lepší produkt nad tím, co
backend umí.
