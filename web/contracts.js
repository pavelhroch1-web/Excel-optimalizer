/**
 * Shared frontend ⇄ backend contract (DTOs).
 *
 * These JSDoc typedefs mirror the exact shapes the backend returns, so the
 * frontend and backend "mluví stejným jazykem". Editors with TS-in-JS checking
 * (VS Code) type-check against these. The canonical source is
 * docs/API_CONTRACT.md; keep the three in sync when an endpoint changes.
 */

/**
 * The single response every import endpoint returns:
 *   POST /api/import/auto, /api/import/{kind}, /api/import/workbook, /api/import/sample
 *
 * @typedef {Object} ImportResult
 * @property {boolean}  ok         True ONLY if data actually landed. Never show
 *                                 a success state when this is false.
 * @property {string}   kind       pos_master | salesapp | activity_plan |
 *                                 tourplan | workbook | unknown
 * @property {string}   kindLabel  Human label for `kind` ("POS Master", …).
 * @property {Object<string, number|Object>} imported  table → rows imported.
 * @property {number}   total      Primary rows that landed (0 when ok=false).
 * @property {string[]} warnings   Non-fatal issues (e.g. missing optional PPT).
 * @property {?string}  error      Precise reason when ok=false; null otherwise.
 * @property {?string}  file       Original filename.
 * @property {string[]} recomputed What was recomputed after import.
 */

/**
 * Render an ImportResult into a host element with ONE honest rule:
 * green success only when `r.ok && r.total > 0`. Anything else is an error.
 *
 * @param {HTMLElement} host
 * @param {ImportResult} r
 * @param {(html:string)=>string} [icoFn]  optional icon helper (app.js `ico`)
 * @returns {boolean} whether it was a real success
 */
function renderImportResult(host, r, icoFn) {
  const esc = (s) => String(s == null ? "" : s).replace(/[&<>"']/g, (c) =>
    ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
  const check = icoFn ? icoFn("check") : "✓";
  if (!r || !r.ok || !(r.total > 0)) {
    const why = (r && r.error) || "Import se nezdařil.";
    host.innerHTML =
      `<div class="import-bad"><div class="ib-h">⚠️ Nenaimportováno</div>` +
      `<div class="ib-s">${esc(why)}</div></div>`;
    return false;
  }
  const rows = Object.entries(r.imported || {})
    .filter(([, v]) => typeof v === "number")
    .map(([k, v]) => `${esc(k)}: <b>${v}</b>`).join(" · ");
  const warn = (r.warnings && r.warnings.length)
    ? `<div class="ib-warn">Upozornění: ${r.warnings.map(esc).join(", ")}. ` +
      `Import proběhl, ale zvaž doplnění.</div>` : "";
  host.innerHTML =
    `<div class="import-ok">${check}<div>` +
    `<div class="io-h">Naimportováno: ${esc(r.kindLabel || r.kind)} — ${r.total} záznamů</div>` +
    `<div class="io-s">${esc(r.file || "")}${rows ? " · " + rows : ""}</div></div></div>` + warn;
  return true;
}

// expose for app.js (loaded after this file)
window.renderImportResult = renderImportResult;
