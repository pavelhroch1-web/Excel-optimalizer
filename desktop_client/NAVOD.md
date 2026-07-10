# Tour Plán Generátor (.exe) — nejjednodušší cesta k plánu

**Tohle je ta appka, o kterou ti šlo: jedno okno, jedno tlačítko, plán na
5 týdnů jako Excel — celé lokálně na tvém PC, žádný server, žádné čekání,
žádný OOM.** Používá naprosto stejný Planning Engine jako web.

## Vyrobení .exe (jednou, na Windows PC s Pythonem)

1. Stáhni si repo (Code → Download ZIP) a rozbal, nebo měj naklonované.
2. Ve složce `desktop_client` **dvojklikni na `build_tourplan_exe.bat`**.
   Sám doinstaluje potřebné knihovny a zabalí appku.
3. Hotovo → vznikne **`dist\FieldForceTourPlan.exe`**. Tenhle jeden soubor
   zkopíruj kamkoli (i na firemní PC) a spouštěj dvojklikem — Python už
   na cílovém počítači potřeba není.

> Předpoklad pro build: Python 3.10+ z https://python.org (při instalaci
> zaškrtnout „Add python.exe to PATH"). Build si nemusíš dělat ty —
> můžu ti hotový `.exe` poslat.

## Používání

1. Spusť `FieldForceTourPlan.exe`.
2. **(Nepovinné) Data:** když chceš plán z čerstvých dat, klikni „Vybrat
   SalesApp exporty…" a vyber týdenní exporty (můžeš víc naráz). POS export
   je nepovinný — bez něj se síť vezme z posledního snapshotu.
3. **Parametry:** počáteční týden, horizont (kolik týdnů), návštěv na
   technika a týden. Override „Štolba za Dvořáka" necháš zaškrtnutý,
   pokud ho chceš.
4. **Generovat a uložit Excel** → vybereš, kam soubor uložit → za chvíli
   je hotovo. Excel má list `TOUR_PLAN` (celý plán) a `SOUHRN` (přehled).

První týden = **Dojezd** (nejzanedbanější POS), zbytek = **Kampaň**;
GECO/CORN cadence garantováno, každý POS max. 1× za horizont.

---

# Distribution Client — návod pro běžného uživatele (stažení, instalace, použití)

Tenhle návod je pro tebe jako uživatele appky, ne pro vývojáře — bez
příkazové řádky, krok za krokem. Appka běží na Windows.

## 0. Co appka umí

- **Prohlížet a exportovat** už publikovaný plán po technicích (jedno
  kliknutí = Excel soubor pro daného technika).
- **Nově i spustit celý týdenní cyklus (všech 8 kroků)** přímo nad
  workbookem, bez nutnosti otevírat Excel Online a vkládat skripty do
  Automatizace (viz varování v kroku 4).

Business logika (co, kdy a komu naplánovat) běží ve dvou nezávisle
ověřených implementacích — v Excelu (Office Scripts, hlavní/oficiální) a
teď i v appce (Python, ověřeno proti Excelu na reálných datech). Appka
nikdy nenahrazuje Excel jako zdroj pravdy, jen ho zpřístupňuje pohodlněji.

## 1. Stažení appky

Appku dostaneš jako `.zip` soubor (poslal jsem ti ho v chatu — pokud ho
nemáš po ruce, napiš a pošlu znovu). Stáhni si ho z chatu do libovolné
složky, např. `Dokumenty\FieldForceOptimizer\`.

Rozbal zip (pravé tlačítko → Extrahovat vše…). Uvnitř najdeš složku
`desktop_client` s několika `.py` soubory — to je zdrojový kód appky,
zatím ne spustitelný program.

## 2. Jednorázová příprava (jen poprvé, cca 5 minut)

Appka je napsaná v Pythonu, takže potřebuješ Python nainstalovaný na
počítači, na kterém appku **sestavíš** do `.exe`. Na počítačích, kam pak
`.exe` jen zkopíruješ, už Python potřeba není.

1. Stáhni a nainstaluj Python z **https://python.org** (tlačítko
   "Download Python"). Při instalaci **zaškrtni "Add python.exe to
   PATH"** — je to dole na první obrazovce instalátoru, snadno se
   přehlédne.
2. Otevři složku `desktop_client` (tu z rozbaleného zipu).
3. Dvojklikni na `build_exe.bat`. Otevře se černé okno, které:
   - nainstaluje potřebné knihovny (chvíli to trvá, stahuje se z
     internetu),
   - sestaví appku do jednoho souboru.
4. Až napíše `=== Hotovo ===`, najdeš výsledek v podsložce `dist` jako
   `FieldForceDistributionClient.exe`.

Tenhle krok stačí udělat znovu jen tehdy, když dostaneš novou verzi appky
(nové `.py` soubory) — jinak `.exe` zůstává použitelný napořád.

## 3. Spuštění appky

Zkopíruj `FieldForceDistributionClient.exe` kamkoli je pohodlné (plocha,
sdílená složka…) a spouštěj ho **dvojklikem** — jako kteroukoli jinou
aplikaci. Od teď už Python ani `desktop_client` složku nepotřebuješ,
`.exe` je samostatný.

## 4. Použití

1. **📂 Otevřít workbook…** (vpravo nahoře) → vyber `.xlsx` soubor
   (lokální, nebo v OneDrive-synchronizované složce — appka ho vidí jako
   běžný soubor na disku).
2. **Prohlížení/export** (bezpečné, nic se nezapisuje): vlevo vyber
   technika, vpravo vidíš jeho plán, tlačítkem "Exportovat" uložíš jeho
   `.xlsx`.
3. **Lokální spuštění enginů** (žlutý panel nahoře, tlačítka `1 ▶ Import`
   až `8 ▶ Reporting`): **tohle přepisuje otevřený soubor na disku.** Než
   na to sáhneš:
   - zavři daný soubor v Excelu (jinak si zápisy můžou navzájem
     přepsat),
   - appka si před zápisem sama udělá zálohu (`.backup_...xlsx` vedle
     původního souboru), ale i tak doporučuju první běh vyzkoušet na
     kopii — checklist pro první ostrý test jsem ti poslal dřív v chatu.
   - po libovolném engine otevři soubor v Excelu (na webu), aby se
     přepočítaly vzorce na listech jako `TECHNICIAN_PLAN`/`HOME`/
     `PERFORMANCE` — appka sama vzorce nepřepočítává, jen zapisuje
     surová data.

   Tlačítka jsou očíslovaná ve stejném pořadí, v jakém se spouští podle
   `docs/EXCEL_ONLY_WORKFLOW.md`:
   1. **Import** — vezme `RAW_DATA`/`POS_STATUS_IMPORT`, aktualizuje
      `POS_MASTER`.
   2. **Planning** — vygeneruje/aktualizuje Draft týdny v `MANAGER_PLAN`.
   3. **Publish** — zveřejní nejbližší Draft týden.
   4. **Start Tracking** — řekne appce, které publikované týdny se mají
      počítat do manažerských přehledů (spusť, až chceš, aby se týden
      začal sledovat).
   5. **Compliance** — po novém importu **návštěv ze SalesApp** (list
      `SALESAPP_IMPORT`, ne `RAW_DATA`) vyhodnotí, co bylo splněno.
   6. **Advisor** — diagnostická upozornění (zanedbané POS, přetížení
      technika…) do `ADVISOR_LOG`.
   7. **Performance** — přepočítá manažerské přehledy podle technika/týdne.
   8. **Reporting** — obnoví `DASHBOARD` a mapu území (`POS_MAP_DATA`).

   Kroky 1–4 patří k týdennímu plánování, kroky 5–8 spouštěj po každém
   novém importu dat ze SalesApp.

## 5. Když appka nejde spustit / nechceš appku vůbec

Máš záložní cestu úplně bez appky, čistě v Excel Online — poslal jsem ti
ji jako `docs/EXCEL_ONLY_WORKFLOW.md`.

## Bezpečnostní shrnutí

- Appka nikdy nesahá na SalesApp, internet, ani žádné jiné API — pracuje
  jen s tím `.xlsx` souborem, který jí otevřeš.
- Export (krok 2) nikdy nezapisuje do zdrojového souboru.
- Lokální spuštění enginů (krok 3) zapisuje, ale jen do listů, které
  odpovídající Office Script smí zapisovat (nikdy do listů se vzorci jako
  `TECHNICIAN_PLAN`/`HOME`), a vždy s automatickou zálohou před zápisem.
