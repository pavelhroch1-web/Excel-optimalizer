"""Road routing via OSRM, cached in SQLite.

The GIS layers need real routes that follow roads, not straight lines between
points. We call an OSRM routing service, then cache the returned geometry keyed
by the ordered coordinates, so every route is fetched at most once and is
instant (and available offline) afterwards. If routing is unreachable, we fall
back to the straight-line polyline so the map still draws.

The service URL is configurable (settings key `map.osrmUrl`); it defaults to the
public OSRM demo. In a portable deployment the customer can point it at their
own OSRM instance and everything keeps working offline.
"""
from __future__ import annotations

import hashlib
import json

import db

_DEFAULT_OSRM = "https://router.project-osrm.org"
_MAX_COORDS = 90            # keep the request URL well under limits


def _osrm_base() -> str:
    try:
        import settings
        v = settings.get("map", "osrmUrl")
        if v:
            return str(v).rstrip("/")
    except Exception:  # noqa: BLE001
        pass
    return _DEFAULT_OSRM


def _key(coords) -> str:
    raw = ";".join(f"{round(a, 5)},{round(b, 5)}" for a, b in coords)
    return hashlib.sha1(raw.encode()).hexdigest()


def _straight(coords) -> dict:
    from desktop_client.engines.core_logic import distance_km
    km = sum(distance_km(coords[i][0], coords[i][1], coords[i + 1][0], coords[i + 1][1])
             for i in range(len(coords) - 1))
    return {"geometry": [[a, b] for a, b in coords], "km": round(km, 2),
            "min": None, "source": "straight"}


def road_route(coords) -> dict:
    """coords: ordered [(lat, lon), ...]. Returns {geometry:[[lat,lon]...],
    km, min, source}. Cached; straight-line fallback if OSRM is unreachable."""
    coords = [(float(a), float(b)) for a, b in coords if a is not None and b is not None]
    if len(coords) < 2:
        return {"geometry": [[a, b] for a, b in coords], "km": 0.0, "min": 0.0, "source": "none"}
    if len(coords) > _MAX_COORDS:
        coords = coords[:_MAX_COORDS]
    ck = _key(coords)
    row = db.get("SELECT geometry, road_km, road_min, source FROM route_geometry WHERE cache_key=?", (ck,))
    if row:
        return {"geometry": json.loads(row[0]["geometry"]), "km": row[0]["road_km"],
                "min": row[0]["road_min"], "source": row[0]["source"]}
    out = _fetch_osrm(coords) or _straight(coords)
    try:
        db.run("INSERT OR REPLACE INTO route_geometry(cache_key, geometry, road_km, road_min, source) "
               "VALUES(?,?,?,?,?)", (ck, json.dumps(out["geometry"]), out["km"], out["min"], out["source"]))
    except Exception:  # noqa: BLE001
        pass
    return out


def _fetch_osrm(coords) -> dict | None:
    try:
        import requests
    except ImportError:
        return None
    # OSRM wants lon,lat
    path = ";".join(f"{lon},{lat}" for lat, lon in coords)
    url = f"{_osrm_base()}/route/v1/driving/{path}"
    try:
        r = requests.get(url, params={"overview": "full", "geometries": "geojson"},
                         timeout=12, verify="/root/.ccr/ca-bundle.crt")
        if r.status_code != 200:
            return None
        j = r.json()
        if j.get("code") != "Ok" or not j.get("routes"):
            return None
        rt = j["routes"][0]
        geo = [[c[1], c[0]] for c in rt["geometry"]["coordinates"]]   # -> [lat,lon]
        return {"geometry": geo, "km": round(rt["distance"] / 1000.0, 2),
                "min": round(rt["duration"] / 60.0, 1), "source": "osrm"}
    except Exception:  # noqa: BLE001
        return None
