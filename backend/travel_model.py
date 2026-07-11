"""Realistic travel-time model (city vs. open-road speeds).

We deliberately avoid treating every kilometre the same. Short hops are
city-dominated (slow), long legs are open-road dominated (fast), so a leg's
effective speed ramps with its distance. This turns distances into realistic
travel MINUTES - a far more useful figure for a manager than raw km.

For real driven days we already know the true travel time from the SalesApp
timestamps; this model is used for the hypothetical cases (an optimal ordering,
a trip that could have been combined) and wherever a timestamp is missing.

Speeds are constants here but written so a real routing engine (OSRM /
OpenRouteService) can later replace estimate_minutes() behind the same call,
with no change to the callers.
"""
from __future__ import annotations

CITY_SPEED_KMH = 32.0        # dense town driving
OPEN_SPEED_KMH = 72.0        # open road / between towns
RAMP_START_KM = 2.0          # up to here a leg is essentially all-city
RAMP_END_KM = 25.0           # from here a leg is essentially all open-road


def effective_speed_kmh(km: float) -> float:
    """A leg's blended average speed: all-city for short hops, ramping to
    open-road speed for long legs."""
    if km <= RAMP_START_KM:
        return CITY_SPEED_KMH
    if km >= RAMP_END_KM:
        return OPEN_SPEED_KMH
    frac = (km - RAMP_START_KM) / (RAMP_END_KM - RAMP_START_KM)
    return CITY_SPEED_KMH + frac * (OPEN_SPEED_KMH - CITY_SPEED_KMH)


def estimate_minutes(km: float | None) -> float:
    """Estimated driving time for a single leg of `km` kilometres."""
    if not km or km <= 0:
        return 0.0
    return round(60.0 * km / effective_speed_kmh(km), 1)


def minutes_for_legs(leg_kms) -> float:
    """Total estimated driving time for a list of leg distances."""
    return round(sum(estimate_minutes(k) for k in leg_kms if k), 1)


def describe() -> dict:
    """Transparent read-out of the model so it can be shown / tuned."""
    return {
        "citySpeedKmh": CITY_SPEED_KMH, "openSpeedKmh": OPEN_SPEED_KMH,
        "rampFromKm": RAMP_START_KM, "rampToKm": RAMP_END_KM,
        "note": "Rychlost přejezdu roste se vzdáleností: krátké úseky městskou "
                "rychlostí, dlouhé úseky rychlostí mimo obec.",
    }
