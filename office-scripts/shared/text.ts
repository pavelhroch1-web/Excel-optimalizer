// Dev-source reference. Office Scripts cannot import across files, so this block is
// copied verbatim into the top of every deployable script in office-scripts/*.ts
// (see office-scripts/README.md). Kept here as the single source of truth to diff against.
// Ported unchanged from V10.5.5 - confirmed stays-as-is in ARCHITECTURE.md Phase 0 review.

function norm(v: string): string {
  return v
    .toUpperCase()
    .normalize("NFD")
    .replace(/[\u0300-\u036f]/g, "")
    .trim();
}
