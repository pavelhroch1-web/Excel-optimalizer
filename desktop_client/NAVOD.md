# Distribution Client — návod pro běžného uživatele (stažení, instalace, použití)

Tenhle návod je pro tebe jako uživatele appky, ne pro vývojáře — bez
příkazové řádky, krok za krokem. Appka běží na Windows.

## 0. Co appka umí

- **Prohlížet a exportovat** už publikovaný plán po technicích (jedno
  kliknutí = Excel soubor pro daného technika).
- **Nově i spustit Import/Planning/Publish** přímo nad workbookem, bez
  nutnosti otevírat Excel Online (viz varování v kroku 4).

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
3. **Lokální spuštění enginů** (žlutý panel nahoře, `▶ Import` /
   `▶ Planning` / `▶ Publish`): **tohle přepisuje otevřený soubor na
   disku.** Než na to sáhneš:
   - zavři daný soubor v Excelu (jinak si zápisy můžou navzájem
     přepsat),
   - appka si před zápisem sama udělá zálohu (`.backup_...xlsx` vedle
     původního souboru), ale i tak doporučuju první běh vyzkoušet na
     kopii — checklist pro první ostrý test jsem ti poslal dřív v chatu.
   - po spuštění Planning/Publish otevři soubor v Excelu (na webu), aby
     se přepočítaly vzorce na listu `TECHNICIAN_PLAN` — appka sama vzorce
     nepřepočítává.

## 5. Když appka nejde spustit / nechceš appku vůbec

Máš záložní cestu úplně bez appky, čistě v Excel Online — poslal jsem ti
ji jako `docs/EXCEL_ONLY_WORKFLOW.md`.

## Bezpečnostní shrnutí

- Appka nikdy nesahá na SalesApp, internet, ani žádné jiné API — pracuje
  jen s tím `.xlsx` souborem, který jí otevřeš.
- Export (krok 2) nikdy nezapisuje do zdrojového souboru.
- Lokální spuštění enginů (krok 3) zapisuje, ale jen do čtyř konkrétních
  listů (`POS_MASTER`, `MANAGER_PLAN`, `MANAGER_PLAN_PUBLISHED`,
  `PLAN_LIFECYCLE`) a vždy s automatickou zálohou před zápisem.
