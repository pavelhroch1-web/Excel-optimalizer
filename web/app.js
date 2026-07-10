// Field Force Optimizer - production live-planner cockpit. Talks to the
// backend API only via fetch(); holds no business logic - every action is a
// thin wrapper around one backend endpoint, which runs the unchanged
// desktop_client/engines/ Python engines.

const API_BASE = window.FFO_API_BASE || "http://localhost:8000";

const loginScreen = document.getElementById("login-screen");
const appScreen = document.getElementById("app-screen");
const loginForm = document.getElementById("login-form");
const loginError = document.getElementById("login-error");

const getToken = () => localStorage.getItem("ffo_token");
const setToken = (t) => localStorage.setItem("ffo_token", t);
const clearToken = () => localStorage.removeItem("ffo_token");

// Null-safe listener registration: if an element is missing (e.g. a browser
// serving a stale cached index.html against a newer app.js), skip it instead
// of throwing and blanking the whole page.
function on(id, event, handler) {
  const el = document.getElementById(id);
  if (el) el.addEventListener(event, handler);
}

async function apiFetch(path, options = {}) {
  const res = await fetch(API_BASE + path, {
    ...options,
    headers: {
      ...(options.headers || {}),
      ...(getToken() ? { Authorization: "Bearer " + getToken() } : {}),
    },
  });
  if (res.status === 401) {
    clearToken();
    showLogin();
    throw new Error("Přihlášení vypršelo.");
  }
  return res;
}

async function apiJson(path, options = {}) {
  const res = await apiFetch(path, options);
  const data = await res.json().catch(() => ({}));
  if (!res.ok) throw new Error(data.detail || ("Chyba " + res.status));
  return data;
}

function postJson(path, body) {
  return apiJson(path, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body),
  });
}

function showApp() {
  loginScreen.classList.add("hidden");
  appScreen.classList.remove("hidden");
  loadStatus();
  loadVersions();
  loadRules();
}
function showLogin() {
  appScreen.classList.add("hidden");
  loginScreen.classList.remove("hidden");
}

function setResult(id, msg, kind) {
  const el = document.getElementById(id);
  el.textContent = msg;
  el.className = "result" + (kind ? " " + kind : "");
}

// ---- login ----------------------------------------------------------------

loginForm.addEventListener("submit", async (e) => {
  e.preventDefault();
  loginError.textContent = "";
  const password = document.getElementById("password").value;
  try {
    const res = await fetch(API_BASE + "/api/login", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ password }),
    });
    if (!res.ok) {
      const d = await res.json().catch(() => ({}));
      loginError.textContent = d.detail || "Přihlášení selhalo.";
      return;
    }
    const data = await res.json();
    setToken(data.token);
    showApp();
  } catch (err) {
    loginError.textContent = "Nelze se spojit se serverem. (Server se možná probouzí, zkus to za 30 s.)";
  }
});

on("logout", "click", () => {
  clearToken();
  showLogin();
});

// ---- status ---------------------------------------------------------------

async function loadStatus() {
  try {
    const s = await apiJson("/api/status");
    document.getElementById("last-published").textContent = s.lastPublishedWeek ?? "—";
    document.getElementById("version-count").textContent = s.publishedVersions ?? 0;
    document.getElementById("draft-state").textContent = s.hasDraft ? "čeká" : "žádný";
  } catch (_) {}
}

// ---- 1) upload ------------------------------------------------------------

on("upload-form", "submit", async (e) => {
  e.preventDefault();
  const posFile = document.getElementById("pos-export").files[0];
  const saFiles = document.getElementById("salesapp-files").files;
  if (!posFile) return;
  const fd = new FormData();
  fd.append("pos_export", posFile);
  for (const f of saFiles) fd.append("salesapp", f);

  const btn = document.getElementById("upload-btn");
  btn.disabled = true;
  setResult("upload-result", "Nahrávám a počítám Import + Compliance… (může trvat i minutu)", "");
  try {
    const data = await apiJson("/api/draft/upload", { method: "POST", body: fd });
    const m = data.messages || {};
    setResult("upload-result",
      "Draft vytvořen. " + (m.import || "") + " " + (m.compliance || ""), "ok");
    loadStatus();
    loadRules();
  } catch (err) {
    setResult("upload-result", "Chyba: " + err.message, "err");
  } finally {
    btn.disabled = false;
  }
});

// ---- 2) generate ----------------------------------------------------------

on("generate-form", "submit", async (e) => {
  e.preventDefault();
  const start_week = parseInt(document.getElementById("start-week").value, 10);
  const length = parseInt(document.getElementById("length").value, 10);
  const btn = document.getElementById("generate-btn");
  btn.disabled = true;
  setResult("generate-result", "Generuji tour plán…", "");
  try {
    const data = await postJson("/api/draft/generate", { start_week, length });
    const m = data.messages || {};
    setResult("generate-result", m.planning || "Hotovo.", "ok");
    document.getElementById("cand-week").value = start_week;
    loadDraft();
  } catch (err) {
    setResult("generate-result", "Chyba: " + err.message, "err");
  } finally {
    btn.disabled = false;
  }
});

// ---- 3) candidates --------------------------------------------------------

let candCache = [];
let candWeek = null;

on("candidates-form", "submit", async (e) => {
  e.preventDefault();
  const week = parseInt(document.getElementById("cand-week").value, 10);
  candWeek = week;
  const tech = document.getElementById("cand-technician").value.trim();
  setResult("cand-result", "Počítám kandidáty (stejný engine jako Generovat)…", "");
  try {
    const q = "/api/draft/candidates?week=" + week + (tech ? "&technician=" + encodeURIComponent(tech) : "");
    const data = await apiJson(q);
    candCache = data.candidates || [];
    setResult("cand-result",
      `Týden ${data.week}: ${data.total} kandidátů, ${data.selected} vybraných.`, "ok");
    renderCandidates();
  } catch (err) {
    setResult("cand-result", "Chyba: " + err.message, "err");
  }
});

["cand-filter-status", "cand-filter-weeks", "cand-filter-text"].forEach((id) =>
  document.getElementById(id).addEventListener("input", renderCandidates));

function renderCandidates() {
  const status = document.getElementById("cand-filter-status").value;
  const minWeeks = parseInt(document.getElementById("cand-filter-weeks").value, 10);
  const text = document.getElementById("cand-filter-text").value.trim().toLowerCase();
  const body = document.getElementById("cand-body");
  body.innerHTML = "";
  const fmt = (n) => (n === null || n === undefined || n === "" ? "" : Number(n).toLocaleString("cs-CZ"));

  candCache
    .filter((c) => !status || c.status === status)
    .filter((c) => isNaN(minWeeks) || (c.weeksSinceLastVisit ?? -1) >= minWeeks)
    .filter((c) => !text ||
      String(c.pos).toLowerCase().includes(text) ||
      String(c.nazev || "").toLowerCase().includes(text))
    .forEach((c) => {
      const tr = document.createElement("tr");
      tr.className = c.status === "Vybráno" ? "sel" : (c.status.startsWith("Odloženo") ? "hold" : "");
      const cells = [
        c.status, c.pos, c.nazev, c.tech, c.kategorie, c.market, c.classification,
        c.core ? "ANO" : "", fmt(c.ppt), c.lastRealVisitDate || "", c.weeksSinceLastVisit ?? "",
        fmt(c.score), fmt(c.pptComponent), fmt(c.coreBonus), fmt(c.aBonus), fmt(c.gapPenalty),
        fmt(c.neglectedBonus), fmt(c.urgencyBoost), fmt(c.gpsBonus), c.explanation || "",
      ];
      cells.forEach((v, i) => {
        const td = document.createElement("td");
        if (i === 1) {
          const a = document.createElement("span");
          a.className = "pos-link";
          a.textContent = v;
          a.addEventListener("click", () => openPosDetail(c.pos, candWeek));
          td.appendChild(a);
        } else {
          td.textContent = v;
        }
        tr.appendChild(td);
      });
      body.appendChild(tr);
    });
}

// ---- 4) draft view + edits ------------------------------------------------

on("refresh-draft", "click", loadDraft);

async function loadDraft() {
  const body = document.getElementById("draft-body");
  body.innerHTML = "";
  try {
    const data = await apiJson("/api/draft");
    (data.rows || []).forEach((r) => {
      const tr = document.createElement("tr");
      const cells = [
        r.WEEK, r.DAY, r.POS, r.NAZEV_PROVOZOVNY, r.TECHNICIAN,
        r.PPT, r.lastRealVisitDate || "", r.weeksSinceLastVisit ?? "",
        r.terminalType || "", r.market || "", r.REASON_FRIENDLY || r.REASON || "",
      ];
      cells.forEach((v, i) => {
        const td = document.createElement("td");
        if (i === 2) {
          const a = document.createElement("span");
          a.className = "pos-link";
          a.textContent = v ?? "";
          a.addEventListener("click", () => openPosDetail(r.POS, r.WEEK));
          td.appendChild(a);
        } else {
          td.textContent = v ?? "";
        }
        tr.appendChild(td);
      });
      const tdBtn = document.createElement("td");
      const btn = document.createElement("button");
      btn.textContent = "Odebrat";
      btn.className = "ghost small";
      btn.addEventListener("click", () => removePos(r.WEEK, r.POS, r.TECHNICIAN));
      tdBtn.appendChild(btn);
      tr.appendChild(tdBtn);
      body.appendChild(tr);
    });
  } catch (err) {
    setResult("add-pos-result", "Chyba načtení návrhu: " + err.message, "err");
  }
}

async function removePos(week, pos, tech) {
  try {
    await postJson("/api/draft/remove-pos", { week, pos_id: String(pos), technician: tech });
    loadDraft();
  } catch (err) {
    setResult("add-pos-result", "Chyba: " + err.message, "err");
  }
}

on("add-pos-form", "submit", async (e) => {
  e.preventDefault();
  const body = {
    pos_id: document.getElementById("add-pos-id").value.trim(),
    week: parseInt(document.getElementById("add-pos-week").value, 10),
    day: document.getElementById("add-pos-day").value,
    technician: document.getElementById("add-pos-technician").value.trim(),
  };
  try {
    await postJson("/api/draft/add-pos", body);
    setResult("add-pos-result", "POS přidán.", "ok");
    loadDraft();
  } catch (err) {
    setResult("add-pos-result", "Chyba: " + err.message, "err");
  }
});

on("download-draft-btn", "click", () =>
  downloadFile("/api/draft/download", "MANAGER_PLAN_draft.xlsx"));

// ---- 5) publish -----------------------------------------------------------

on("publish-form", "submit", async (e) => {
  e.preventDefault();
  if (!confirm("Publikovat tour plán? Vytvoří se nová immutable verze, kterou už nepůjde změnit.")) return;
  const message = document.getElementById("publish-message").value.trim();
  const btn = document.getElementById("publish-btn");
  btn.disabled = true;
  setResult("publish-result", "Publikuji…", "");
  try {
    const data = await postJson("/api/publish", { message });
    const p = data.published;
    setResult("publish-result",
      `Publikováno jako ${p.id} (týden ${(p.publishedWeeks || []).join(", ")}). ${data.engineMessage || ""}`, "ok");
    loadStatus();
    loadVersions();
  } catch (err) {
    setResult("publish-result", "Chyba: " + err.message, "err");
  } finally {
    btn.disabled = false;
  }
});

// ---- 6) history + 7) download published -----------------------------------

on("refresh-versions", "click", loadVersions);

async function loadVersions() {
  const body = document.getElementById("versions-body");
  body.innerHTML = "";
  try {
    const data = await apiJson("/api/versions");
    (data.versions || []).forEach((v) => {
      const tr = document.createElement("tr");
      const when = v.publishedAt ? new Date(v.publishedAt).toLocaleString("cs-CZ") : "";
      [v.id, (v.publishedWeeks || []).join(", "), when, v.message || "", v.engineVersion || ""].forEach((val) => {
        const td = document.createElement("td");
        td.textContent = val;
        tr.appendChild(td);
      });
      const tdDl = document.createElement("td");
      const btn = document.createElement("button");
      btn.textContent = "Stáhnout";
      btn.className = "ghost small";
      btn.addEventListener("click", () =>
        downloadFile("/api/versions/" + v.id + "/manager-plan", "MANAGER_PLAN_" + v.id + ".xlsx"));
      tdDl.appendChild(btn);
      tr.appendChild(tdDl);
      body.appendChild(tr);
    });
  } catch (_) {}
}

async function downloadFile(path, filename) {
  try {
    const res = await apiFetch(path);
    if (!res.ok) throw new Error("Chyba " + res.status);
    const blob = await res.blob();
    const url = URL.createObjectURL(blob);
    const a = document.createElement("a");
    a.href = url;
    a.download = filename;
    a.click();
    URL.revokeObjectURL(url);
  } catch (err) {
    alert("Stažení selhalo: " + err.message);
  }
}

// ---- 2) planning filters (rule tables that constrain the engine input) ----
// These are NOT display filters: the Planning Engine reads TERMINAL_RULES /
// MARKET_RULES / CATEGORY_RULES / ACTIVITY_PLAN and excludes POS accordingly,
// so saving here changes which POS the plan is generated from.

let currentRules = null;
const CATEGORY_OPTIONS = ["CORE", "NORMAL", "EXCLUDE"];

async function loadRules() {
  try {
    currentRules = await apiJson("/api/rules");
    renderToggleList("rules-terminal", "TYP TERMINALU", currentRules.terminal);
    renderToggleList("rules-market", "MARKET", currentRules.market);
    renderDropdownList("rules-category", "CATEGORY", "RULE", currentRules.category, CATEGORY_OPTIONS);
    renderCampaigns(currentRules.campaigns);
  } catch (err) {
    setResult("rules-result", "Pravidla nelze načíst: " + err.message, "err");
  }
}

function renderToggleList(containerId, keyField, rows) {
  document.getElementById(containerId).innerHTML = (rows || [])
    .map((row, i) => `
    <label class="toggle-row">
      <input type="checkbox" data-index="${i}" ${row.ACTIVE === "YES" ? "checked" : ""}>
      ${row[keyField]}
    </label>`).join("");
}

function renderDropdownList(containerId, keyField, valueField, rows, options) {
  document.getElementById(containerId).innerHTML = (rows || [])
    .map((row, i) => `
    <label class="dropdown-row">
      ${row[keyField]}
      <select data-index="${i}">
        ${options.map((o) => `<option value="${o}" ${row[valueField] === o ? "selected" : ""}>${o}</option>`).join("")}
      </select>
    </label>`).join("");
}

function renderCampaigns(rows) {
  document.getElementById("rules-campaigns-body").innerHTML = (rows || [])
    .map((row, i) => `
    <tr>
      <td>${row.TYPE ?? ""}</td>
      <td>${row.ACTIVITY ?? ""}</td>
      <td><input type="number" data-index="${i}" data-field="START_WEEK" value="${row.START_WEEK ?? ""}"></td>
      <td><input type="number" data-index="${i}" data-field="END_WEEK" value="${row.END_WEEK ?? ""}"></td>
      <td><input type="number" data-index="${i}" data-field="PRIORITY" value="${row.PRIORITY ?? ""}"></td>
      <td>
        <select data-index="${i}" data-field="OVERRIDE_GAP">
          <option value="YES" ${row.OVERRIDE_GAP === "YES" ? "selected" : ""}>YES</option>
          <option value="NO" ${row.OVERRIDE_GAP === "NO" ? "selected" : ""}>NO</option>
        </select>
      </td>
    </tr>`).join("");
}

function collectToggleList(containerId, rows) {
  const updated = (rows || []).map((r) => ({ ...r }));
  document.querySelectorAll(`#${containerId} input[type="checkbox"]`).forEach((input) => {
    updated[parseInt(input.dataset.index, 10)].ACTIVE = input.checked ? "YES" : "NO";
  });
  return updated;
}

function collectDropdownList(containerId, valueField, rows) {
  const updated = (rows || []).map((r) => ({ ...r }));
  document.querySelectorAll(`#${containerId} select`).forEach((sel) => {
    updated[parseInt(sel.dataset.index, 10)][valueField] = sel.value;
  });
  return updated;
}

function collectCampaigns(rows) {
  const updated = (rows || []).map((r) => ({ ...r }));
  document.querySelectorAll("#rules-campaigns-body input, #rules-campaigns-body select").forEach((input) => {
    const i = parseInt(input.dataset.index, 10);
    updated[i][input.dataset.field] = input.type === "number" ? Number(input.value) : input.value;
  });
  return updated;
}

on("reload-rules-btn", "click", loadRules);

on("save-rules-btn", "click", async () => {
  setResult("rules-result", "Ukládám…", "");
  try {
    const payload = [
      ["TERMINAL_RULES", collectToggleList("rules-terminal", currentRules.terminal)],
      ["MARKET_RULES", collectToggleList("rules-market", currentRules.market)],
      ["CATEGORY_RULES", collectDropdownList("rules-category", "RULE", currentRules.category)],
      ["ACTIVITY_PLAN", collectCampaigns(currentRules.campaigns)],
    ];
    for (const [sheet, rows] of payload) {
      await postJson("/api/rules", { sheet, rows });
    }
    setResult("rules-result", "Uloženo. Tour plán se teď vygeneruje jen z vybraných POS.", "ok");
    await loadRules();
  } catch (err) {
    setResult("rules-result", "Chyba: " + err.message, "err");
  }
});

// ---- Decision Support: Co kdyby... ----------------------------------------

on("whatif-form", "submit", async (e) => {
  e.preventDefault();
  const week = parseInt(document.getElementById("whatif-week").value, 10) ||
    parseInt(document.getElementById("cand-week").value, 10) ||
    parseInt(document.getElementById("start-week").value, 10);
  if (!week) { setResult("whatif-result", "Zadej týden.", "err"); return; }
  document.getElementById("whatif-week").value = week;
  setResult("whatif-result", "Počítám dopady (jeden běh Planning Engine)…", "");
  document.getElementById("whatif-body").innerHTML = "";
  try {
    const d = await apiJson("/api/draft/what-if?week=" + week);
    const b = d.baseline || {};
    setResult("whatif-result",
      `Týden ${d.week}: teď ${b.candidates} kandidátů, ${b.selected} naplánováno, ${b.heldBack} odloženo.`, "ok");
    const rows = (d.scenarios || []).map((s) =>
      `<tr><td>${escWi(s.label)}</td><td class="wi-impact ${s.delta >= 0 ? "pos" : "neg"}">${escWi(s.impact)}</td><td>${escWi(s.metric)}</td></tr>`).join("");
    document.getElementById("whatif-body").innerHTML = rows
      ? `<table class="pd-score-table wi-table"><thead><tr><th>Kdyby…</th><th>Dopad</th><th>Metrika</th></tr></thead><tbody>${rows}</tbody></table>`
      : `<p class="pd-sub">Žádné páčky s dopadem pro tento týden.</p>`;
  } catch (err) {
    setResult("whatif-result", "Chyba: " + err.message, "err");
  }
});

const escWi = (s) => String(s ?? "").replace(/[&<>]/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;" }[c]));

// ---- POS Detail panel (read-only diagnostic) ------------------------------

const num = (n) => (n === null || n === undefined || n === "" ? "—" : Number(n).toLocaleString("cs-CZ"));
const esc = (s) => String(s ?? "").replace(/[&<>]/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;" }[c]));

function closePosDetail() {
  document.getElementById("pos-detail-overlay").classList.add("hidden");
}
on("pd-close", "click", closePosDetail);
on("pos-detail-overlay", "click", (e) => {
  if (e.target.id === "pos-detail-overlay") closePosDetail();
});

async function openPosDetail(posId, week) {
  const overlay = document.getElementById("pos-detail-overlay");
  const bodyEl = document.getElementById("pd-body");
  document.getElementById("pd-title").textContent = "POS " + posId;
  bodyEl.innerHTML = "<p class='pd-sub'>Načítám diagnostiku…</p>";
  overlay.classList.remove("hidden");
  if (!week) {
    bodyEl.innerHTML = "<p class='result err'>Nejdřív zadej týden v sekci Kandidáti nebo otevři POS z návrhu.</p>";
    return;
  }
  try {
    const d = await apiJson("/api/draft/pos/" + encodeURIComponent(posId) + "?week=" + week);
    if (!d.found) {
      bodyEl.innerHTML = "<p class='result err'>POS nebyl nalezen v datech.</p>";
      return;
    }
    document.getElementById("pd-title").textContent = "POS " + d.pos + " · " + (d.nazev || "");
    bodyEl.innerHTML = renderPosDetail(d, week);
  } catch (err) {
    bodyEl.innerHTML = "<p class='result err'>Chyba: " + esc(err.message) + "</p>";
  }
}

function renderPosDetail(d, week) {
  const verdictClass = d.status === "Vybráno" ? "sel"
    : (String(d.status).startsWith("Odloženo") ? "hold" : "no");
  const rows = [
    ["Partner / síť", d.market],
    ["Typ terminálu", d.terminalType],
    ["Kategorie", esc(d.kategorie) + (d.categoryRule ? ` <span class="pd-chip">${esc(d.categoryRule)}</span>` : "")],
    ["Klasifikace", d.classification],
    ["Technik", d.managerOverrideTechnician || d.assignedTechnician || d.tech || "—"],
    ["Stav POS", d.posStatus],
    ["Adresa", [d.street, d.city].filter(Boolean).join(", ")],
    ["Poslední návštěva", d.lastRealVisitDate || "—"],
    ["Týdnů od návštěvy", d.weeksSinceLastVisit ?? "—"],
    ["PPT", num(d.ppt)],
  ];
  let html = "";
  html += `<div class="pd-verdict ${verdictClass}"><strong>${esc(d.status)}</strong> · ${esc(d.explanation || "")}</div>`;
  html += `<p class="pd-sub">Diagnostika pro týden ${week} – přesně data a rozhodnutí Planning Engine.</p>`;

  // Decision Support: engine recommendation
  const rec = d.recommendation;
  if (rec) {
    html += `<div class="pd-rec ${verdictClass}">`;
    html += `<div class="pd-rec-head">Doporučení enginu: <strong>${esc(rec.verdict)}</strong></div>`;
    if ((rec.reasons || []).length) {
      html += `<ul class="pd-rec-list">` + rec.reasons.map((r) => `<li>${esc(r)}</li>`).join("") + `</ul>`;
    }
    if (d.includeLever) {
      html += `<div class="pd-rec-lever">Aby se stal kandidátem: ${esc(d.includeLever)}.</div>`;
    }
    html += `</div>`;
  }

  html += `<dl class="pd-grid">` +
    rows.map(([k, v]) => `<dt>${esc(k)}</dt><dd>${v ?? "—"}</dd>`).join("") + `</dl>`;

  // compliance
  if (d.lastCompliance) {
    const c = d.lastCompliance;
    html += `<div class="pd-section">Poslední compliance</div>`;
    html += `<dl class="pd-grid"><dt>Stav</dt><dd>${esc(c.status)}</dd>` +
      `<dt>Plánovaný týden</dt><dd>${esc(c.plannedWeek)}/${esc(c.plannedYear)}</dd>` +
      (c.matchedActualDate ? `<dt>Reálná návštěva</dt><dd>${esc(c.matchedActualDate)}</dd>` : "") + `</dl>`;
  }

  // active campaigns
  html += `<div class="pd-section">Aktivní kampaně (týden ${week})</div>`;
  if ((d.activeCampaigns || []).length) {
    html += d.activeCampaigns.map((c) =>
      `<span class="pd-chip">${esc(c.type)} · ${esc(c.activity)} (${c.startWeek}–${c.endWeek})</span>`).join("");
  } else {
    html += `<p class="pd-sub">Žádná aktivní kampaň pro tento týden.</p>`;
  }

  // score breakdown (only for scored candidates)
  if (d.isCandidate && d.score !== null && d.score !== undefined) {
    const comp = [
      ["CORE bonus", d.coreBonus],
      ["Klasifikace A", d.aBonus],
      ["PPT složka", d.pptComponent],
      ["Penalizace min. rozestup", d.gapPenalty],
      ["Bonus zanedbáno", d.neglectedBonus],
      ["Základní skóre", d.baseScore],
      ["Urgence (boost)", d.urgencyBoost],
      ["GPS shluk (bonus)", d.gpsBonus],
    ];
    html += `<div class="pd-section">Rozpad skóre po pravidlech</div>`;
    html += `<table class="pd-score-table"><tbody>` +
      comp.map(([k, v]) => `<tr><td>${esc(k)}</td><td>${num(v)}</td></tr>`).join("") +
      `<tr class="total"><td>Výsledné skóre</td><td>${num(d.score)}</td></tr></tbody></table>`;
    if (d.mandatoryRuleId) {
      html += `<p class="pd-sub">Povinné pravidlo (cadence): <span class="pd-chip">${esc(d.mandatoryRuleId)}</span></p>`;
    }
  } else {
    html += `<div class="pd-section">Skóre</div><p class="pd-sub">POS není kandidát – engine ho vyřadil před bodováním, proto nemá skóre.</p>`;
  }

  // visit history
  if ((d.visitHistory || []).length) {
    html += `<div class="pd-section">Poslední návštěvy (SalesApp)</div>`;
    html += `<table class="pd-score-table"><tbody>` +
      d.visitHistory.slice(0, 8).map((v) =>
        `<tr><td>${esc(v.date)}</td><td>${esc(v.executor || "")}</td></tr>`).join("") + `</tbody></table>`;
  }
  return html;
}

// ---- boot -----------------------------------------------------------------

if (getToken()) showApp();
else showLogin();
