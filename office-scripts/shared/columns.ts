// Dev-source reference, duplicated into each deployable script (see README.md).
// Ported unchanged from V10.5.5 - confirmed "stays as-is" in ARCHITECTURE.md Phase 0 review.
// Dynamic column mapping: import sheets don't need a fixed column order, only a
// header row whose text is recognizable. Requires norm() from text.ts.

// SYNC-BLOCK-START: columns.ts
function buildHeaderIndex(headerRow: (string | number | boolean)[]): string[] {
  return headerRow.map((x) => norm(String(x)));
}

// Exact match after normalization (diacritics/case-insensitive). Use for fields
// where the header text is stable and you want to fail loudly on a rename.
function exactCol(headers: string[], name: string): number {
  const n = norm(name);
  for (let i = 0; i < headers.length; i++) {
    if (headers[i] == n) {
      return i;
    }
  }
  return -1;
}

// Substring match after normalization. Use only for fields that may have
// slightly different header text across export versions (e.g. "TECH" inside
// "TECHNIK"). Prefer exactCol wherever the header text is otherwise stable -
// substring matching is intentionally used sparingly (V10.5.5 used it only for
// TECH and PTT columns).
function col(headers: string[], name: string): number {
  const n = norm(name);
  for (let i = 0; i < headers.length; i++) {
    if (headers[i].includes(n)) {
      return i;
    }
  }
  return -1;
}
// SYNC-BLOCK-END: columns.ts
