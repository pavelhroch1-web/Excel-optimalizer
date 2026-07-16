"""Planner Phase 2 — micro-clustering of POS.

POS in the same shopping centre or within a few tens of metres walking are one
logical unit: if the technician is already at one, the others cost almost no
extra travel. The planner should almost always evaluate them together.

We precompute these micro-clusters from pos_master GPS with a small walking
radius, using a spatial grid + union-find so it scales to the whole network.
Deterministic, no ML. Recomputable cache in `pos_clusters`, rebuilt after a POS
master import.
"""
from __future__ import annotations

import db
from desktop_client.engines.core_logic import distance_km

_DEFAULT_RADIUS_M = 75      # walking distance that counts as "the same spot"


def _radius_m() -> float:
    try:
        import settings
        v = settings.get("map", "clusterRadiusM")
        if v:
            return float(v)
    except Exception:  # noqa: BLE001
        pass
    return _DEFAULT_RADIUS_M


class _UF:
    def __init__(self):
        self.p = {}

    def find(self, x):
        self.p.setdefault(x, x)
        while self.p[x] != x:
            self.p[x] = self.p[self.p[x]]; x = self.p[x]
        return x

    def union(self, a, b):
        ra, rb = self.find(a), self.find(b)
        if ra != rb:
            self.p[ra] = rb


def rebuild(radius_m: float | None = None) -> dict:
    """Recompute micro-clusters from pos_master and store them."""
    radius_m = radius_m or _radius_m()
    rad_km = radius_m / 1000.0
    pts = db.get("SELECT pos_id, gps_x lat, gps_y lon FROM pos_master "
                 "WHERE active=1 AND gps_x IS NOT NULL AND gps_y IS NOT NULL")
    if not pts:
        return {"rebuilt": False, "reason": "no GPS"}
    # spatial grid: cell ~ radius, so candidates are in the 3x3 neighbourhood
    import math
    lat0 = sum(p["lat"] for p in pts) / len(pts)
    dlat = radius_m / 111000.0
    dlon = radius_m / (111000.0 * max(math.cos(math.radians(lat0)), 0.3))
    grid: dict = {}
    for p in pts:
        cell = (int(p["lat"] / dlat), int(p["lon"] / dlon))
        grid.setdefault(cell, []).append(p)

    uf = _UF()
    for p in pts:
        uf.find(str(p["pos_id"]))    # ensure present
    for (cx, cy), members in grid.items():
        # candidates = this cell + 8 neighbours
        cand = []
        for ax in (cx - 1, cx, cx + 1):
            for ay in (cy - 1, cy, cy + 1):
                cand.extend(grid.get((ax, ay), []))
        for a in members:
            for b in cand:
                if a["pos_id"] == b["pos_id"]:
                    continue
                if distance_km(a["lat"], a["lon"], b["lat"], b["lon"]) <= rad_km:
                    uf.union(str(a["pos_id"]), str(b["pos_id"]))

    from collections import defaultdict
    comp = defaultdict(list)
    for p in pts:
        comp[uf.find(str(p["pos_id"]))].append(str(p["pos_id"]))

    db.run("DELETE FROM pos_clusters")
    n_clusters = 0
    for cid, (root, members) in enumerate(comp.items(), start=1):
        if len(members) < 2:
            continue                 # singletons are not clusters
        n_clusters += 1
        for pos in members:
            db.run("INSERT OR REPLACE INTO pos_clusters(pos_id, cluster_id, size) VALUES(?,?,?)",
                   (pos, cid, len(members)))
    clustered = db.get("SELECT COUNT(*) c FROM pos_clusters")[0]["c"]
    return {"rebuilt": True, "radiusM": radius_m, "clusters": n_clusters,
            "clusteredPos": clustered, "totalPos": len(pts)}


def cluster_of(pos_id: str) -> dict:
    """The micro-cluster a POS belongs to, with its co-members and walking
    distance from this POS."""
    row = db.get("SELECT cluster_id, size FROM pos_clusters WHERE pos_id=?", (str(pos_id),))
    if not row:
        return {"pos": str(pos_id), "clustered": False, "size": 1}
    cid = row[0]["cluster_id"]
    mates = db.get(
        "SELECT c.pos_id pos, p.name nm, p.city city, p.market chain, p.gps_x lat, p.gps_y lon "
        "FROM pos_clusters c JOIN pos_master p ON p.pos_id=c.pos_id WHERE c.cluster_id=?", (cid,))
    me = next((m for m in mates if str(m["pos"]) == str(pos_id)), None)
    others = []
    for m in mates:
        if str(m["pos"]) == str(pos_id):
            continue
        dm = None
        if me and None not in (me["lat"], me["lon"], m["lat"], m["lon"]):
            dm = round(distance_km(me["lat"], me["lon"], m["lat"], m["lon"]) * 1000)
        others.append({"pos": str(m["pos"]), "name": m["nm"], "city": m["city"],
                       "chain": m["chain"], "distM": dm})
    others.sort(key=lambda x: (x["distM"] is None, x["distM"] or 0))
    return {"pos": str(pos_id), "clustered": True, "clusterId": cid,
            "size": row[0]["size"], "members": others}


def overview() -> dict:
    rows = db.get("SELECT cluster_id, size FROM pos_clusters GROUP BY cluster_id")
    sizes = [r["size"] for r in rows]
    biggest = db.get(
        "SELECT c.cluster_id cid, c.size sz, MIN(p.city) city, MIN(p.name) nm "
        "FROM pos_clusters c JOIN pos_master p ON p.pos_id=c.pos_id "
        "GROUP BY c.cluster_id ORDER BY c.size DESC LIMIT 10")
    return {"clusters": len(sizes), "clusteredPos": sum(sizes),
            "avgSize": round(sum(sizes) / len(sizes), 1) if sizes else 0,
            "maxSize": max(sizes) if sizes else 0,
            "radiusM": _radius_m(),
            "biggest": [{"clusterId": b["cid"], "size": b["sz"], "city": b["city"],
                         "example": b["nm"]} for b in biggest]}
