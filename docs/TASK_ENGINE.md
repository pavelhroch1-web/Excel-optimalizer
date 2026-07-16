# Task Engine — servis + kampaně + materiál v jedné cestě

Neplánujeme jen servisní návštěvy. Plánujeme i **obchodní kampaně** a **logistické
úkoly (materiál)**. Klíčová business hodnota není optimalizace trasy, ale
**optimalizace počtu návštěv** — když už technik jede na daný POS, udělá tam
**všechno otevřené** (servis + kampaň + materiál) v jedné cestě.

Tento dokument popisuje, jak je to zakotvené v architektuře — **bez jediné změny
ověřeného Planning Enginu** (core testy zůstávají 120/0).

---

## 1. Datový model (vše konfigurovatelné, nic natvrdo)

**`task_types`** — typy aktivit (konfigurace „z Velína"). Každý typ má:

| pole | význam |
|------|--------|
| `name` | název (Servisní oprava, Předání poukázek, Kotouče…) |
| `category` | **`service` \| `campaign` \| `material` \| `other`** — tři business koše |
| `default_minutes` | odhad času (vlastní pro každý typ) |
| `default_priority` | priorita 1–5 (1 = nejvyšší) |
| `combinable` | lze splnit při běžné návštěvě? (piggyback vs vlastní výjezd) |
| `active` | zapnuto |

**`tasks`** — konkrétní úkol = instance typu na jednom POS: `type_id`, `pos_id`,
`deadline`, `quantity` (množství!), `priority`/`est_minutes` (override typu),
`note`, `status` (`open`/`done`/`cancelled`).

Nový typ materiálu nebo kampaně = **nový řádek konfigurace**, ne vývoj. „Materiál"
i „kampaň" i „servis" jsou jen `category` nad stejným generickým enginem.

### Zakládání (typický scénář)
1. Nahraju Excel (POS + množství + poznámka).
2. Jednou nastavím typ aktivity, deadline, prioritu, odhad času.
3. Systém založí úkol pro **všechny POS** (`bulk_create` / `parse_bulk_excel`).

Stejným způsobem materiál (kotouče, stojánky, letáky, poukázky) — každý typ má
vlastní prioritu a odhad času.

---

## 2. Most do Planning Enginu (engine s úkoly automaticky počítá)

**Zásada:** engine se nemění. Úkoly ho ovlivňují **jen přes stávající
config-overlay** — přesně jako OZ-priorita (`db_state._apply_priority`).

Modul **`task_bridge.apply_to_state(state)`** (volaný v `db_state.configure()`):

- POS má otevřený úkol, který **vyžaduje vlastní výjezd** → nastaví se
  `managerOverrideType = FORCE_INCLUDE` → engine mu **garantuje místo v plánu**.
- „Vyžaduje vlastní výjezd" = úkol **není combinable**, **nebo** je **do
  `deadline` ≤ 14 dní** (hrozí, že běžná návštěva nedorazí včas).
- `FORCE_EXCLUDE` (ruční zákaz manažera) vždy vyhrává nad mostem.

Dvě situace, jedno pravidlo:

| situace | co udělá most | proč |
|---------|---------------|------|
| combinable úkol, deadline daleko | **nic** | piggybackuje zdarma, až tam technik pojede |
| not-combinable **nebo** blízko deadline | **FORCE_INCLUDE** | jinak by se výjezd nenaplánoval včas |

> Engine tedy „počítá s úkoly" tak, že díky mostu **sám naplánuje návštěvu** tam,
> kde je úkol naléhavý — a přitom jeho algoritmus zůstává beze změny.

---

## 3. Sbalení úkolů na zastávku (bundling)

Když je POS v plánu (ať už kvůli servisu, kadenci, nebo mostu výše), přidá se k
zastávce **balík všech otevřených úkolů** toho POS.

**`tasks.bundle_for_pos(pos_id)`** vrací úkoly seskupené do tří košů:

```json
{
  "count": 3, "topPriority": 2, "hasDedicated": true, "totalMinutes": 39,
  "groups": {
    "service":  [{ "type": "Instalace služby", "priority": 2, "estMinutes": 30 }],
    "campaign": [{ "type": "Jednorázová akce", "quantity": 10, "priority": 3 }],
    "material": [{ "type": "Kotouče", "quantity": 50, "priority": 3 }]
  },
  "summary": "Opravit: Instalace služby | Kampaň: Jednorázová akce 10× | Materiál: Kotouče 50×"
}
```

Tenhle balík se propisuje na **dvou místech** (obojí jen prezentační vrstva):

- **Náhled návrhu / TourPlan** — `plan_io.read_enriched_draft()` přidá `tasks`
  (balík) ke každému řádku plánu. UI u zastávky ukáže: co opravit, jakou kampaň,
  jaký materiál, **v jakém množství**, s jakou **prioritou**.
- **Export (.xlsx)** — `_stream_sheet` přidá do MANAGER_PLAN sloupec **`ÚKOLY`**
  s jednořádkovým souhrnem balíku, aby to technik viděl i v Excelu/tisku.

---

## 4. Co technik na výstupu jasně vidí

V TourPlanu i v exportu u každé zastávky:

- **co opravit** (koš `service`),
- **jakou kampaň provést** (koš `campaign`),
- **jaký materiál předat** (koš `material`),
- **v jakém množství** (`quantity`),
- **s jakou prioritou** (`priority`, 1 = nejvyšší; `topPriority` balíku).

---

## 5. Proč to takhle (a co to nezmění)

- **Nezvyšujeme počet výjezdů** — combinable úkoly jedou „na svezenou".
- **Naléhavé úkoly se neztratí** — most jim přes `FORCE_INCLUDE` zajistí výjezd.
- **Jedna cesta = víc činností** — bundling spojí servis + kampaň + materiál.
- **Engine se nemění** — všechno běží přes overlay (`configure()`) a přes
  prezentační enrichment (draft/TourPlan/export). Core testy 120/0.
- **Aktualizace bezpečné** — `task_types.category` se doplňuje aditivní migrací;
  stará data i konfigurace zůstávají.

---

## 6. Stav implementace

| Vrstva | Stav |
|--------|------|
| `task_types` + `tasks` + bulk import (POS + množství) | ✅ hotovo |
| `category` (service/campaign/material) + aditivní migrace | ✅ hotovo |
| Seed typů pro servis / kampaň / materiál | ✅ hotovo |
| Most do enginu (`task_bridge` → FORCE_INCLUDE) | ✅ hotovo + ověřeno |
| Bundling do náhledu návrhu (`read_enriched_draft`) | ✅ hotovo |
| Sloupec `ÚKOLY` v exportu | ✅ hotovo |
| **UI:** zobrazení balíku úkolů u zastávky v TourPlanu | ⏳ další krok (frontend) |
| **UI:** správa `category` v konfiguraci typů | ⏳ další krok (frontend) |

Backend počítá s úkoly od začátku, jak sis přál. Zbývá je vizuálně vytáhnout do
zastávek v TourPlan UI — to doladíme, až appku spustíš (patří to k druhému kolu
UX).
