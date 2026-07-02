// Dev-source reference, duplicated into each deployable script (see README.md).
// Ported unchanged from V10.5.5 - confirmed "stays as-is" in ARCHITECTURE.md Phase 0 review.
// Flat-earth approximation (111 km/degree latitude, ~72 km/degree longitude at
// Czech latitudes ~50N). Good enough at this scale; no need for haversine.

function distanceKm(ax: number, ay: number, bx: number, by: number): number {
  const dx = (ax - bx) * 111;
  const dy = (ay - by) * 72;
  return Math.sqrt(dx * dx + dy * dy);
}
