# Field Force Optimizer — portable verze (bez instalace)

## Podmínka: na cílovém PC se NEINSTALUJE nic

Aplikace je **plně portable**. Na firemním (i zamčeném) PC:

1. rozbalíš ZIP do libovolné složky (plocha, síť, USB),
2. otevřeš složku,
3. spustíš **`FieldForceOptimizer.exe`**.

**Žádný Python, žádné PATH, žádný instalátor, žádná admin práva, žádný runtime.**
Všechna data, konfigurace i výstupy se ukládají do složky **`FieldForceData/`
vedle .exe** — appku kdykoli přesuneš/zazipuješ i s daty.

### Jak je to možné
- **Python i všechny knihovny jsou zabalené uvnitř** (`_internal/`) —
  PyInstaller `--onedir`. Cílový PC nepotřebuje nic doinstalovat.
- **Okno aplikace:** primárně nativní okno (WebView2, na Win10/11 obvykle je).
  Kdyby WebView2 chybělo, appka **bez instalace** spadne na výchozí **prohlížeč**
  (ten má každý Windows) + malé kontrolní okénko pro ukončení. Obojí jen ze
  standardní knihovny — nic se nedoinstalovává.

---

## Jak získat hotový ZIP (nepotřebuješ vůbec build stroj)

### A) Přes GitHub Actions (doporučeno — nikde nepotřebuješ Python)
1. V repu **Actions → „build-desktop-portable" → Run workflow**.
2. Po doběhnutí stáhni artefakt **`FieldForceOptimizer-portable`** (ZIP).
3. Rozbal na cílovém PC a spusť `.exe`. Hotovo.

Build běží na Windows runneru GitHubu — ty ani firemní PC Python nepotřebujete.

### B) Ručně na jakémkoli Windows PC s Pythonem (jednorázově)
Kdo má doma/jinde nezamčené Windows s Pythonem, spustí:
```
desktop_client\build_desktop_exe.bat
```
Vznikne `dist\FieldForceOptimizer\` + `dist\FieldForceOptimizer-portable.zip`.
Ten ZIP pak jen přeneseš na firemní PC.

---

## Co appka v MVP umí
- import SalesApp / PPT / POS (drag-drop, typ se pozná sám),
- přepočet: coverage podle segmentů, kapacita, predikce trvání, mikro-clustery,
- Task Engine: hromadné založení úkolů z Excelu (POS + počet),
- Měsíční souhrn: KPI, mapa sítě, coverage & riziko, detaily technik/den/POS,
- konfigurace se ukládá do složky (`FieldForceData/`).

## Poznámky
- **Mapy:** pozadí (silnice, města) a routování po silnicích potřebují internet.
  Bez internetu se trasy kreslí jako přímky a data-vrstvy fungují dál.
- **Aktualizace:** nová verze = nový ZIP; složku `FieldForceData/` si přeneseš,
  data zůstanou.
