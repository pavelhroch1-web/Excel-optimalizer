"""POS address de-duplication + blacklist.

Two active POS at the SAME address with the SAME business are the same store
registered twice (typically an old and a new terminal number). We keep the
STRONGEST one active — by revenue potential (PPT), then classification, terminal
type, having GPS, and recency — and blacklist the weaker ones so they stay
inactive even across re-imports. Also supports a manual blacklist for one-off
exclusions. Deterministic; read-only except the explicit apply / blacklist ops.
"""
from __future__ import annotations

import db

_CLASS_RANK = {"A": 4, "B": 3, "C": 2, "D": 1}
_TERM_RANK = {"VELKY TERMINAL": 3, "LI": 2, "SMALL TERMINAL": 1}


def _strength(r) -> tuple:
    """Higher tuple = stronger POS (revenue first)."""
    return (
        float(r.get("ppt") or 0),
        _CLASS_RANK.get(str(r.get("classification") or "").upper(), 0),
        _TERM_RANK.get(str(r.get("terminal_type") or "").upper(), 0),
        1 if r.get("gps_x") not in (None, 0) else 0,
        str(r.get("last_seen") or ""),
        str(r.get("pos_id")),
    )


def _sq(s) -> str:
    """Squash: lower-case, collapse every run of whitespace to one space, trim.
    So 'Hradecká  408/40' and 'hradecká 408/40' become the same key — a real
    1:1 address/name match that tolerates only spacing/case, nothing semantic."""
    return " ".join(str(s or "").split()).lower()


def _norm_addr(r) -> str:
    """Exact address key (street + house number + city), 1:1 up to spacing/case."""
    parts = [_sq(r.get("street")), _sq(r.get("house_number")), _sq(r.get("city"))]
    return " ".join(p for p in parts if p)


def _norm_name(r) -> str:
    return _sq(r.get("name"))


def _mini(r) -> dict:
    return {"pos": str(r["pos_id"]), "name": r.get("name"), "ppt": r.get("ppt"),
            "classification": r.get("classification"), "terminalType": r.get("terminal_type"),
            "city": r.get("city"), "technician": r.get("technician")}


def _display_addr(r) -> str:
    """Human, original-cased address of a POS (not the lowercased match key)."""
    parts = [str(r.get("street") or "").strip(), str(r.get("house_number") or "").strip()]
    line = " ".join(p for p in parts if p)
    city = str(r.get("city") or "").strip()
    return (line + (", " + city if city else "")).strip(", ").strip()


def _keep_reason(keep, drop) -> str:
    """Why this POS is the one we keep — in plain words."""
    kp = float(keep.get("ppt") or 0)
    if kp > 0 and all(float(d.get("ppt") or 0) < kp for d in drop):
        return "nejvyšší PPT"
    if keep.get("classification"):
        return f"lepší klasifikace ({keep['classification']})"
    if keep.get("terminal_type"):
        return f"silnější terminál ({keep['terminal_type']})"
    return "nejsilnější"


def _build_groups() -> list:
    """All same-address + same-business groups of >=2 active POS, strongest first
    within each group. Returns list of (keep_row, [drop_rows...])."""
    rows = db.get("SELECT pos_id,name,street,house_number,city,ppt,classification,"
                  "terminal_type,gps_x,last_seen,market,category,technician "
                  "FROM pos_master WHERE active=1 AND street IS NOT NULL AND street<>''")
    buckets: dict = {}
    for r in rows:
        d = dict(r)
        addr = _norm_addr(d)
        nm = _norm_name(d)
        # Require BOTH an identical address AND an identical business name. Same
        # address + DIFFERENT name is a shared building (mall: Albert + GECO +
        # Česká pošta at one street number) — separate stores, never merged.
        if not addr or not nm:
            continue
        buckets.setdefault((addr, nm), []).append(d)
    groups = []
    for members in buckets.values():
        if len(members) < 2:
            continue
        members.sort(key=_strength, reverse=True)
        groups.append((members[0], members[1:]))
    # biggest stores (by kept PPT) first — the ones worth checking
    groups.sort(key=lambda g: -float(g[0].get("ppt") or 0))
    return groups


def duplicate_groups(limit: int = 200) -> dict:
    groups = _build_groups()
    total_drop = sum(len(d) for _, d in groups)
    shown = [{"address": _norm_addr(k), "displayAddress": _display_addr(k),
              "name": k.get("name"), "keep": _mini(k), "drop": [_mini(x) for x in d],
              "keepReason": _keep_reason(k, d), "dropCount": len(d)}
             for k, d in groups[:limit]]
    return {"groups": shown, "groupCount": len(groups),
            "totalDeactivatable": total_drop, "shown": len(shown)}


def apply_dedup(addresses: list | None = None) -> dict:
    """Blacklist (deactivate) the weaker POS of every duplicate group. If
    `addresses` is given, only those groups (by normalized address) are applied;
    otherwise all groups. Returns how many POS were deactivated."""
    groups = _build_groups()
    want = set(addresses) if addresses else None
    done = 0
    for keep, drop in groups:
        if want is not None and _norm_addr(keep) not in want:
            continue
        for d in drop:
            blacklist_add(d["pos_id"], reason="duplicitní adresa",
                          source="dedup", kept=str(keep["pos_id"]))
            done += 1
    return {"deactivated": done, "groups": len(groups)}


# ------------------------------------------------------------------ blacklist
def blacklist_add(pos_id: str, reason: str = "", source: str = "manual",
                  kept: str | None = None) -> dict:
    pos_id = str(pos_id)
    db.run("INSERT INTO pos_blacklist (pos_id, reason, source, kept_pos_id) VALUES (?,?,?,?) "
           "ON CONFLICT(pos_id) DO UPDATE SET reason=excluded.reason, source=excluded.source, "
           "kept_pos_id=excluded.kept_pos_id", (pos_id, reason, source, kept))
    db.run("UPDATE pos_master SET active=0, updated_at=datetime('now') WHERE pos_id=?", (pos_id,))
    return {"pos": pos_id, "blacklisted": True}


def blacklist_remove(pos_id: str, reactivate: bool = True) -> dict:
    pos_id = str(pos_id)
    db.run("DELETE FROM pos_blacklist WHERE pos_id=?", (pos_id,))
    if reactivate:
        db.run("UPDATE pos_master SET active=1, updated_at=datetime('now') WHERE pos_id=?", (pos_id,))
    return {"pos": pos_id, "blacklisted": False, "reactivated": reactivate}


def blacklist_list(limit: int = 500) -> dict:
    rows = db.get(
        "SELECT b.pos_id, b.reason, b.source, b.kept_pos_id, b.created_at, "
        "p.name, p.city, p.ppt, p.active FROM pos_blacklist b "
        "LEFT JOIN pos_master p ON p.pos_id=b.pos_id ORDER BY b.created_at DESC LIMIT ?", (limit,))
    return {"count": db.get("SELECT COUNT(*) c FROM pos_blacklist")[0]["c"],
            "items": [dict(r) for r in rows]}


def enforce_blacklist(conn=None) -> int:
    """Force every blacklisted POS to active=0. Called after an import so the
    blacklist survives re-imports. Returns rows touched (best-effort)."""
    sql = ("UPDATE pos_master SET active=0 WHERE active=1 AND pos_id IN "
           "(SELECT pos_id FROM pos_blacklist)")
    if conn is not None:
        conn.execute(sql)
        return 0
    db.run(sql)
    return 0
