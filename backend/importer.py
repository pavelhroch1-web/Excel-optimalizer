"""Excel -> SQLite import (the ONLY way real data enters the datastore).

Reads an authoritative workbook (the current real Excel, or fresh exports)
and loads it into the normalised SQLite tables. Faithful, repeatable, and
idempotent where it matters:
  - pos_master     : upsert by pos_id (+ audit changed fields to history)
  - salesapp_visits: insert, deduped by UID (re-importing overlapping exports
    is harmless - matches the engine's UID dedup)
  - campaigns      : replaced from ACTIVITY_PLAN
  - config         : CONTROL settings + rule tables (as JSON)
  - technicians    : derived from POS assignments + SalesApp executors
  - closed_pos     : derived from POS status/closedSince*

Linking a SalesApp visit to a POS is done by the engine (authoritative); here
we store the raw SalesApp fields faithfully (incl. store_uid + start/finish
times for later route ordering). Plan-vs-reality matching is a later layer.
"""
from __future__ import annotations

import json

import openpyxl

import db


def _rows(ws):
    it = ws.iter_rows(values_only=True)
    header = next(it, None) or []
    hidx = {str(h): i for i, h in enumerate(header)}
    return hidx, it, header


def _g(row, hidx, name):
    i = hidx.get(name)
    if i is None or i >= len(row):
        return None
    v = row[i]
    return v if v not in ("", None) else None


# ---------------------------------------------------------------------------

_POS_MAP = {
    "posId": "pos_id", "terminalId": "terminal_id",
    "nazev": "name", "street": "street", "houseNumber": "house_number",
    "city": "city", "area": "area", "posArea": "pos_area", "category": "category",
    "market": "market", "classification": "classification", "terminalType": "terminal_type",
    "ppt": "ppt", "gpsX": "gps_x", "gpsY": "gps_y", "assignedTechnician": "technician",
    "managerOverrideType": "manager_override_type",
}


def import_pos_master(conn, ws) -> int:
    hidx, it, _ = _rows(ws)
    n = 0
    for row in it:
        pid = _g(row, hidx, "posId")
        if pid is None:
            continue
        vals = {dst: _g(row, hidx, src) for src, dst in _POS_MAP.items()}
        vals["pos_id"] = str(pid)
        status = _g(row, hidx, "status")
        vals["active"] = 0 if (status and str(status).upper() in ("CLOSED", "ZAVRENO", "ZAVŘENO")) else 1
        fields = ", ".join(vals.keys())
        marks = ", ".join("?" for _ in vals)
        updates = ", ".join(f"{k}=excluded.{k}" for k in vals if k != "pos_id")
        conn.execute(
            f"INSERT INTO pos_master ({fields}) VALUES ({marks}) "
            f"ON CONFLICT(pos_id) DO UPDATE SET {updates}, updated_at=datetime('now'), last_seen=datetime('now')",
            tuple(vals.values()))
        # closed POS
        csw = _g(row, hidx, "closedSinceWeek")
        if csw is not None or vals["active"] == 0:
            conn.execute(
                "INSERT INTO closed_pos (pos_id, closed_on, reason, source) VALUES (?, ?, ?, 'import') "
                "ON CONFLICT(pos_id) DO NOTHING",
                (str(pid), str(csw) if csw is not None else None, "status/closedSince"))
        n += 1
    return n


def import_salesapp(conn, ws, filename: str | None = None) -> int:
    hidx, it, header = _rows(ws)
    purpose_cols = [(str(h), i) for i, h in enumerate(header) if str(h).startswith("Účel")]

    # Link SalesApp visit -> POS the same way the engine does:
    # SalesApp "Store UID" == pos_master.terminal_id -> posId.
    term_to_pos = {str(r["terminal_id"]): r["pos_id"]
                   for r in conn.execute(
                       "SELECT terminal_id, pos_id FROM pos_master WHERE terminal_id IS NOT NULL")}

    cur = conn.execute(
        "INSERT INTO salesapp_imports (filename, row_count) VALUES (?, 0)", (filename,))
    import_id = cur.lastrowid

    def purpose_and_role(row):
        hit, is_oz, is_tech = [], False, False
        for h, i in purpose_cols:
            if i < len(row) and row[i] not in (None, "", 0):
                hit.append(h.replace("Účel návštevy", "").strip(" -"))
                # header form: "Účel návštevy - OZ - ..." / "- Technik - ..."
                seg = h.replace("Účel návštevy", "")
                if "OZ" in seg:
                    is_oz = True
                if "Technik" in seg:
                    is_tech = True
        role = "OZ" if (is_oz and not is_tech) else ("TECHNIK" if is_tech and not is_oz else None)
        return ("; ".join(hit) if hit else None), role

    batch, n = [], 0
    for row in it:
        uid = _g(row, hidx, "UID")
        if uid is None:
            continue
        purpose, role = purpose_and_role(row)
        store_uid = _g(row, hidx, "Store UID")
        pos_id = term_to_pos.get(str(store_uid)) if store_uid is not None else None
        batch.append((
            str(uid), pos_id, store_uid,
            _g(row, hidx, "Store"), _g(row, hidx, "Store address"), _g(row, hidx, "Agency region"),
            _g(row, hidx, "Executor"), _g(row, hidx, "Executor UID"), role,
            _g(row, hidx, "Date"), _g(row, hidx, "Started at"), _g(row, hidx, "Finished at"),
            _g(row, hidx, "Real duration (h)"), purpose, import_id))
        n += 1
        if len(batch) >= 1000:
            _flush_visits(conn, batch); batch = []
    if batch:
        _flush_visits(conn, batch)
    conn.execute("UPDATE salesapp_imports SET row_count=? WHERE id=?", (n, import_id))
    return n


def _flush_visits(conn, batch):
    conn.executemany(
        "INSERT INTO salesapp_visits (uid, pos_id, store_uid, store_name, store_address, region, "
        "technician, executor_uid, visitor_role, visit_date, started_at, finished_at, real_duration, purpose, import_id) "
        "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?) ON CONFLICT(uid) DO NOTHING",
        batch)


def import_activity_plan(conn, ws) -> int:
    hidx, it, _ = _rows(ws)
    conn.execute("DELETE FROM campaigns")
    n = 0
    for row in it:
        name = _g(row, hidx, "ACTIVITY")
        if name is None:
            continue
        odhad_raw = _g(row, hidx, "ODHAD_NAVSTEV_ZA_KAMPAN")
        try:
            target = int(float(odhad_raw)) if odhad_raw not in (None, "") else None
        except (ValueError, TypeError):
            target = None
        conn.execute(
            "INSERT INTO campaigns (kind, name, year, start_week, end_week, priority, override_gap, estimate, target_visits) "
            "VALUES (?,?,?,?,?,?,?,?,?)",
            (_g(row, hidx, "TYPE"), str(name), 2026, _g(row, hidx, "START_WEEK"),
             _g(row, hidx, "END_WEEK"), _g(row, hidx, "PRIORITY"), _g(row, hidx, "OVERRIDE_GAP"),
             str(odhad_raw or ""), target))
        n += 1
    return n


def import_config(conn, wb) -> int:
    n = 0
    if "CONTROL" in wb.sheetnames:
        hidx, it, _ = _rows(wb["CONTROL"])
        for row in it:
            key = _g(row, hidx, "SETTING")
            if key is None:
                continue
            conn.execute("INSERT INTO config (key, value) VALUES (?, ?) "
                         "ON CONFLICT(key) DO UPDATE SET value=excluded.value",
                         (str(key), str(_g(row, hidx, "VALUE") or "")))
            n += 1
    # rule tables stored as JSON under config (engine-ready, extensible)
    for sheet, key, cols in (
        ("TERMINAL_RULES", "terminal_rules", ("TYP TERMINALU", "ACTIVE")),
        ("MARKET_RULES", "market_rules", ("MARKET", "ACTIVE")),
        ("CATEGORY_RULES", "category_rules", ("CATEGORY", "RULE")),
    ):
        if sheet not in wb.sheetnames:
            continue
        hidx, it, _ = _rows(wb[sheet])
        data = []
        for row in it:
            k = _g(row, hidx, cols[0])
            if k is None:
                continue
            data.append({cols[0]: k, cols[1]: _g(row, hidx, cols[1])})
        conn.execute("INSERT INTO config (key, value) VALUES (?, ?) "
                     "ON CONFLICT(key) DO UPDATE SET value=excluded.value",
                     (key, json.dumps(data, ensure_ascii=False)))
    return n


def derive_technicians(conn) -> int:
    conn.execute(
        "INSERT OR IGNORE INTO technicians (name) "
        "SELECT DISTINCT technician FROM pos_master WHERE technician IS NOT NULL AND technician<>'' "
        "UNION SELECT DISTINCT technician FROM salesapp_visits WHERE technician IS NOT NULL AND technician<>''")
    # role from visits: OZ if the person's OZ visits outnumber TECHNIK visits.
    conn.execute(
        "UPDATE technicians SET role='OZ' WHERE name IN ("
        "  SELECT technician FROM salesapp_visits WHERE technician IS NOT NULL "
        "  GROUP BY technician "
        "  HAVING SUM(visitor_role='OZ') > SUM(visitor_role='TECHNIK'))")
    return conn.execute("SELECT COUNT(*) c FROM technicians").fetchone()[0]


def import_workbook(path: str, filename: str | None = None) -> dict:
    """Import everything from one workbook into SQLite. Returns per-table counts."""
    db.init_db()
    wb = openpyxl.load_workbook(path, read_only=True, data_only=True)
    conn = db.connect()
    try:
        result = {
            "pos_master": import_pos_master(conn, wb["POS_MASTER"]) if "POS_MASTER" in wb.sheetnames else 0,
            "salesapp_visits": import_salesapp(conn, wb["SALESAPP_IMPORT"], filename) if "SALESAPP_IMPORT" in wb.sheetnames else 0,
            "campaigns": import_activity_plan(conn, wb["ACTIVITY_PLAN"]) if "ACTIVITY_PLAN" in wb.sheetnames else 0,
            "config": import_config(conn, wb),
        }
        result["technicians"] = derive_technicians(conn)
        result["closed_pos"] = conn.execute("SELECT COUNT(*) c FROM closed_pos").fetchone()[0]
        conn.commit()
    finally:
        conn.close()
        wb.close()
    sync_rules_from_config()  # keep business_rules/settings in step with imported CONTROL
    return result


# CONTROL key -> where the DB config that maps to it should be synced, so the
# DB (source of truth) reflects the real imported CONTROL and db_state's overlay
# reproduces the baseline plan. (Only knobs the engine actually reads.)
_CONTROL_TO_RULE = {
    "STANDARD_VISIT_GAP":          ("MIN_GAP", "weeks", float),
    "NEGLECTED_AFTER_WEEKS":       ("NEGLECTED_AFTER", "weeks", float),
    "HOLDBACK_LOOKAHEAD_WEEKS":    ("HOLDBACK", "lookahead_weeks", float),
    "HOLDBACK_TOLERANCE_A_WEEKS":  ("HOLDBACK", "tolerance_a", float),
    "HOLDBACK_TOLERANCE_OTHER_WEEKS": ("HOLDBACK", "tolerance_other", float),
    "TARGET_VISITS_WEEK":          ("MAX_VISITS_WEEK", "per_week", float),
    "TARGET_VISITS_DAY":           ("MAX_VISITS_WEEK", "per_day", float),
    "GPS_EXTRA_MAX_VISITS":        ("GPS_EXTRA", "max_extra_visits", float),
}


def sync_rules_from_config() -> None:
    """Sync business_rules params + planner settings from the imported CONTROL
    (config table), so the DB is the source of truth and the engine, driven by
    db_state, reproduces the current behaviour. Idempotent."""
    import business_rules
    import settings as settings_mod

    ctrl = {r["key"]: r["value"] for r in db.get("SELECT key, value FROM config")}
    eff = business_rules.effective()
    for ckey, (rule, pkey, cast) in _CONTROL_TO_RULE.items():
        if ckey not in ctrl or ctrl[ckey] in (None, ""):
            continue
        try:
            val = cast(ctrl[ckey])
        except (ValueError, TypeError):
            continue
        val = int(val) if isinstance(val, float) and val.is_integer() else val
        params = dict(eff.get(rule, {}).get("params", {}))
        params[pkey] = val
        business_rules.set_params(rule, params)
        eff.setdefault(rule, {"params": {}})["params"] = params
    if "TARGET_VISITS_DAY" in ctrl and ctrl["TARGET_VISITS_DAY"] not in (None, ""):
        try:
            settings_mod.set_value("planner", "max_visits_per_day", int(float(ctrl["TARGET_VISITS_DAY"])))
        except (ValueError, TypeError):
            pass
    # GPS extra visits on/off follows the imported CONTROL flag.
    if "GPS_EXTRA_ENABLED" in ctrl and ctrl["GPS_EXTRA_ENABLED"] not in (None, ""):
        try:
            business_rules.set_enabled("GPS_EXTRA", float(ctrl["GPS_EXTRA_ENABLED"]) == 1)
        except (ValueError, TypeError):
            pass
