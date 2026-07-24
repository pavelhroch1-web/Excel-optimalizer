# API kontrakt — importní vrstva

Jeden zdroj pravdy pro tvar požadavků/odpovědí mezi frontendem (`web/`) a
backendem (`backend/`). Když se změní endpoint, updatuj **všechny tři**:

1. backend Pydantic/DTO (`backend/contracts.py`)
2. frontend typedef (`web/contracts.js`)
3. tento dokument

## `ImportResult` — odpověď každého importního endpointu

Vrací ji: `POST /api/import/auto`, `POST /api/import/{kind}`,
`POST /api/import/workbook`, `POST /api/import/sample`.

```jsonc
{
  "ok": true,                    // bool — true JEN když se data opravdu propsala
  "kind": "pos_master",          // pos_master|salesapp|activity_plan|tourplan|workbook|unknown
  "kindLabel": "POS Master",     // člověku srozumitelný název
  "imported": { "pos_master": 11605, "technicians": 42 },  // tabulka → počet řádků
  "total": 11605,                // primární počet řádků (0 když ok=false)
  "warnings": ["PPT / PTT (potenciál)"],   // nefatální (chybí nepovinný sloupec)
  "error": null,                 // string s přesným důvodem když ok=false, jinak null
  "file": "POS_master.xlsx",
  "recomputed": ["alerts:37"]
}
```

### Železné pravidlo (žádné falešné „hotovo")

Frontend smí zobrazit zelený úspěch **pouze** když `ok === true && total > 0`.
Cokoli jiného = chybový stav s textem z `error`. Sdílený renderer
`renderImportResult()` (`web/contracts.js`) to vynucuje na jednom místě, takže
to nejde obejít v jednotlivých handlerech.

### Kdy backend vrátí `ok:false`

| Situace | `kind` | `error` |
|---|---|---|
| Nerozpoznaný soubor | `unknown` | „Nepodařilo se rozpoznat typ…" |
| Chybí povinný sloupec | detekovaný | „…chybí povinný sloupec: číslo POS." |
| Hlavička OK, ale 0 datových řádků | detekovaný | „…pod ní nejsou žádné datové řádky…" |
| Import proběhl, ale 0 řádků se propsalo | detekovaný | „…nenaimportoval se žádný řádek…" |

Validace (`backend/import_validate.py`) běží **před** jakýmkoli zápisem, takže
strukturálně vadný soubor se odmítne dřív, než něco změní v databázi.

### Povinné / doporučené sloupce (validátor)

| kind | povinné (hard) | doporučené (soft → warning) |
|---|---|---|
| `pos_master` | číslo POS | PPT/PTT, název, ulice, město |
| `salesapp` | UID, Executor | Store UID, datum, reálné trvání |
| `tourplan` | POS, technik, týden | — |
| `activity_plan` | — (matrix i tabulka) | — |

HTTP status: úspěch i „ok:false" se vrací jako **200** s tělem `ImportResult`
(business výsledek). Skutečné serverové chyby (výjimka, špatný multipart) jsou
**4xx/5xx** s `{ "detail": "…" }` — frontend to kontroluje přes `res.ok`.

## Duplicity adres (`/api/pos/duplicates`)

Skupina = **stejná adresa (1:1, jen case/mezery se sjednotí) + stejný název
firmy**. Stejná adresa + jiná firma (nákupní centrum) se **nespojuje**.
V každé skupině zůstává provozovna se **silnějším PPT**; slabší jde na blacklist
(`active=0`) a zůstane neaktivní i po re-importu.
