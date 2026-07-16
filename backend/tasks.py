"""Planner [T] — generic Task Engine over POS.

Arbitrary tasks over POS: voucher handover, material exchange, addendum signing,
service install, one-off marketing, inventory — anything. The task TYPE is
configuration (from the Velín); a task is an instance of a type. Nothing is
hardcoded — no "vouchers" in code, just a generic engine.

Integration model (used later by the day builder):
  * combinable tasks piggyback on a normal visit — if the technician is going to
    the POS anyway, the task just shows up there (zero extra cost);
  * a dedicated visit is created only when the deadline approaches and a normal
    visit will not happen in time (or the task is not combinable). Urgency is
    computed here (days to deadline); the day builder decides attach vs dedicate.

Deterministic; fully configurable.
"""
from __future__ import annotations

import datetime

import db

_URGENT_DAYS = 14      # within this many days of the deadline → needs its own slot


# ------------------------------------------------------------------ task types
def types(active_only: bool = False) -> list:
    q = "SELECT * FROM task_types"
    if active_only:
        q += " WHERE active=1"
    return [dict(r) for r in db.get(q + " ORDER BY name")]


def upsert_type(t: dict) -> dict:
    f = (t.get("name"), t.get("default_minutes", 5), t.get("default_priority", 3),
         1 if t.get("combinable", True) else 0, 1 if t.get("active", True) else 0)
    if t.get("id"):
        db.run("UPDATE task_types SET name=?, default_minutes=?, default_priority=?, combinable=?, "
               "active=? WHERE id=?", f + (t["id"],))
        return {"id": t["id"], "updated": True}
    db.run("INSERT INTO task_types(name, default_minutes, default_priority, combinable, active) "
           "VALUES(?,?,?,?,?)", f)
    return {"id": db.get("SELECT last_insert_rowid() id")[0]["id"], "created": True}


def seed_default_types() -> dict:
    if db.get("SELECT id FROM task_types LIMIT 1"):
        return {"seeded": False}
    for name, mins, prio, comb in [
        ("Předání poukázek", 5, 3, 1), ("Výměna materiálů", 8, 3, 1),
        ("Podpis dodatku", 10, 2, 1), ("Instalace služby", 30, 2, 0),
        ("Jednorázová akce", 15, 3, 1), ("Inventura", 25, 2, 0)]:
        upsert_type({"name": name, "default_minutes": mins, "default_priority": prio, "combinable": comb})
    return {"seeded": True}


# ------------------------------------------------------------------ tasks
def _days_to(deadline, today):
    if not deadline:
        return None
    try:
        return (datetime.date.fromisoformat(str(deadline)[:10]) - today).days
    except (ValueError, TypeError):
        return None


def _enrich(rows):
    today = datetime.date.today()
    out = []
    for r in rows:
        d = dict(r)
        combinable = d["combinable"] if d["combinable"] is not None else d.get("type_combinable", 1)
        dtd = _days_to(d.get("deadline"), today)
        d["daysToDeadline"] = dtd
        d["combinable"] = bool(combinable)
        # needs its own visit? not combinable, or deadline close (piggyback risk)
        d["needsDedicated"] = (not combinable) or (dtd is not None and dtd <= _URGENT_DAYS)
        d["urgency"] = "overdue" if (dtd is not None and dtd < 0) else \
            ("urgent" if (dtd is not None and dtd <= _URGENT_DAYS) else "normal")
        out.append(d)
    return out


def create(t: dict) -> dict:
    tt = db.get("SELECT default_minutes, default_priority, combinable FROM task_types WHERE id=?",
                (t.get("type_id"),))
    dt = tt[0] if tt else {"default_minutes": 5, "default_priority": 3, "combinable": 1}
    poss = t.get("pos_ids") or ([t["pos_id"]] if t.get("pos_id") else [])
    ids = []
    for pos in poss:
        db.run("INSERT INTO tasks(type_id, pos_id, deadline, est_minutes, priority, combinable, "
               "quantity, note) VALUES(?,?,?,?,?,?,?,?)",
               (t.get("type_id"), str(pos), t.get("deadline"),
                t.get("est_minutes", dt["default_minutes"]), t.get("priority", dt["default_priority"]),
                (1 if t["combinable"] else 0) if "combinable" in t else None,
                t.get("quantity"), t.get("note")))
        ids.append(db.get("SELECT last_insert_rowid() id")[0]["id"])
    return {"created": len(ids), "ids": ids}


def bulk_create(pos_rows, type_id, deadline=None, priority=None, est_minutes=None,
                combinable=None) -> dict:
    """Create one task per POS in `pos_rows` (each {pos, quantity?, note?}) with a
    single shared activity type / deadline / priority. The practical way tasks
    are created — an uploaded list, not one by one."""
    tt = db.get("SELECT default_minutes, default_priority, combinable FROM task_types WHERE id=?", (type_id,))
    dt = tt[0] if tt else {"default_minutes": 5, "default_priority": 3, "combinable": 1}
    made, skipped = 0, 0
    known = {str(r["pos_id"]) for r in db.get("SELECT pos_id FROM pos_master")}
    for row in pos_rows:
        pos = str(row.get("pos") or row.get("pos_id") or "").strip()
        if not pos or pos not in known:
            skipped += 1; continue
        db.run("INSERT INTO tasks(type_id, pos_id, deadline, est_minutes, priority, combinable, quantity, note) "
               "VALUES(?,?,?,?,?,?,?,?)",
               (type_id, pos, deadline, est_minutes if est_minutes is not None else dt["default_minutes"],
                priority if priority is not None else dt["default_priority"],
                (1 if combinable else 0) if combinable is not None else None,
                row.get("quantity"), row.get("note")))
        made += 1
    return {"created": made, "skipped": skipped}


_POS_HEADERS = ("pos", "pos id", "posid", "číslo", "cislo", "id pos", "terminál", "terminal")
_QTY_HEADERS = ("počet", "pocet", "ks", "kusů", "kusu", "množství", "mnozstvi", "quantity", "qty")


def parse_bulk_excel(path: str) -> list:
    """Read an uploaded Excel of POS (+ optional quantity/note) into rows."""
    import openpyxl
    wb = openpyxl.load_workbook(path, read_only=True, data_only=True)
    ws = wb.active
    it = ws.iter_rows(values_only=True)
    header = [str(h).strip().lower() if h is not None else "" for h in (next(it, None) or [])]
    def find(cands):
        for i, h in enumerate(header):
            if any(c == h or c in h for c in cands):
                return i
        return None
    pi = find(_POS_HEADERS)
    if pi is None:
        pi = 0                                  # fall back to first column
    qi = find(_QTY_HEADERS)
    ni = find(("poznámka", "poznamka", "note"))
    rows = []
    for r in it:
        if pi >= len(r) or r[pi] in (None, ""):
            continue
        pos = str(r[pi]).strip()
        pos = pos[:-2] if pos.endswith(".0") else pos     # excel float ids
        row = {"pos": pos}
        if qi is not None and qi < len(r) and r[qi] not in (None, ""):
            try:
                row["quantity"] = int(float(r[qi]))
            except (ValueError, TypeError):
                pass
        if ni is not None and ni < len(r) and r[ni] not in (None, ""):
            row["note"] = str(r[ni])
        rows.append(row)
    return rows


def set_status(task_id: int, status: str) -> dict:
    db.run("UPDATE tasks SET status=?, updated_at=datetime('now') WHERE id=?", (status, task_id))
    return {"id": task_id, "status": status}


def _join_rows(where, params):
    return db.get(
        "SELECT t.*, tt.name type_name, tt.combinable type_combinable, p.name pos_name, p.city pos_city "
        "FROM tasks t LEFT JOIN task_types tt ON tt.id=t.type_id "
        "LEFT JOIN pos_master p ON p.pos_id=t.pos_id " + where, tuple(params))


def for_pos(pos_id: str) -> list:
    """Open tasks for one POS — surfaced at the visit ('also do X')."""
    return _enrich(_join_rows("WHERE t.pos_id=? AND t.status='open' ORDER BY t.deadline", (str(pos_id),)))


def open_tasks(limit: int = 500) -> dict:
    """All open tasks, urgency-ranked — the manager's task board."""
    rows = _enrich(_join_rows("WHERE t.status='open' ORDER BY t.deadline LIMIT ?", (limit,)))
    order = {"overdue": 0, "urgent": 1, "normal": 2}
    rows.sort(key=lambda x: (order.get(x["urgency"], 3), x["daysToDeadline"] if x["daysToDeadline"] is not None else 1e9))
    return {"tasks": rows,
            "counts": {"open": len(rows),
                       "urgent": sum(1 for t in rows if t["urgency"] in ("urgent", "overdue")),
                       "needsDedicated": sum(1 for t in rows if t["needsDedicated"])}}
