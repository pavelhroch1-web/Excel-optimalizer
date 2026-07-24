"""Data-quality checks — the silent problems that make numbers wrong without
ever raising an error.

A manager trusts the dashboards; if the same technician is logged under two
spellings, or 12% of visits never link to a POS, the totals are quietly off and
nothing warns them. This surfaces those, each with a count, a few examples, and
(where possible) where to fix it. Read-only over SQLite; deterministic.
"""
from __future__ import annotations

import re
import unicodedata
from collections import defaultdict

import db


def _tokens(s) -> frozenset:
    s = unicodedata.normalize("NFKD", str(s or "")).encode("ascii", "ignore").decode().lower()
    return frozenset(t for t in re.split(r"[^a-z]+", s) if len(t) >= 2)


def _check(cid, level, title, count, detail, sample=None, action=None) -> dict:
    return {"id": cid, "level": level, "title": title, "count": count,
            "detail": detail, "sample": sample or [], "action": action}


def report() -> dict:
    """All checks, worst first. level: bad | warn | ok."""
    checks = []

    # 1) technician name variants — the same person under >1 spelling splits
    #    their stats across two "people".
    names = [r["technician"] for r in db.get(
        "SELECT DISTINCT technician FROM salesapp_visits "
        "WHERE technician IS NOT NULL AND technician<>''")]
    grp = defaultdict(set)
    for n in names:
        t = _tokens(n)
        if t:
            grp[t].add(n)
    variants = [sorted(v) for v in grp.values() if len(v) > 1]
    checks.append(_check(
        "tech_variants", "bad" if variants else "ok",
        "Stejný technik pod více jmény", len(variants),
        "Jeden člověk zapsaný různě (např. „Vlk Pavel\" vs „Pavel Vlk\") si rozdělí "
        "statistiky mezi dvě osoby. Sjednoť jména v SalesApp exportu.",
        sample=[" / ".join(v) for v in variants[:6]],
        action={"label": "Otevřít Technici", "nav": "settings"} if variants else None))

    # 2) visits that never linked to a POS — they don't count as coverage. Office
    #    / lunch / prospect entries are expected; a spike means POS-number
    #    mismatch between SalesApp and the POS file.
    tot = db.get("SELECT COUNT(*) c FROM salesapp_visits")[0]["c"]
    nolink = db.get("SELECT COUNT(*) c FROM salesapp_visits WHERE pos_id IS NULL")[0]["c"]
    pct = round(100 * nolink / tot) if tot else 0
    top = db.get("SELECT store_name, COUNT(*) n FROM salesapp_visits "
                 "WHERE pos_id IS NULL AND store_name IS NOT NULL AND store_name<>'' "
                 "GROUP BY store_name ORDER BY n DESC LIMIT 6")
    checks.append(_check(
        "visits_unlinked", "warn" if pct >= 10 else "ok",
        "Návštěvy nenapojené na POS", nolink,
        f"{pct}% návštěv nemá vazbu na POS, takže se nepočítají do pokrytí. Kanceláře, "
        f"pauzy a prospekti jsou v pořádku; vysoké číslo značí nesoulad čísel POS "
        f"mezi SalesApp a POS souborem.",
        sample=[f"{r['store_name']} ({r['n']}×)" for r in top]))

    # 3) active POS with no PPT — PPT drives ranking and address de-dup
    noppt = db.get("SELECT pos_id, name, city FROM pos_master "
                   "WHERE active=1 AND (ppt IS NULL OR ppt=0) LIMIT 6")
    npptc = db.get("SELECT COUNT(*) c FROM pos_master WHERE active=1 AND (ppt IS NULL OR ppt=0)")[0]["c"]
    checks.append(_check(
        "pos_no_ppt", "warn" if npptc else "ok",
        "Aktivní POS bez PPT", npptc,
        "PPT (potenciál) řídí řazení i sloučení duplicit. Bez něj POS „nemá váhu\". "
        "Doplň PPT v POS souboru a naimportuj znovu.",
        sample=[f"{r['name']} – {r['city']} ({r['pos_id']})" for r in noppt]))

    # 4) active POS with no GPS — invisible on every map layer
    nogps = db.get("SELECT COUNT(*) c FROM pos_master WHERE active=1 AND (gps_x IS NULL OR gps_x=0)")[0]["c"]
    checks.append(_check(
        "pos_no_gps", "warn" if nogps else "ok",
        "Aktivní POS bez GPS", nogps,
        "POS bez souřadnic se nezobrazí na mapě ani nevstupuje do výpočtu tras. "
        "Doplň X/Y v POS souboru.",
        sample=[f"{r['name']} – {r['city']} ({r['pos_id']})" for r in db.get(
            "SELECT pos_id, name, city FROM pos_master "
            "WHERE active=1 AND (gps_x IS NULL OR gps_x=0) LIMIT 6")] if nogps else []))

    # 5) active technicians with no region — středisko filters skip them
    noreg = db.get("SELECT name FROM technicians WHERE role='TECHNIK' AND active=1 "
                   "AND excluded=0 AND (region IS NULL OR region='') ORDER BY name LIMIT 8")
    nregc = db.get("SELECT COUNT(*) c FROM technicians WHERE role='TECHNIK' AND active=1 "
                   "AND excluded=0 AND (region IS NULL OR region='')")[0]["c"]
    checks.append(_check(
        "tech_no_region", "warn" if nregc else "ok",
        "Aktivní technici bez střediska", nregc,
        "Bez regionu vypadnou z filtrů po střediscích. Většinou nemají žádné "
        "návštěvy (nedá se odvodit) — doplň region ručně nebo je vyřaď, pokud "
        "nejsou aktivní.",
        sample=[r["name"] for r in noreg],
        action={"label": "Otevřít Technici", "nav": "settings"} if nregc else None))

    # 6) duplicate addresses (two-terminal POS) — informational; managed on POS
    try:
        import pos_dedup
        dup = pos_dedup.duplicate_groups(limit=1)
        dcount = dup.get("groupCount", 0)
    except Exception:  # noqa: BLE001
        dcount = 0
    checks.append(_check(
        "pos_dupes", "warn" if dcount else "ok",
        "Duplicitní adresy (2 terminály)", dcount,
        "Stejná adresa i firma pod dvěma čísly POS. Nech silnější (PPT) a slabší "
        "dej na blacklist v sekci POS.",
        action={"label": "Otevřít POS", "nav": "pos"} if dcount else None))

    order = {"bad": 0, "warn": 1, "ok": 2}
    checks.sort(key=lambda c: (order.get(c["level"], 3), -c["count"]))
    issues = sum(1 for c in checks if c["level"] != "ok")
    return {"checks": checks, "issues": issues, "clean": issues == 0}
