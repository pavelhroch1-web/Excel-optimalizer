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
  loadStrategyModes();
  loadPlannerModes();
  loadRouteTechnicians();
  loadExclusionCount();
  loadPriority();
  loadReassignments();
  loadAlerts();
  loadRactTechnicians();
  loadLiveTechnicians();
  loadLive();
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

async function handleUpload(e) {
  if (e) e.preventDefault();
  setResult("upload-result", "Připravuji nahrání…", "");
  try {
    const posEl = document.getElementById("pos-export");
    const saEl = document.getElementById("salesapp-files");
    const posFile = posEl && posEl.files[0];
    const saFiles = (saEl && saEl.files) || [];
    if (!posFile && saFiles.length === 0) {
      setResult("upload-result", "Vyber aspoň jeden soubor: SalesApp export (týdenní) a/nebo POS export.", "err");
      return;
    }
    const fd = new FormData();
    if (posFile) fd.append("pos_export", posFile);
    for (const f of saFiles) fd.append("salesapp", f);

    const btn = document.getElementById("upload-btn");
    if (btn) btn.disabled = true;
    const what = posFile ? "POS export + " + saFiles.length + "× SalesApp"
      : saFiles.length + "× SalesApp (síť z posledního snapshotu)";
    setResult("upload-result",
      `Nahrávám (${what}) a počítám Import + Compliance… Může to trvat 1–3 min a server se možná probouzí – nezavírej stránku.`, "");
    const ctrl = new AbortController();
    const timer = setTimeout(() => ctrl.abort(), 300000); // 5 min hard cap
    try {
      const res = await apiFetch("/api/draft/upload", { method: "POST", body: fd, signal: ctrl.signal });
      const data = await res.json().catch(() => ({}));
      if (!res.ok) throw new Error(data.detail || ("Server vrátil " + res.status));
      const m = data.messages || {};
      setResult("upload-result", "Hotovo – Draft vytvořen. " + (m.import || "") + " " + (m.compliance || ""), "ok");
      loadStatus();
      loadRules();
    } catch (err) {
      const msg = err.name === "AbortError"
        ? "Vypršel čas (5 min). Server se možná probouzí – zkus to prosím ještě jednou."
        : (err.message || err);
      setResult("upload-result", "Chyba při nahrávání: " + msg, "err");
    } finally {
      clearTimeout(timer);
      if (btn) btn.disabled = false;
    }
  } catch (err) {
    setResult("upload-result", "Chyba: " + (err && err.message ? err.message : err), "err");
  }
}
on("upload-form", "submit", handleUpload);
on("upload-btn", "click", (e) => { e.preventDefault(); handleUpload(e); });

// Immediate confirmation that file selection registered (so it never feels
// like "nothing happens"): list chosen files the moment they are picked.
function confirmUploadSelection() {
  const posEl = document.getElementById("pos-export");
  const saEl = document.getElementById("salesapp-files");
  const names = [];
  if (saEl) for (const f of saEl.files) names.push(f.name);
  if (posEl && posEl.files[0]) names.push(posEl.files[0].name + " (POS)");
  if (names.length) {
    setResult("upload-result", "Vybráno " + names.length + " souborů: " + names.join(", ") +
      " — teď klikni „Nahrát a vytvořit Draft“.", "ok");
  }
}
on("salesapp-files", "change", confirmUploadSelection);
on("pos-export", "change", confirmUploadSelection);

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

// ---- Field Brain: strategy modes + pre-flight scorecard -------------------

let brainModesLoaded = false;

async function loadStrategyModes() {
  if (brainModesLoaded) return;
  try {
    const d = await apiJson("/api/strategy-modes");
    const sel = document.getElementById("brain-mode");
    if (!sel) return;
    sel.innerHTML = (d.modes || []).map((m) => `<option value="${escWi(m.id)}">${escWi(m.label)}</option>`).join("");
    sel._descs = {};
    (d.modes || []).forEach((m) => (sel._descs[m.id] = m.desc));
    const showDesc = () => {
      const el = document.getElementById("brain-mode-desc");
      if (el) el.textContent = sel._descs[sel.value] || "";
    };
    sel.addEventListener("change", showDesc);
    showDesc();
    brainModesLoaded = true;
  } catch (_) {}
}

on("brain-form", "submit", async (e) => {
  e.preventDefault();
  const body = brainRequest();
  setResult("brain-result", "Simuluji horizont stejným Planning Enginem a počítám pokrytí cílů…", "");
  document.getElementById("brain-scorecard").innerHTML = "";
  document.getElementById("brain-generate-row").style.display = "none";
  try {
    const sc = await postJson("/api/draft/preflight", body);
    setResult("brain-result", "Pre-flight hotový. Zkontroluj pokrytí, pak generuj.", "ok");
    document.getElementById("brain-scorecard").innerHTML = renderScorecard(sc);
    document.getElementById("brain-generate-row").style.display = "flex";
  } catch (err) {
    setResult("brain-result", "Chyba: " + err.message, "err");
  }
});

on("brain-generate-btn", "click", async () => {
  const r = brainRequest();
  setResult("brain-result", "Generuji plán ve zvoleném režimu…", "");
  try {
    const d = await postJson("/api/draft/generate",
      { start_week: r.start_week, length: r.length, mode: r.mode, visits_per_tech_week: r.visits_per_tech_week });
    setResult("brain-result", (d.messages && d.messages.planning) || "Plán vygenerován.", "ok");
    loadDraft();
    loadStatus();
  } catch (err) {
    setResult("brain-result", "Chyba: " + err.message, "err");
  }
});

function brainRequest() {
  const techs = parseInt(document.getElementById("brain-techs").value, 10);
  return {
    start_week: parseInt(document.getElementById("brain-week").value, 10),
    length: parseInt(document.getElementById("brain-length").value, 10) || 5,
    mode: document.getElementById("brain-mode").value,
    visits_per_tech_week: parseFloat(document.getElementById("brain-visits").value) || null,
    tech_count_override: isNaN(techs) ? null : techs,
  };
}

function tile(label, value, sub, cls) {
  return `<div class="bt ${cls || ""}"><div class="bt-v">${value}</div><div class="bt-l">${escWi(label)}</div>${sub ? `<div class="bt-s">${escWi(sub)}</div>` : ""}</div>`;
}

function renderScorecard(sc) {
  const c = sc.capacity;
  const capCls = c.utilizationPct != null && c.utilizationPct > 100 ? "warn" : "ok";
  let h = `<div class="brain-tiles">`;
  h += tile("Kapacita", c.totalCapacity != null ? Math.round(c.totalCapacity) : "—",
    `${c.technicians} tech × ${c.visitsPerTechWeek || "?"}/týd × ${c.weeks}`, "");
  h += tile("Naplánováno", c.plannedVisits, c.utilizationPct != null ? c.utilizationPct + " % kapacity" : "", capCls);
  h += tile("CORE", sc.core.pct + " %", `${sc.core.covered}/${sc.core.due}`, sc.core.pct >= 95 ? "ok" : "warn");
  h += tile("Cadence GECO/CORN", sc.cadence.pct + " %", `${sc.cadence.covered}/${sc.cadence.overdue}`, sc.cadence.pct >= 99 ? "ok" : "warn");
  h += tile("Neglect zbyde", sc.neglect.remainingAfter, `z ${sc.neglect.backlogBefore} (dojede ${sc.neglect.cleared})`, sc.neglect.remainingAfter === 0 ? "ok" : "warn");
  h += `</div>`;

  if ((sc.campaigns || []).length) {
    h += `<div class="pd-section">Pokrytí kampaní v horizontu</div>`;
    h += `<table class="pd-score-table"><thead><tr><th>Kampaň</th><th>Typ</th><th>Týdny</th><th>Pokrytí</th><th>Riziko</th></tr></thead><tbody>`;
    h += sc.campaigns.map((cp) => {
      const cls = cp.pct >= 90 ? "wi-impact pos" : "wi-impact neg";
      return `<tr><td>${escWi(cp.name)}</td><td>${escWi(cp.type)}</td><td>t${cp.startWeek}–${cp.endWeek}</td>` +
        `<td class="${cls}">≈ ${cp.pct} %</td><td>${cp.riskPct} %</td></tr>`;
    }).join("");
    h += `</tbody></table>`;
    h += `<p class="pd-sub">Pozn.: „poptávka" kampaně je zatím odhad = počet aktivních POS (sloupec ODHAD v Activity Planu neobsahuje číslo). Řekni mi, jak se má cílová množina Sportky/losů definovat, a čísla zpřesním.</p>`;
  }

  const byWeek = Object.entries(sc.plannedByWeek || {}).map(([w, n]) => `t${w}: ${n}`).join("  ·  ");
  h += `<div class="pd-section">Rozložení do týdnů</div><p class="pd-sub">${byWeek || "—"}</p>`;
  h += `<div class="brain-rec"><strong>Doporučení mozku:</strong> ${escWi(sc.recommendation)}</div>`;
  return h;
}

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

// ---- cloud generate (GitHub Actions) --------------------------------------

let cloudPollTimer = null;

async function cloudDownload(week) {
  const res = await apiFetch(`/api/cloud/download?start_week=${week}`);
  if (!res.ok) { setResult("cloud-result", "Stažení selhalo.", "err"); return; }
  const blob = await res.blob();
  const url = URL.createObjectURL(blob);
  const a = document.createElement("a");
  a.href = url;
  a.download = `TOUR_PLAN_tydny_${week}.xlsx`;
  document.body.appendChild(a);
  a.click();
  a.remove();
  URL.revokeObjectURL(url);
}

function cloudPoll(week) {
  if (cloudPollTimer) clearTimeout(cloudPollTimer);
  const tick = async () => {
    let data;
    try {
      data = await apiJson(`/api/cloud/status?start_week=${week}`);
    } catch (e) {
      setResult("cloud-result", "Chyba stavu: " + e.message, "err");
      return;
    }
    const run = data.run;
    const link = document.getElementById("cloud-run-link");
    if (run && link) { link.href = run.html_url; link.style.display = ""; }
    if (data.ready) {
      setResult("cloud-result", "Hotovo! Plán je připravený ke stažení.", "ok");
      document.getElementById("cloud-download-row").style.display = "";
      document.getElementById("cloud-download-btn").onclick = () => cloudDownload(week);
      return;
    }
    if (run && run.status === "completed" && run.conclusion !== "success") {
      setResult("cloud-result", `Výpočet skončil chybou (${run.conclusion}). Otevři běh na GitHubu.`, "err");
      return;
    }
    const label = run ? (run.status === "queued" ? "ve frontě" : "počítá se") : "spouští se";
    setResult("cloud-result", `GitHub výpočet – ${label}… (obnovuji každých 12 s)`, "");
    cloudPollTimer = setTimeout(tick, 12000);
  };
  tick();
}

on("cloud-form", "submit", async (e) => {
  e.preventDefault();
  const week = parseInt(document.getElementById("cloud-week").value, 10);
  const length = parseInt(document.getElementById("cloud-length").value, 10) || 5;
  const visits = parseInt(document.getElementById("cloud-visits").value, 10) || 40;
  document.getElementById("cloud-download-row").style.display = "none";
  setResult("cloud-result", "Spouštím výpočet na GitHubu…", "");
  try {
    await postJson("/api/cloud/generate", { start_week: week, length, visits_per_tech: visits });
  } catch (err) {
    setResult("cloud-result", "Nepodařilo se spustit: " + err.message, "err");
    return;
  }
  // small delay so the run has time to appear, then poll
  setTimeout(() => cloudPoll(week), 4000);
});

// ---- planner (decision-support work tool) ---------------------------------

let _plannerParams = null;

async function loadPlannerModes() {
  const sel = document.getElementById("planner-mode");
  if (!sel) return;
  try {
    const d = await apiJson("/api/strategy-modes");
    sel.innerHTML = (d.modes || []).map((m) => `<option value="${esc(m.id)}">${esc(m.label)}</option>`).join("");
  } catch (e) { /* ignore */ }
}

function tile(label, value, sub, cls) {
  return `<div class="pl-tile ${cls || ""}"><div class="v">${value}</div><div class="l">${esc(label)}</div>${sub ? `<div class="s">${esc(sub)}</div>` : ""}</div>`;
}

function renderAdvise(a) {
  const ad = a.advice, cap = a.capacity, cad = a.coverage.cadence, neg = a.coverage.neglect;
  const utl = cap.utilizationPct;
  const utilCls = utl == null ? "" : (utl > 100 ? "bad" : (utl > 90 ? "warn" : "good"));
  let html = "";
  // verdict
  html += `<div class="pl-verdict ${ad.verdict.level}"><div class="dot"></div><div>` +
    `<h3>${ad.verdict.level === "good" ? "✓ Dává smysl" : ad.verdict.level === "risk" ? "✗ Rizika" : "◐ Se slabinami"}</h3>` +
    `<p class="vsum">${esc(ad.verdict.summary)}</p>` +
    `<div class="vrow"><b>Nejslabší místo:</b> ${esc(ad.weakestLink)}</div>` +
    `<div class="vrow"><b>Co brzdí růst:</b> ${esc(ad.bindingConstraint)}</div>` +
    `</div></div>`;
  // tiles
  html += `<div class="pl-tiles">`;
  html += tile("Vytížení kapacity", (utl == null ? "—" : utl + " %"),
    `${cap.plannedVisits} z ${cap.totalCapacity} návštěv`, utilCls);
  html += tile("Přetížení technici", cap.overloadedTechnicians,
    `z ${cap.technicians} · ${cap.underloadedTechnicians} nevyužito`, cap.overloadedTechnicians ? "warn" : "good");
  html += tile("Cadence GECO/CORN", (cad.pct == null ? "—" : cad.pct + " %"),
    `mimo: ${Math.max((cad.overdue || 0) - (cad.covered || 0), 0)}`, ((cad.overdue - cad.covered) > 0) ? "bad" : "good");
  html += tile("Neglect backlog", neg.backlogBefore, `dojede se ${neg.cleared}`, neg.remainingAfter ? "warn" : "good");
  html += tile("Kampaně", "zatím —", "cíle + rozsah se dodělávají", "pending");
  html += `</div>`;
  // recommendations
  if (ad.recommendations && ad.recommendations.length) {
    html += `<div class="pl-section">Co změnit</div><ul class="pl-recs">` +
      ad.recommendations.map((r) => `<li>${esc(r)}</li>`).join("") + `</ul>`;
  }
  // container for the unserved analysis (filled by loadUnserved)
  html += `<div id="planner-unserved"><p class="result">Načítám neobsloužené POS…</p></div>`;

  // technician workload + regions = supplementary, collapsed by default
  const techs = (a.perTechnician || []).slice(0, 20);
  html += `<details style="margin-top:12px"><summary style="cursor:pointer;color:var(--text-dim);font-size:13px">Doplňkové: vytížení techniků & regiony</summary><div style="margin-top:10px">`;
  if (techs.length) {
    html += `<div class="pl-section">Vytížení techniků (kapacita ${cap.visitsPerTechWeek}/týden)</div>`;
    html += techs.map((t) => {
      const w = Math.min(100, Math.round((t.utilizationPct || 0)));
      return `<div class="pl-tech ${t.status}"><span class="nm">${esc(t.technician)}</span>` +
        `<span class="pl-bar"><span style="width:${w}%"></span></span>` +
        `<span class="ut">${t.avgPerWeek}/t · ${t.utilizationPct ?? "—"} %</span></div>`;
    }).join("");
  }
  if (a.perRegion && a.perRegion.length) {
    html += `<div class="pl-section">Zátěž regionů</div><div style="font-size:13px;color:var(--text-dim)">` +
      a.perRegion.slice(0, 10).map((r) => `${esc(r.region)}: ${r.visits}`).join(" · ") + `</div>`;
  }
  html += `</div></details>`;
  return html;
}

const _UNS_LABEL = {
  capacity: "Nevešlo do kapacity",
  holdback: "Odloženo hold-backem (blíží se kampaň)",
  mingap: "Pod minimálním rozestupem (navštíveno nedávno)",
};

function renderUnserved(u) {
  let html = `<div class="pl-section">Co zůstalo neobslouženo a proč <span class="badge">kontrola výběru</span></div>`;
  html += `<p class="hint" style="margin:0 0 10px">Naplánováno ${u.served} POS. Níže důležité POS, které se nenaplánovaly – seskupené podle důvodu, který dal engine. Nejdůležitější (CORE / cadence / PPT) nahoře.</p>`;
  const ua = u.unservedActionable;
  for (const key of ["capacity", "holdback", "mingap"]) {
    const g = ua[key];
    if (!g || !g.count) continue;
    html += `<details ${key === "capacity" ? "open" : ""} style="margin-bottom:8px">` +
      `<summary style="cursor:pointer;font-size:14px;font-weight:600">${esc(_UNS_LABEL[key])}: ${g.count}` +
      ` <span style="color:var(--text-dim);font-weight:400">(CORE ${g.core}, cadence ${g.cadenceDue})</span></summary>`;
    if (g.items.length) {
      html += `<table class="pd-score-table" style="margin-top:6px"><tbody>` +
        g.items.map((it) =>
          `<tr><td><span class="pos-link" data-pos="${esc(it.pos)}">${esc(it.pos)}</span></td>` +
          `<td>${esc(it.nazev || "")}</td><td>${esc(it.kategorie || "")}</td>` +
          `<td>${it.core ? "CORE" : ""}${it.cadence ? " " + esc(it.cadence) : ""}</td>` +
          `<td style="text-align:right">${it.ppt != null ? Math.round(it.ppt).toLocaleString("cs") : ""}</td>` +
          `<td style="text-align:right">${it.weeksSinceLastVisit ?? "—"} týd.</td></tr>`).join("") +
        `</tbody></table>`;
      if (g.count > g.items.length) html += `<p class="hint">…a dalších ${g.count - g.items.length}.</p>`;
    }
    html += `</details>`;
  }
  if (u.filteredByRule && u.filteredByRule.length) {
    html += `<div class="pl-section" style="margin-top:10px">Vyřazeno pravidly (záměrně)</div>`;
    html += `<div style="font-size:13px;color:var(--text-dim)">` +
      u.filteredByRule.map((f) => `${esc(f.reason)}: ${f.count}`).join(" · ") + `</div>`;
  }
  // wire POS links
  setTimeout(() => document.querySelectorAll("#planner-unserved .pos-link").forEach((el) =>
    el.addEventListener("click", () => openPosDetail && openPosDetail(el.dataset.pos))), 0);
  return html;
}

async function loadUnserved(p) {
  const el = document.getElementById("planner-unserved");
  if (!el) return;
  try {
    const u = await postJson("/api/planner/unserved", p);
    el.innerHTML = renderUnserved(u);
  } catch (e) {
    el.innerHTML = `<p class="result err">Neobsloužené POS se nepodařilo načíst: ${esc(e.message)}</p>`;
  }
}

function renderCompare(base, scen) {
  const row = (label, b, s) => {
    const d = (typeof b === "number" && typeof s === "number") ? s - b : null;
    const dcls = d == null || d === 0 ? "" : (d > 0 ? "pos" : "neg");
    return `<div class="vrow"><b>${esc(label)}:</b> ${b} → ${s}` +
      (d != null && d !== 0 ? ` <span class="pl-delta ${dcls}">(${d > 0 ? "+" : ""}${d})</span>` : "") + `</div>`;
  };
  return `<div class="pl-compare">` +
    `<div class="col"><h4>Aktuální</h4>${verdictMini(base)}</div>` +
    `<div class="arrow">→</div>` +
    `<div class="col"><h4>Co kdyby</h4>${verdictMini(scen)}</div></div>` +
    `<div style="margin-top:10px">` +
    row("Vytížení %", base.capacity.utilizationPct, scen.capacity.utilizationPct) +
    row("Přetížení techniků", base.capacity.overloadedTechnicians, scen.capacity.overloadedTechnicians) +
    row("Naplánováno návštěv", base.capacity.plannedVisits, scen.capacity.plannedVisits) +
    `</div>`;
}
function verdictMini(a) {
  const v = a.advice.verdict;
  return `<span class="badge" style="background:${v.level === "good" ? "var(--good)" : v.level === "risk" ? "#B3453A" : "var(--warn)"}">${v.level}</span> ${esc(a.advice.weakestLink)}`;
}

on("planner-form", "submit", async (e) => {
  e.preventDefault();
  const p = {
    mode: document.getElementById("planner-mode").value,
    start_week: parseInt(document.getElementById("planner-week").value, 10),
    length: parseInt(document.getElementById("planner-length").value, 10) || 5,
    visits_per_tech_week: parseFloat(document.getElementById("planner-visits").value) || null,
    tech_count: parseInt(document.getElementById("planner-techs").value, 10) || null,
  };
  if (!p.start_week) { setResult("planner-result", "Zadej počáteční týden.", "err"); return; }
  setResult("planner-result", "Počítám scénář (běží engine)…", "");
  try {
    const a = await postJson("/api/planner/advise", p);
    _plannerParams = p; window._plannerBase = a;
    document.getElementById("planner-out").innerHTML = renderAdvise(a);
    setResult("planner-result", "", "ok");
    document.getElementById("planner-whatif-wrap").classList.remove("hidden");
    document.getElementById("whatif-visits").value = p.visits_per_tech_week || 40;
    loadUnserved(p);   // the control surface: what stays unserved and why
  } catch (err) {
    setResult("planner-result", "Chyba: " + err.message, "err");
  }
});

on("planner-whatif-form", "submit", async (e) => {
  e.preventDefault();
  if (!_plannerParams) return;
  const scen = { ..._plannerParams,
    visits_per_tech_week: parseFloat(document.getElementById("whatif-visits").value) || _plannerParams.visits_per_tech_week,
    tech_count: parseInt(document.getElementById("whatif-techs").value, 10) || _plannerParams.tech_count,
  };
  document.getElementById("planner-compare").innerHTML = "<p class='result'>Počítám…</p>";
  try {
    const s = await postJson("/api/planner/advise", scen);
    document.getElementById("planner-compare").innerHTML = renderCompare(window._plannerBase, s);
  } catch (err) {
    document.getElementById("planner-compare").innerHTML = `<p class="result err">Chyba: ${esc(err.message)}</p>`;
  }
});

// ---- actual route map -----------------------------------------------------

let _ractMap = null, _ractLayer = null;

async function loadRactTechnicians() {
  const sel = document.getElementById("ract-tech");
  if (!sel) return;
  try {
    const list = (await apiJson("/api/technicians")).technicians.filter((t) => t.role === "TECHNIK" && t.active);
    sel.innerHTML = list.map((t) => `<option>${esc(t.name)}</option>`).join("");
    if (list.length) loadRactDays();
  } catch (e) { /* ignore */ }
}

async function loadRactDays() {
  const tech = document.getElementById("ract-tech").value;
  const sel = document.getElementById("ract-day");
  if (!tech) return;
  try {
    const days = (await apiJson(`/api/route/days?technician=${encodeURIComponent(tech)}`)).days;
    sel.innerHTML = days.map((d) => `<option>${d}</option>`).join("") || `<option value="">žádné</option>`;
  } catch (e) { sel.innerHTML = `<option value="">—</option>`; }
}

function renderRouteMap(day) {
  const mapDiv = document.getElementById("ract-map");
  mapDiv.style.display = "block";
  if (typeof L === "undefined") { mapDiv.style.display = "none"; return false; }
  if (!_ractMap) {
    _ractMap = L.map("ract-map");
    L.tileLayer("https://{s}.tile.openstreetmap.org/{z}/{x}/{y}.png",
      { maxZoom: 19, attribution: "© OpenStreetMap" }).addTo(_ractMap);
  }
  if (_ractLayer) _ractLayer.remove();
  _ractLayer = L.layerGroup().addTo(_ractMap);
  const pts = [];
  for (const s of day.stops) {
    if (s.lat == null || s.lon == null) continue;
    pts.push([s.lat, s.lon]);
    L.marker([s.lat, s.lon]).addTo(_ractLayer)
      .bindPopup(`#${s.seq} ${esc(s.name || s.pos || "")}<br>${esc(s.city || "")}<br>${s.started ? s.started.slice(-8) : ""} · ${s.onPosMin ?? "—"} min na POS`)
      .bindTooltip(String(s.seq), { permanent: true, direction: "center", className: "ract-num" });
  }
  if (pts.length) {
    L.polyline(pts, { color: "#12807C", weight: 3, opacity: 0.7 }).addTo(_ractLayer);
    _ractMap.fitBounds(pts, { padding: [30, 30] });
  }
  setTimeout(() => _ractMap.invalidateSize(), 50);
  return true;
}

on("ract-tech", "change", loadRactDays);

on("route-actual-form", "submit", async (e) => {
  e.preventDefault();
  const tech = document.getElementById("ract-tech").value;
  const day = document.getElementById("ract-day").value;
  if (!tech || !day) { setResult("ract-result", "Vyber technika a den.", "err"); return; }
  setResult("ract-result", "Načítám trasu…", "");
  try {
    const r = await apiJson(`/api/route/actual?technician=${encodeURIComponent(tech)}&date_from=${day}&date_to=${day}`);
    const d = r.days[0];
    if (!d) { setResult("ract-result", "Pro tento den nejsou data.", "err"); return; }
    document.getElementById("ract-totals").innerHTML = `<div class="pl-tiles">` +
      tile("Zastávek", d.stopCount, "návštěv") +
      tile("Najeto km", d.totalKm, "informativně") +
      tile("Čas na cestě", Math.round(d.travelMin) + " min", "mezi POS") +
      tile("Čas na POS", Math.round(d.onPosMin) + " min", "celkem") +
      tile("Odpracováno", d.workHours != null ? d.workHours + " h" : "—",
           d.workStart && d.workEnd ? `${d.workStart}–${d.workEnd}` : "z časů návštěv") + `</div>`;
    const hasMap = renderRouteMap(d);
    let legsHtml = `<div class="pl-section">Zastávky a přejezdy</div><table class="pd-score-table"><tbody>`;
    d.stops.forEach((s, i) => {
      legsHtml += `<tr><td>#${s.seq}</td><td><span class="pos-link" data-pos="${esc(s.pos || "")}">${esc(s.name || s.pos || "")}</span></td>` +
        `<td>${esc(s.city || "")}</td><td>${s.started ? s.started.slice(-8) : ""}</td>` +
        `<td style="text-align:right">${s.onPosMin ?? "—"} min</td></tr>`;
      const leg = d.legs.find((l) => l.fromSeq === s.seq);
      if (leg) legsHtml += `<tr><td></td><td colspan="4" style="color:var(--text-dim)">↓ ${leg.km ?? "—"} km · ${leg.travelMin ?? "—"} min jízdy</td></tr>`;
    });
    legsHtml += `</tbody></table>`;
    if (!hasMap) legsHtml = `<p class="hint">Mapa se nenačetla (offline?). Trasa je níže.</p>` + legsHtml;
    document.getElementById("ract-legs").innerHTML = legsHtml;
    setResult("ract-result", "", "ok");
    document.querySelectorAll("#ract-legs .pos-link").forEach((el) =>
      el.addEventListener("click", () => el.dataset.pos && openPosDetail(el.dataset.pos)));
  } catch (err) { setResult("ract-result", "Chyba: " + err.message, "err"); }
});

// ---- technician configuration ---------------------------------------------

const _ROLES = ["TECHNIK", "OZ", "ADMIN", "MANAGER"];

async function loadTechnicians() {
  const el = document.getElementById("tech-out");
  if (!el) return;
  const filter = document.getElementById("tech-filter").value;
  try {
    let list = (await apiJson("/api/technicians")).technicians;
    if (filter) list = list.filter((t) => t.role === filter);
    el.innerHTML = `<table class="pd-score-table"><thead><tr><th>Jméno</th><th>Role</th><th>Aktivní</th></tr></thead><tbody>` +
      list.map((t) =>
        `<tr><td>${esc(t.name)}${t.manual_role ? " ✎" : ""}</td>` +
        `<td><select data-tech="${esc(t.name)}" class="tech-role">` +
        _ROLES.map((r) => `<option ${r === t.role ? "selected" : ""}>${r}</option>`).join("") + `</select></td>` +
        `<td><input type="checkbox" class="tech-active" data-tech="${esc(t.name)}" ${t.active ? "checked" : ""}></td></tr>`).join("") +
      `</tbody></table><p class="hint">${list.length} osob. ✎ = ručně nastaveno.</p>`;
    el.querySelectorAll(".tech-role").forEach((s) => s.addEventListener("change", async () => {
      await postJsonPut(`/api/technicians/${encodeURIComponent(s.dataset.tech)}`, { role: s.value });
      loadAlerts();
    }));
    el.querySelectorAll(".tech-active").forEach((cb) => cb.addEventListener("change", async () => {
      await postJsonPut(`/api/technicians/${encodeURIComponent(cb.dataset.tech)}`, { active: cb.checked });
      loadAlerts();
    }));
  } catch (e) { el.innerHTML = `<p class="result err">${esc(e.message)}</p>`; }
}

function postJsonPut(path, body) {
  return apiJson(path, { method: "PUT", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body) });
}

on("tech-refresh", "click", loadTechnicians);
on("tech-filter", "change", loadTechnicians);

// ---- automatic import + alerts --------------------------------------------

const _TYPE_LABEL = { workbook: "kompletní workbook", salesapp: "SalesApp", pos_master: "POS Master", activity_plan: "Activity Plan", unknown: "neznámý" };

on("auto-import-form", "submit", async (e) => {
  e.preventDefault();
  const f = document.getElementById("auto-file").files[0];
  if (!f) { setResult("auto-import-result", "Vyber soubor.", "err"); return; }
  setResult("auto-import-result", "Zpracovávám…", "");
  try {
    const fd = new FormData(); fd.append("file", f);
    const res = await apiFetch("/api/import/auto", { method: "POST", body: fd });
    const r = await res.json();
    if (r.detected === "unknown") { setResult("auto-import-result", r.error || "Nerozpoznaný typ.", "err"); return; }
    const counts = Object.entries(r.counts || {}).map(([k, v]) => `${k}: ${v}`).join(", ");
    setResult("auto-import-result", `Rozpoznáno: ${_TYPE_LABEL[r.detected] || r.detected}. ${counts}.`, "ok");
    loadAlerts(); loadStatus && loadStatus();
  } catch (err) { setResult("auto-import-result", "Chyba: " + err.message, "err"); }
});

function renderAlerts(list) {
  if (!list.length) return `<p class="pd-sub">Žádná upozornění. 👍</p>`;
  const color = (s) => s === "warn" ? "#B3453A" : s === "info" ? "var(--warn)" : "var(--text-dim)";
  return list.map((a) => {
    const p = a.payload || {};
    return `<div class="pl-tech"><span class="dot" style="width:10px;height:10px;border-radius:50%;background:${color(p.severity)};flex:none"></span>` +
      `<span style="flex:1">${esc(p.message || "")}</span></div>`;
  }).join("");
}

async function loadAlerts() {
  const el = document.getElementById("alerts-out");
  if (!el) return;
  try {
    const list = (await apiJson("/api/alerts")).alerts;
    document.getElementById("alerts-count").textContent = list.length;
    el.innerHTML = renderAlerts(list);
  } catch (e) { /* ignore */ }
}

on("alerts-refresh", "click", async () => {
  const el = document.getElementById("alerts-out");
  el.innerHTML = "<p class='result'>Přepočítávám…</p>";
  try { await postJson("/api/alerts/recompute", {}); loadAlerts(); }
  catch (e) { el.innerHTML = `<p class="result err">${esc(e.message)}</p>`; }
});

// ---- plan vs reality (SalesApp) -------------------------------------------

function renderReality(r) {
  if (!r.technicians.length) return `<p class="pd-sub">V tomto rozsahu nejsou žádné návštěvy techniků.</p>`;
  const mins = r.technicians.map((t) => t.avgOnPosMinutes).filter((x) => x != null).sort((a, b) => a - b);
  const median = mins.length ? mins[Math.floor(mins.length / 2)] : null;
  let html = `<p class="pd-sub">${r.technicianCount} techniků, týdny ${r.weekFrom ?? "–"}–${r.weekTo ?? "–"}.</p>`;
  html += `<table class="pd-score-table"><thead><tr><th>Technik</th><th>Návštěv</th><th>POS</th><th>Dní</th><th>/den</th><th>min/POS</th><th>hod/den</th></tr></thead><tbody>`;
  html += r.technicians.map((t) => {
    // flag unusually long/short on-POS time vs median (>1.6x or <0.5x)
    let cls = "";
    if (median && t.avgOnPosMinutes != null) {
      if (t.avgOnPosMinutes > median * 1.6) cls = "wi-impact neg";
      else if (t.avgOnPosMinutes < median * 0.5) cls = "wi-impact";
    }
    return `<tr><td>${esc(t.technician)}</td><td>${t.visits}</td><td>${t.uniquePos}</td>` +
      `<td>${t.daysWorked}</td><td>${t.avgVisitsPerDay}</td>` +
      `<td class="${cls}" style="text-align:right">${t.avgOnPosMinutes ?? "—"}</td>` +
      `<td style="text-align:right">${t.avgHoursPerDay != null ? t.avgHoursPerDay + " h" : "—"}</td></tr>`;
  }).join("");
  html += `</tbody></table><p class="hint">Medián času na POS ≈ ${median ?? "—"} min. „hod/den“ = odhad odpracovaných hodin denně (první příjezd → poslední odjezd, včetně přejezdů). Výrazně vyšší hodnoty (červeně) stojí za pozornost.</p>`;
  return html;
}

function renderFulfillment(f) {
  let html = `<div class="pl-tiles">`;
  html += tile("Naplánováno", f.planned, `týdny ${f.weekFrom}–${f.weekTo}`);
  html += tile("Splněno", f.done + f.doneShifted, `${f.fulfilmentPct ?? "—"} %`, (f.fulfilmentPct >= 80 ? "good" : "warn"));
  html += tile("Neobslouženo", f.missed, "z plánu", f.missed ? "bad" : "good");
  html += tile("Mimořádné návštěvy", f.extraVisits, "mimo plán");
  html += tile("Jiný technik", f.wrongTechnician, "než plánováno", f.wrongTechnician ? "warn" : "");
  html += `</div>`;
  if (f.note) html += `<p class="hint">${esc(f.note)}</p>`;
  if (f.perTechnician && f.perTechnician.length && (f.done + f.doneShifted + f.missed)) {
    html += `<table class="pd-score-table"><thead><tr><th>Technik</th><th>Plán</th><th>Splněno</th><th>Neobsl.</th><th>%</th></tr></thead><tbody>` +
      f.perTechnician.map((t) => `<tr><td>${esc(t.technician)}</td><td>${t.planned}</td>` +
        `<td>${t.done + t.doneShifted}</td><td>${t.missed}</td><td>${t.fulfilmentPct} %</td></tr>`).join("") +
      `</tbody></table>`;
  }
  return html;
}

on("reality-form", "submit", async (e) => {
  e.preventDefault();
  const wf = document.getElementById("reality-wf").value;
  const wt = document.getElementById("reality-wt").value;
  setResult("reality-result", "Načítám realitu…", "");
  try {
    const q = new URLSearchParams();
    if (wf) q.set("week_from", wf); if (wt) q.set("week_to", wt);
    const r = await apiJson("/api/reality/technicians?" + q.toString());
    document.getElementById("reality-out").innerHTML = renderReality(r);
    setResult("reality-result", "", "ok");
  } catch (err) { setResult("reality-result", "Chyba: " + err.message, "err"); }
});

on("fulfil-btn", "click", async () => {
  const wf = document.getElementById("reality-wf").value;
  const wt = document.getElementById("reality-wt").value;
  if (!wf || !wt) { setResult("reality-result", "Zadej rozsah týdnů.", "err"); return; }
  setResult("reality-result", "Porovnávám plán s realitou…", "");
  try {
    const f = await apiJson(`/api/reality/fulfillment?week_from=${wf}&week_to=${wt}`);
    document.getElementById("reality-out").innerHTML = renderFulfillment(f);
    setResult("reality-result", "", "ok");
  } catch (err) { setResult("reality-result", "Chyba: " + err.message, "err"); }
});

// ---- predictions: capacity sweep ------------------------------------------

function renderSweep(s) {
  let html = `<p class="pd-sub">Servisovatelná síť: <strong>${s.servableNetwork}</strong> POS (projdou pravidly).</p>`;
  html += `<table class="pd-score-table"><thead><tr>` +
    `<th>Kapacita/týd</th><th>Obslouženo POS</th><th>% sítě</th><th>Unikát/týd</th><th>Pokrytí sítě</th></tr></thead><tbody>` +
    s.capacities.map((r) =>
      `<tr><td><strong>${r.capacityPerTechWeek}</strong></td>` +
      `<td>${r.uniquePosServed}</td>` +
      `<td>${r.coveragePctOfNetwork ?? "—"} %</td>` +
      `<td>${r.uniquePerWeek ?? "—"}</td>` +
      `<td>~${r.estWeeksToCoverNetwork ?? "—"} týd.</td></tr>`).join("") +
    `</tbody></table>`;
  html += `<p class="hint">${esc(s.assumptions)}</p>`;
  return html;
}

on("sweep-form", "submit", async (e) => {
  e.preventDefault();
  const caps = (document.getElementById("sweep-caps").value || "")
    .split(/[\s,;]+/).map((x) => parseInt(x, 10)).filter((x) => x > 0);
  if (!caps.length) { setResult("sweep-result", "Zadej aspoň jednu kapacitu.", "err"); return; }
  const week = parseInt(document.getElementById("planner-week").value, 10);
  if (!week) { setResult("sweep-result", "Nahoře zadej počáteční týden.", "err"); return; }
  const p = {
    mode: document.getElementById("planner-mode").value,
    start_week: week,
    length: parseInt(document.getElementById("planner-length").value, 10) || 5,
    capacities: caps,
    tech_count: parseInt(document.getElementById("planner-techs").value, 10) || null,
  };
  setResult("sweep-result", `Počítám predikci pro ${caps.length} kapacit… (~${caps.length * 25 + 20} s)`, "");
  try {
    const s = await postJson("/api/planner/sweep", p);
    document.getElementById("sweep-out").innerHTML = renderSweep(s);
    setResult("sweep-result", "", "ok");
  } catch (err) {
    setResult("sweep-result", "Chyba: " + err.message, "err");
  }
});

// ---- hard-exclude POS from planning ---------------------------------------

async function loadExclusionCount() {
  const el = document.getElementById("excl-count");
  if (!el) return;
  try { el.textContent = (await apiJson("/api/exclusions")).count; } catch (e) { /* ignore */ }
}

on("excl-form", "submit", async (e) => {
  e.preventDefault();
  const ids = document.getElementById("excl-ids").value.trim();
  if (!ids) return;
  try {
    const r = await postJson("/api/exclusions", { pos_ids: ids, reason: "manuálně" });
    setResult("excl-result", `Vyřazeno ${r.added} POS. Celkem vyřazeno: ${r.count}.`, "ok");
    document.getElementById("excl-ids").value = "";
    document.getElementById("excl-count").textContent = r.count;
  } catch (err) { setResult("excl-result", "Chyba: " + err.message, "err"); }
});

on("excl-clear", "click", async () => {
  if (!confirm("Zrušit všechna vyřazení POS?")) return;
  try {
    const r = await apiFetch("/api/exclusions/_all", { method: "DELETE" });
    const d = await r.json();
    setResult("excl-result", "Vyřazení zrušena.", "ok");
    document.getElementById("excl-count").textContent = d.count;
  } catch (err) { setResult("excl-result", "Chyba: " + err.message, "err"); }
});

// ---- OZ campaign prep priority list (FORCE_INCLUDE) -----------------------

async function loadPriority() {
  const el = document.getElementById("prio-list");
  const cnt = document.getElementById("prio-count");
  if (!el) return;
  try {
    const r = await apiJson("/api/priority");
    if (cnt) cnt.textContent = r.count;
    if (!r.priority.length) { el.innerHTML = ""; return; }
    el.innerHTML = `<table class="pd-score-table"><thead><tr><th>POS</th><th>Kampaň</th><th></th></tr></thead><tbody>` +
      r.priority.map((p) =>
        `<tr><td>${esc(p.pos_id)}</td><td>${esc(p.campaign || "—")}</td>` +
        `<td style="text-align:right"><button class="ghost prio-del" data-pos="${esc(p.pos_id)}">✕</button></td></tr>`).join("") +
      `</tbody></table>`;
    el.querySelectorAll(".prio-del").forEach((b) => b.addEventListener("click", async () => {
      await apiFetch(`/api/priority/${encodeURIComponent(b.dataset.pos)}`, { method: "DELETE" });
      loadPriority();
    }));
  } catch (e) { /* ignore */ }
}

on("prio-form", "submit", async (e) => {
  e.preventDefault();
  const ids = document.getElementById("prio-ids").value.trim();
  if (!ids) return;
  const campaign = document.getElementById("prio-campaign").value.trim();
  try {
    const r = await postJson("/api/priority", { pos_ids: ids, campaign });
    setResult("prio-result", `Přichystáno ${r.added} POS. Celkem prioritních: ${r.count}.`, "ok");
    document.getElementById("prio-ids").value = "";
    loadPriority();
  } catch (err) { setResult("prio-result", "Chyba: " + err.message, "err"); }
});

on("prio-clear", "click", async () => {
  if (!confirm("Zrušit celý seznam prioritních POS?")) return;
  try {
    await apiFetch("/api/priority/_all", { method: "DELETE" });
    setResult("prio-result", "Seznam zrušen.", "ok");
    loadPriority();
  } catch (err) { setResult("prio-result", "Chyba: " + err.message, "err"); }
});

// ---- temporary POS reassignment (vacation / sickness cover) ---------------

async function loadReassignments() {
  const el = document.getElementById("reassign-list");
  const cnt = document.getElementById("reassign-count");
  if (!el) return;
  try {
    const r = await apiJson("/api/reassignments");
    if (cnt) cnt.textContent = r.count;
    if (!r.reassignments.length) { el.innerHTML = ""; return; }
    el.innerHTML = `<table class="pd-score-table"><thead><tr><th>Co</th><th>→ Na koho</th><th>Důvod</th><th>Platnost</th><th></th></tr></thead><tbody>` +
      r.reassignments.map((x) => {
        const what = x.pos_id ? `POS ${esc(x.pos_id)}` : `vše: ${esc(x.from_technician || "?")}`;
        const span = (x.valid_from || x.valid_to) ? `${x.valid_from || "…"} – ${x.valid_to || "…"}` : "trvale";
        const badge = x.current ? "" : ` <span class="badge">neaktivní</span>`;
        return `<tr><td>${what}</td><td>${esc(x.to_technician)}</td><td>${esc(x.reason || "—")}</td>` +
          `<td>${span}${badge}</td>` +
          `<td style="text-align:right"><button class="ghost reassign-del" data-id="${x.id}">✕</button></td></tr>`;
      }).join("") + `</tbody></table>`;
    el.querySelectorAll(".reassign-del").forEach((b) => b.addEventListener("click", async () => {
      await apiFetch(`/api/reassignments/${b.dataset.id}`, { method: "DELETE" });
      loadReassignments();
    }));
  } catch (e) { /* ignore */ }
}

on("reassign-form", "submit", async (e) => {
  e.preventDefault();
  const to = document.getElementById("reassign-to").value.trim();
  if (!to) { setResult("reassign-result", "Zadej, na koho POS přehodit.", "err"); return; }
  const body = {
    from_technician: document.getElementById("reassign-from").value.trim() || null,
    pos_ids: document.getElementById("reassign-pos").value.trim(),
    to_technician: to,
    reason: document.getElementById("reassign-reason").value,
    valid_from: document.getElementById("reassign-vf").value || null,
    valid_to: document.getElementById("reassign-vt").value || null,
  };
  if (!body.from_technician && !body.pos_ids) {
    setResult("reassign-result", "Vyplň „Od koho“ nebo „čísla POS“.", "err"); return;
  }
  try {
    const r = await postJson("/api/reassignments", body);
    if (!r.ok) { setResult("reassign-result", r.error || "Chyba.", "err"); return; }
    setResult("reassign-result", `Přehozeno (${r.added}). Projeví se při dalším generování plánu.`, "ok");
    document.getElementById("reassign-pos").value = "";
    document.getElementById("reassign-from").value = "";
    loadReassignments();
  } catch (err) { setResult("reassign-result", "Chyba: " + err.message, "err"); }
});

// ---- live published TourPlan (main working screen) ------------------------

const _LIVE_STATUS = {
  done: { label: "Hotovo", cls: "st-done" },
  due: { label: "Tento týden", cls: "st-due" },
  overdue: { label: "Zpožděno", cls: "st-overdue" },
  upcoming: { label: "Naplánováno", cls: "st-upcoming" },
};

function _cd(days) {
  if (days == null) return "—";
  if (days < 0) return `${-days} dní po termínu`;
  if (days === 0) return "dnes";
  return `za ${days} dní`;
}

function renderBoard(b) {
  if (!b.published) return `<p class="pd-sub">${esc(b.message || "Zatím nebyl publikován žádný plán.")}</p>`;
  const r = b.rollup;
  let html = `<div class="pl-tiles">` +
    tile("Verze", b.version || "—", `${b.versionCount} publikováno`) +
    tile("Týdny plánu", b.weekRange || "—", `teď je týden ${b.currentWeek}`) +
    tile("Zastávek", r.total, "v publikovaném plánu") +
    tile("Splněno", r.done, `${r.fulfilmentPct ?? 0} %`, r.fulfilmentPct >= 80 ? "good" : "") +
    tile("Zpožděno", r.overdue, "z plánu", r.overdue ? "bad" : "good") +
    tile("Naplánováno", r.upcoming, "před termínem") + `</div>`;
  // per-week strip
  if (b.perWeek && b.perWeek.length) {
    html += `<div class="pl-section">Podle týdnů</div><table class="pd-score-table"><thead><tr><th>Týden</th><th>Zastávek</th><th>Hotovo</th><th>Zpožděno</th></tr></thead><tbody>` +
      b.perWeek.map((w) => `<tr><td><strong>${w.week}</strong></td><td>${w.stops}</td>` +
        `<td>${w.done}</td><td>${w.overdue || "—"}</td></tr>`).join("") + `</tbody></table>`;
  }
  // upcoming stops (limit for readability), most imminent first
  const stops = b.stops.filter((s) => s.status !== "done")
    .sort((a, z) => (a.daysUntil ?? 9999) - (z.daysUntil ?? 9999)).slice(0, 60);
  if (stops.length) {
    html += `<div class="pl-section">Nadcházející zastávky</div><table class="pd-score-table"><thead><tr>` +
      `<th>Týd</th><th>Datum</th><th>Technik</th><th>POS</th><th>Stav</th><th>Odpočet</th></tr></thead><tbody>` +
      stops.map((s) => {
        const st = _LIVE_STATUS[s.status] || { label: s.status, cls: "" };
        return `<tr><td>${s.week ?? "—"}</td><td>${esc(s.planDate || "—")}</td>` +
          `<td>${esc(s.technician || "—")}</td>` +
          `<td><span class="pos-link" data-pos="${esc(s.pos || "")}">${esc(s.name || s.pos || "")}</span></td>` +
          `<td><span class="chip ${st.cls}">${st.label}</span></td>` +
          `<td>${_cd(s.daysUntil)}</td></tr>`;
      }).join("") + `</tbody></table>`;
    if (b.stops.filter((s) => s.status !== "done").length > 60)
      html += `<p class="hint">Zobrazeno prvních 60 nejbližších zastávek.</p>`;
  }
  return html;
}

function renderDue(d) {
  const c = d.counts;
  document.getElementById("due-count").textContent = d.total;
  let html = `<div class="pl-tiles">` +
    tile("Po termínu", c.overdue, "mimo cadence", c.overdue ? "bad" : "good") +
    tile("Brzy splatné", c.dueSoon, "do 14 dní", c.dueSoon ? "warn" : "") +
    tile("V pořádku", c.ok, "v cadence", "good") +
    tile("Nikdy nenavštíveno", c.neverVisited, "priorita", c.neverVisited ? "warn" : "") + `</div>`;
  if (!d.posList.length) return html + `<p class="pd-sub">Žádné POS v tomto stavu.</p>`;
  html += `<table class="pd-score-table"><thead><tr><th>POS</th><th>Cadence</th><th>Poslední návštěva</th><th>Splatnost</th><th>Odpočet</th></tr></thead><tbody>` +
    d.posList.map((p) => `<tr><td><span class="pos-link" data-pos="${esc(p.pos)}">${esc(p.name || p.pos)}</span></td>` +
      `<td>${esc(p.cadence)} (${p.cadenceWeeks} t.)</td><td>${esc(p.lastVisit || "nikdy")}</td>` +
      `<td>${esc(p.nextDue || "—")}</td><td>${_cd(p.daysRemaining)}</td></tr>`).join("") +
    `</tbody></table><p class="hint">Zobrazeno ${d.shown} z ${d.total}.</p>`;
  return html;
}

function _bindPosLinks(scope) {
  document.querySelectorAll(`${scope} .pos-link`).forEach((el) =>
    el.addEventListener("click", () => el.dataset.pos && openPosDetail(el.dataset.pos)));
}

async function loadLive() {
  const el = document.getElementById("live-board");
  if (!el) return;
  const tech = document.getElementById("live-tech").value;
  try {
    const b = await apiJson("/api/live/board" + (tech ? `?technician=${encodeURIComponent(tech)}` : ""));
    document.getElementById("live-version").textContent = b.published ? (b.version || "publikováno") : "nepublikováno";
    el.innerHTML = renderBoard(b);
    _bindPosLinks("#live-board");
  } catch (e) { el.innerHTML = `<p class="result err">${esc(e.message)}</p>`; }
  loadDue();
}

async function loadDue() {
  const el = document.getElementById("due-out");
  if (!el) return;
  const tech = document.getElementById("live-tech").value;
  const status = document.getElementById("due-status").value;
  try {
    const q = new URLSearchParams();
    if (tech) q.set("technician", tech);
    if (status) q.set("status", status);
    const d = await apiJson("/api/live/next-due?" + q.toString());
    el.innerHTML = renderDue(d);
    _bindPosLinks("#due-out");
  } catch (e) { el.innerHTML = `<p class="result err">${esc(e.message)}</p>`; }
}

async function loadLiveTechnicians() {
  const sel = document.getElementById("live-tech");
  if (!sel) return;
  try {
    const list = (await apiJson("/api/technicians")).technicians
      .filter((t) => t.role === "TECHNIK" && t.active);
    sel.innerHTML = `<option value="">všichni</option>` +
      list.map((t) => `<option>${esc(t.name)}</option>`).join("");
  } catch (e) { /* ignore */ }
}

on("live-refresh", "click", loadLive);
on("live-tech", "change", loadLive);
on("due-status", "change", loadDue);

// ---- route planner (long-term per-technician plan) ------------------------

async function loadRouteTechnicians() {
  const sel = document.getElementById("route-technician");
  if (!sel) return;
  try {
    const data = await apiJson("/api/planner/technicians");
    const techs = data.technicians || [];
    sel.innerHTML = techs.length
      ? techs.map((t) => `<option value="${esc(t.technician)}">${esc(t.technician)} (${t.visits}, t${t.wk_from}–${t.wk_to})</option>`).join("")
      : `<option value="">— nejdřív vygeneruj plán —</option>`;
  } catch (e) {
    sel.innerHTML = `<option value="">—</option>`;
  }
}

function renderRoute(r) {
  const DAYS = { MON: "Po", TUE: "Út", WED: "St", THU: "Čt", FRI: "Pá" };
  if (!r.weeks || !r.weeks.length) return `<p class="pd-sub">Pro tohoto technika není v plánu žádná návštěva.</p>`;
  let html = `<p class="pd-sub">Celkem <strong>${r.totalVisits}</strong> návštěv v ${r.weeks.length} týdnech.</p>`;
  for (const w of r.weeks) {
    html += `<div class="pd-section">Týden ${w.week} — ${w.visits} návštěv · ~${w.supportive_km} km (info)</div>`;
    for (const d of w.days) {
      html += `<div style="margin:6px 0 2px"><strong>${DAYS[d.day] || d.day}</strong> ${d.date ? "· " + esc(d.date) : ""} — ${d.count} POS · ~${d.supportive_km} km</div>`;
      html += `<table class="pd-score-table"><tbody>` +
        d.stops.map((s) =>
          `<tr><td><span class="pos-link" data-pos="${esc(s.pos_id)}">${esc(s.pos_id)}</span></td>` +
          `<td>${esc(s.name || "")}</td><td>${esc(s.city || "")}</td>` +
          `<td>${esc(s.category || "")}</td><td>${esc(s.reason || "")}</td></tr>`).join("") +
        `</tbody></table>`;
    }
  }
  return html;
}

on("route-form", "submit", async (e) => {
  e.preventDefault();
  const tech = document.getElementById("route-technician").value;
  if (!tech) { setResult("route-result", "Vyber technika (nejdřív vygeneruj plán).", "err"); return; }
  const wf = document.getElementById("route-week-from").value;
  const wt = document.getElementById("route-week-to").value;
  setResult("route-result", "Načítám plán…", "");
  try {
    const q = new URLSearchParams({ technician: tech });
    if (wf) q.set("week_from", wf);
    if (wt) q.set("week_to", wt);
    const r = await apiJson("/api/planner/route?" + q.toString());
    document.getElementById("route-body").innerHTML = renderRoute(r);
    setResult("route-result", "", "ok");
    // POS links open the existing POS detail panel
    document.querySelectorAll("#route-body .pos-link").forEach((el) => {
      el.addEventListener("click", () => openPosDetail && openPosDetail(el.dataset.pos));
    });
  } catch (err) {
    setResult("route-result", "Chyba: " + err.message, "err");
  }
});

// ---- boot -----------------------------------------------------------------

async function boot() {
  // Local desktop app (served by the bundled FastAPI): no login needed.
  if (window.FFO_LOCAL) {
    try {
      const res = await fetch(API_BASE + "/api/login", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ password: "local" }),
      });
      const data = await res.json();
      if (data.token) setToken(data.token);
    } catch (e) { /* fall through to login screen */ }
    if (getToken()) { showApp(); return; }
  }
  if (getToken()) showApp();
  else showLogin();
}
boot();
