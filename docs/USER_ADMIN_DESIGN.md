# Návrh: Centrální správa uživatelů a oprávnění

Návrh (ne implementace) centrálního místa pro správu osob, rolí a přístupů.
Rozlišuje **co už backend umí dnes** (a jde rovnou zpřístupnit) od **co je
nová vrstva** (autentizace/oprávnění), kterou je potřeba teprve postavit.

---

## 1. Co už dnes existuje (lze použít hned, bez nové logiky)

**Tabulka `technicians`** + `GET /api/technicians`, `PUT /api/technicians/{name}`
umí per osobu:
- **role** (`role`, `manual_role`) — TECHNIK / OZ / ADMIN / MANAGER, ruční
  nastavení přežije import (auto-pravidlo 3xx = OZ jinak).
- **deaktivace** (`active`) — neaktivní osoba vypadne z plánování i metrik.
- **blacklist** (`excluded`) — testovací/služební účty skryté ze všech přehledů,
  alertů, mapy i plánu.
- **region**, **kapacita/týden**.

To pokrývá požadavky: *změnit roli napříč systémem*, *deaktivovat*, *blacklist*.
Dnes je to v **Nastavení → Technici** (tabulka s dropdownem role + checkboxy
Aktivní / Vyřadit). Chybí jen **zvýraznit to jako centrální správu** a doplnit
filtr/hromadné akce.

**Přihlášení** dnes: jediné sdílené heslo (`/api/login`, `auth.py`) — není pojem
individuálního uživatelského účtu ani oprávnění.

---

## 2. Co je nová vrstva (musí se teprve postavit)

Tyto požadavky **v backendu neexistují** a jsou to nové entity (autentizace +
autorizace), ne úprava plánovače:

| Požadavek | Stav | Co je potřeba |
|---|---|---|
| Individuální uživatelské účty | ❌ (jen 1 heslo) | tabulka `users` (login, hash hesla, stav) |
| Změna oprávnění uživatele | ❌ | model rolí→oprávnění |
| Zakázat přístup do celého systému | ❌ | příznak `blocked` na účtu + kontrola při loginu |
| Zakázat pouze vybrané moduly | ❌ | mapa oprávnění na moduly/route |
| Oprávnění napříč aplikací | ❌ | middleware, který route/endpoint hlídá |

---

## 3. Navržený datový model (minimální, deterministický)

```
users            (id, username, display_name, password_hash, status, created_at)
                 status ∈ {active, disabled, blocked}
roles            (id, name)                  -- ADMIN / MANAGER / PLANNER / VIEWER
role_permissions (role_id, permission)       -- např. "planner.generate", "settings.edit"
user_roles       (user_id, role_id)
module_access    (user_id, module, allowed)  -- override na úrovni modulu (import/tourplan/…)
```

Osoby v terénu (`technicians`) zůstávají oddělené od **uživatelů systému**
(`users`) — technik nemusí mít login. Provázání volitelné (`users.technician_id`).

**Oprávnění = množina stringů** (např. `tourplan.view`, `tourplan.generate`,
`settings.edit`, `users.manage`). Modul je nejhrubší úroveň (skryje celou sekci),
permission jemná (skryje akci). Vše deterministické, žádná role-hierarchie magie.

---

## 4. Vynucování (jednoduché, auditovatelné)

- **Backend:** jeden dependency `require_permission("...")` na endpointech (dnes
  už existuje `require_auth`, jen jednoúrovňový). Login vydá session s rolí →
  oprávnění se odvodí z `role_permissions` + `module_access`.
- **Frontend:** navigační shell skryje sekce podle `module_access`; akce podle
  permissions (tlačítko „Generovat" jen s `tourplan.generate`). Skrytí je UX;
  skutečné vynucení je na backendu.
- **Blok systému:** `users.status = blocked` → login odmítnut. Deaktivace →
  `disabled` (dočasné). Blacklist osoby v terénu zůstává `technicians.excluded`.

---

## 5. Navržená obrazovka „Správa uživatelů" (Nastavení)

Jedna tabulka, řádek = uživatel:

| Uživatel | Role | Moduly | Stav | Akce |
|---|---|---|---|---|
| jmeno | [dropdown role] | [chipy modulů ✓/✗] | aktivní/deaktivovat/blokovat | uložit |

+ nad tím sekce **Osoby v terénu** (dnešní `technicians`) — role napříč
systémem, deaktivace, blacklist — kterou lze **hned** povýšit z Nastavení →
Technici na plnohodnotnou centrální správu.

---

## 6. Doporučené fáze (podle rizika)

1. **Fáze 0 (hned, bez nové logiky):** zvýraznit dnešní správu techniků
   (role / aktivní / blacklist) jako „Centrální správa osob", přidat filtr role
   a hromadné akce. *Čistě frontend nad existujícím `/api/technicians`.*
2. **Fáze 1 (nová, malá):** `users` + individuální login + stav
   active/disabled/blocked. Nahradí jediné sdílené heslo.
3. **Fáze 2:** role → permissions + `require_permission` na endpointech.
4. **Fáze 3:** `module_access` overrides + skrývání modulů ve shellu.

Fáze 1–3 jsou nová backendová logika (auth/authz) — mimo plánovač, takže
nekolidují s pravidlem „neměnit Planning Engine". Doporučuji je až po odsouhlasení
tohoto návrhu; Fáze 0 jde udělat okamžitě.
