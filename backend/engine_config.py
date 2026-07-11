"""Engine business constants + score weights as editable config.

Audit finding (2026-07-11): the Planning Engine reads a number of business
constants that had NO way to change them from the app - they lived only as
`setting(name, fallback)` fallbacks inside the engine, or as SCORE_PROFILES /
PARETO_GROUPS sheet values that nothing overlaid. Worse, the `scoring` settings
namespace was shown in the UI but never reached the engine (db_state never
mapped it), so editing "Váha PPT ve skóre" did nothing.

This module closes that gap the same way cadence_config / model_config do: it
reads the manager's *explicit* overrides from the settings table and overlays
them onto the exact structures the engine already reads -

  * scoring.*        -> SCORE_PROFILES DEFAULT rows (CORE / KATEGORIZACE_A /
                        PPT / NEGLECTED_BONUS score weights)
  * engine.premium_top_percent    -> PARETO_GROUPS PREMIUM_TOP20 boundaryValue
  * engine.geo_cluster_* / urgency_* / sync_window_weeks /
    gps_extra_radius_meters       -> CONTROL knobs (the engine's setting() reads)

The engine's algorithm is unchanged; it still just READS these. Crucially the
overlay is INERT until the manager actually overrides a value: apply_to_state
only writes keys that have an explicit row in `settings`, so with no edits the
plan stays byte-identical to before (same guarantee as the other overlays).

NOTE (honesty): min_gap_penalty is a hardcoded -1_000_000 inside
core_logic.compute_score and is NOT read from any sheet, so it cannot be made
configurable without an engine change. It is intentionally NOT wired here; the
inventory documents it as engine-internal.
"""
from __future__ import annotations

import db

# scoring.<key> -> SCORE_PROFILES weight name the engine reads (DEFAULT profile)
_SCORE_WEIGHTS = {
    "core_bonus": "CORE",
    "category_a_bonus": "KATEGORIZACE_A",
    "ppt_weight": "PPT",
    "neglected_bonus": "NEGLECTED_BONUS",
}

# engine.<key> -> CONTROL key the engine's setting() reads
_CONTROL = {
    "geo_cluster_radius_km": "GEO_CLUSTER_RADIUS_KM",
    "geo_cluster_bonus_factor": "GEO_CLUSTER_BONUS_FACTOR",
    "geo_cluster_max_bonus": "GEO_CLUSTER_MAX_BONUS",
    "urgency_boost_max": "URGENCY_BOOST_MAX",
    "urgency_ramp_start_ratio": "URGENCY_BOOST_RAMP_START_RATIO",
    "sync_window_weeks": "SYNC_WINDOW_WEEKS",
    "gps_extra_radius_meters": "GPS_EXTRA_RADIUS_METERS",
}


def _overrides(namespace: str) -> dict:
    """Only GLOBAL, explicitly-set values (so the overlay is inert on defaults)."""
    out = {}
    for r in db.get("SELECT key, value FROM settings WHERE namespace=? AND scope='global'",
                    (namespace,)):
        out[r["key"]] = r["value"]
    return out


def _num(v):
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


def apply_to_state(state: dict) -> int:
    """Overlay explicitly-overridden engine constants + score weights onto the
    engine's SCORE_PROFILES / PARETO_GROUPS / CONTROL. Returns cells changed."""
    import brain
    n = 0

    # 1) score weights -> SCORE_PROFILES DEFAULT rows
    sc = _overrides("scoring")
    if sc:
        sheet = state.get("SCORE_PROFILES")
        if sheet:
            want = {}
            for key, weight_name in _SCORE_WEIGHTS.items():
                if key in sc and _num(sc[key]) is not None:
                    want[weight_name.upper()] = _num(sc[key])
            if want:
                for row in sheet[1:]:
                    if len(row) < 3:
                        continue
                    if str(row[0]).strip().upper() == "DEFAULT":
                        wname = str(row[1]).strip().upper()
                        if wname in want:
                            row[2] = want[wname]
                            n += 1

    # 2) engine constants -> CONTROL knobs
    en = _overrides("engine")
    for key, control_key in _CONTROL.items():
        if key in en and _num(en[key]) is not None:
            brain._set_control(state, control_key, _num(en[key]))
            n += 1

    # 3) premium percentile -> PARETO_GROUPS PREMIUM_TOP20 boundaryValue
    if "premium_top_percent" in en and _num(en["premium_top_percent"]) is not None:
        sheet = state.get("PARETO_GROUPS")
        if sheet and sheet[0]:
            h = {str(c): i for i, c in enumerate(sheet[0])}
            ti, bi = h.get("tierId"), h.get("boundaryValue")
            if ti is not None and bi is not None:
                for row in sheet[1:]:
                    if ti < len(row) and str(row[ti]).strip() == "PREMIUM_TOP20":
                        while len(row) <= bi:
                            row.append("")
                        row[bi] = _num(en["premium_top_percent"])
                        n += 1
    return n


def inventory() -> list[dict]:
    """Structured read-out of every business constant this module governs, with
    its engine default, the effective value, whether it's overridden, and what
    part of the algorithm it drives. Powers the 'Inventura parametrů' UI."""
    import settings
    sc_eff = settings.effective("scoring")
    en_eff = settings.effective("engine")
    sc_ov, en_ov = _overrides("scoring"), _overrides("engine")
    rows = []

    def add(ns, key, eff, ov, default, drives, target):
        rows.append({
            "namespace": ns, "key": key, "value": eff,
            "overridden": key in ov, "default": default,
            "drives": drives, "target": target,
        })

    add("scoring", "core_bonus", sc_eff.get("core_bonus"), sc_ov, 100000000,
        "Obchodní bonus za CORE POS", "SCORE_PROFILES.CORE")
    add("scoring", "category_a_bonus", sc_eff.get("category_a_bonus"), sc_ov, 10000000,
        "Bonus za kategorii A", "SCORE_PROFILES.KATEGORIZACE_A")
    add("scoring", "ppt_weight", sc_eff.get("ppt_weight"), sc_ov, 1,
        "Váha PPT (obchodní hodnota POS) ve skóre", "SCORE_PROFILES.PPT")
    add("scoring", "neglected_bonus", sc_eff.get("neglected_bonus"), sc_ov, 50000,
        "Bonus za dlouho nenavštívené POS", "SCORE_PROFILES.NEGLECTED_BONUS")
    add("engine", "premium_top_percent", en_eff.get("premium_top_percent"), en_ov, 20,
        "Kolik % nejlepších POS je prémiových (drží se před kampaní)", "PARETO_GROUPS.PREMIUM_TOP20")
    add("engine", "geo_cluster_radius_km", en_eff.get("geo_cluster_radius_km"), en_ov, 3,
        "Poloměr, v němž se POS navzájem táhnou do jedné trasy", "CONTROL.GEO_CLUSTER_RADIUS_KM")
    add("engine", "geo_cluster_bonus_factor", en_eff.get("geo_cluster_bonus_factor"), en_ov, 0.01,
        "Jak silně geografická blízkost zvyšuje skóre", "CONTROL.GEO_CLUSTER_BONUS_FACTOR")
    add("engine", "geo_cluster_max_bonus", en_eff.get("geo_cluster_max_bonus"), en_ov, 5000,
        "Strop clusterového bonusu", "CONTROL.GEO_CLUSTER_MAX_BONUS")
    add("engine", "urgency_boost_max", en_eff.get("urgency_boost_max"), en_ov, 20000,
        "Max. proaktivní boost, jak se POS blíží termínu kadence", "CONTROL.URGENCY_BOOST_MAX")
    add("engine", "urgency_ramp_start_ratio", en_eff.get("urgency_ramp_start_ratio"), en_ov, 0.5,
        "Od jaké části termínu začíná urgence růst", "CONTROL.URGENCY_BOOST_RAMP_START_RATIO")
    add("engine", "sync_window_weeks", en_eff.get("sync_window_weeks"), en_ov, 1,
        "Okno dopředu pro změnu Losy/Lottery – drží prémiové POS na kampaň", "CONTROL.SYNC_WINDOW_WEEKS")
    add("engine", "gps_extra_radius_meters", en_eff.get("gps_extra_radius_meters"), en_ov, 300,
        "Poloměr pro GPS extra návštěvy po cestě", "CONTROL.GPS_EXTRA_RADIUS_METERS")
    return rows
