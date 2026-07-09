// Field Force Optimizer - Fáze 0 cockpit. Talks to the backend API only via
// fetch(); holds no business logic of its own - every action is a thin
// wrapper around one backend endpoint, which itself just runs the existing
// desktop_client/engines/ Python engines.

const API_BASE = window.FFO_API_BASE || "http://localhost:8000";

const loginScreen = document.getElementById("login-screen");
const appScreen = document.getElementById("app-screen");
const loginForm = document.getElementById("login-form");
const loginError = document.getElementById("login-error");

function getToken() {
  return localStorage.getItem("ffo_token");
}
function setToken(t) {
  localStorage.setItem("ffo_token", t);
}
function clearToken() {
  localStorage.removeItem("ffo_token");
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

function showApp() {
  loginScreen.classList.add("hidden");
  appScreen.classList.remove("hidden");
  loadStatus();
  loadRules();
}
function showLogin() {
  appScreen.classList.add("hidden");
  loginScreen.classList.remove("hidden");
}

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
      const body = await res.json().catch(() => ({}));
      throw new Error(body.detail || "Přihlášení se nezdařilo.");
    }
    const { token } = await res.json();
    setToken(token);
    showApp();
  } catch (err) {
    loginError.textContent = err.message;
  }
});

document.getElementById("logout").addEventListener("click", () => {
  clearToken();
  showLogin();
});

async function loadStatus() {
  try {
    const res = await apiFetch("/api/status");
    const data = await res.json();
    document.getElementById("last-published").textContent =
      data.lastPublishedWeek != null ? "týden " + data.lastPublishedWeek : "žádný";
    document.getElementById("draft-state").textContent = data.hasDraft
      ? "týdny " + data.draftWeeks.join(", ")
      : "žádný";
    if (data.lastPublishedWeek) {
      document.getElementById("start-week").value = data.lastPublishedWeek + 1;
    }
  } catch (err) {
    console.error(err);
  }
}

document.getElementById("generate-form").addEventListener("submit", async (e) => {
  e.preventDefault();
  const startWeek = parseInt(document.getElementById("start-week").value, 10);
  const length = parseInt(document.getElementById("length").value, 10);
  const btn = document.getElementById("generate-btn");
  const result = document.getElementById("generate-result");
  btn.disabled = true;
  result.textContent = "Generuji tour plán…";
  try {
    const res = await apiFetch("/api/generate-plan", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ start_week: startWeek, length }),
    });
    if (!res.ok) {
      const body = await res.json().catch(() => ({}));
      throw new Error(body.detail || "Generování se nezdařilo.");
    }
    const data = await res.json();
    result.textContent = data.message;
    await loadStatus();
    await loadDraft();
  } catch (err) {
    result.textContent = "Chyba: " + err.message;
  } finally {
    btn.disabled = false;
  }
});

function escapeHtml(v) {
  return String(v ?? "").replace(/[&<>"']/g, (c) => ({
    "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;",
  }[c]));
}

async function loadDraft() {
  const body = document.getElementById("draft-body");
  body.innerHTML = "<tr><td colspan='12'>Načítám…</td></tr>";
  try {
    const res = await apiFetch("/api/plan/draft");
    const data = await res.json();
    const rows = data.rows || [];
    if (rows.length === 0) {
      body.innerHTML = "<tr><td colspan='12'>Zatím žádný návrh - vygeneruj tour plán výše.</td></tr>";
      return;
    }
    body.innerHTML = rows
      .map((row, i) => {
        const weeks = row.weeksSinceLastVisit;
        const weeksText = weeks === null || weeks === undefined || weeks === "" ? "nikdy" : Math.round(weeks * 10) / 10;
        return `<tr data-index="${i}">
          <td>${escapeHtml(row.WEEK)}</td>
          <td>${escapeHtml(row.DAY)}</td>
          <td>${escapeHtml(row.POS)}</td>
          <td>${escapeHtml(row.NAZEV_PROVOZOVNY)}</td>
          <td>${escapeHtml(row.TECHNICIAN)}</td>
          <td>${escapeHtml(row.PPT)}</td>
          <td>${escapeHtml(row.lastRealVisitDate)}</td>
          <td>${escapeHtml(weeksText)}</td>
          <td>${escapeHtml(row.terminalType)}</td>
          <td>${escapeHtml(row.market)}</td>
          <td>${escapeHtml(row.REASON_FRIENDLY)}</td>
          <td class="row-actions">
            <button class="ghost small" data-action="move">Přesunout</button>
            <button class="ghost small danger" data-action="remove">Odebrat</button>
          </td>
        </tr>`;
      })
      .join("");
    currentDraftRows = rows;
  } catch (err) {
    body.innerHTML = `<tr><td colspan="12">Chyba: ${err.message}</td></tr>`;
  }
}

let currentDraftRows = [];

document.getElementById("draft-body").addEventListener("click", async (e) => {
  const btn = e.target.closest("button[data-action]");
  if (!btn) return;
  const tr = btn.closest("tr");
  const row = currentDraftRows[parseInt(tr.dataset.index, 10)];
  if (!row) return;

  if (btn.dataset.action === "remove") {
    if (!confirm(`Odebrat POS ${row.POS} (${row.NAZEV_PROVOZOVNY}) technikovi ${row.TECHNICIAN}?`)) return;
    try {
      const res = await apiFetch("/api/plan/remove-pos", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ week: row.WEEK, pos_id: row.POS, technician: row.TECHNICIAN }),
      });
      if (!res.ok) {
        const body = await res.json().catch(() => ({}));
        throw new Error(body.detail || "Odebrání se nezdařilo.");
      }
      await loadDraft();
    } catch (err) {
      alert("Chyba: " + err.message);
    }
  }

  if (btn.dataset.action === "move") {
    const newTech = prompt(`Přesunout POS ${row.POS} na technika (přesné jméno):`, row.TECHNICIAN);
    if (!newTech || newTech === row.TECHNICIAN) return;
    try {
      const res = await apiFetch("/api/plan/change-technician", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          week: row.WEEK, pos_id: row.POS, old_technician: row.TECHNICIAN, new_technician: newTech,
        }),
      });
      if (!res.ok) {
        const body = await res.json().catch(() => ({}));
        throw new Error(body.detail || "Přesunutí se nezdařilo.");
      }
      await loadDraft();
    } catch (err) {
      alert("Chyba: " + err.message);
    }
  }
});

document.getElementById("add-pos-form").addEventListener("submit", async (e) => {
  e.preventDefault();
  const result = document.getElementById("add-pos-result");
  const posId = document.getElementById("add-pos-id").value.trim();
  const week = parseInt(document.getElementById("add-pos-week").value, 10);
  const day = document.getElementById("add-pos-day").value;
  const technician = document.getElementById("add-pos-technician").value.trim();
  result.textContent = "Přidávám…";
  try {
    const res = await apiFetch("/api/plan/add-pos", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ week, day, technician, pos_id: posId }),
    });
    if (!res.ok) {
      const body = await res.json().catch(() => ({}));
      throw new Error(body.detail || "Přidání se nezdařilo.");
    }
    result.textContent = "Přidáno.";
    await loadDraft();
  } catch (err) {
    result.textContent = "Chyba: " + err.message;
  }
});

document.getElementById("refresh-draft").addEventListener("click", loadDraft);

document.getElementById("download-btn").addEventListener("click", async () => {
  try {
    const res = await apiFetch("/api/download/manager-plan");
    if (!res.ok) throw new Error("Stažení se nezdařilo.");
    const blob = await res.blob();
    const url = URL.createObjectURL(blob);
    const a = document.createElement("a");
    a.href = url;
    a.download = "MANAGER_PLAN.xlsx";
    a.click();
    URL.revokeObjectURL(url);
  } catch (err) {
    alert("Chyba: " + err.message);
  }
});

// --- Pravidla plánování (existing TERMINAL_RULES/MARKET_RULES/CATEGORY_RULES/
// ACTIVITY_PLAN - the exact same tables edited in Excel today, just from
// here instead) ---

let currentRules = null;

const CATEGORY_OPTIONS = ["CORE", "NORMAL", "EXCLUDE"];

async function loadRules() {
  try {
    const res = await apiFetch("/api/rules");
    currentRules = await res.json();
    renderToggleList("rules-terminal", "TYP TERMINALU", currentRules.terminal);
    renderToggleList("rules-market", "MARKET", currentRules.market);
    renderDropdownList("rules-category", "CATEGORY", "RULE", currentRules.category, CATEGORY_OPTIONS);
    renderCampaigns(currentRules.campaigns);
  } catch (err) {
    console.error(err);
  }
}

function renderToggleList(containerId, keyField, rows) {
  const el = document.getElementById(containerId);
  el.innerHTML = rows
    .map(
      (row, i) => `
    <label class="toggle-row">
      <input type="checkbox" data-container="${containerId}" data-index="${i}" ${row.ACTIVE === "YES" ? "checked" : ""}>
      ${row[keyField]}
    </label>`
    )
    .join("");
}

function renderDropdownList(containerId, keyField, valueField, rows, options) {
  const el = document.getElementById(containerId);
  el.innerHTML = rows
    .map(
      (row, i) => `
    <label class="dropdown-row">
      ${row[keyField]}
      <select data-container="${containerId}" data-index="${i}">
        ${options.map((o) => `<option value="${o}" ${row[valueField] === o ? "selected" : ""}>${o}</option>`).join("")}
      </select>
    </label>`
    )
    .join("");
}

function renderCampaigns(rows) {
  const body = document.getElementById("rules-campaigns-body");
  body.innerHTML = rows
    .map(
      (row, i) => `
    <tr>
      <td>${row.TYPE}</td>
      <td>${row.ACTIVITY}</td>
      <td><input type="number" data-index="${i}" data-field="START_WEEK" value="${row.START_WEEK ?? ""}"></td>
      <td><input type="number" data-index="${i}" data-field="END_WEEK" value="${row.END_WEEK ?? ""}"></td>
      <td><input type="number" data-index="${i}" data-field="PRIORITY" value="${row.PRIORITY ?? ""}"></td>
      <td>
        <select data-index="${i}" data-field="OVERRIDE_GAP">
          <option value="YES" ${row.OVERRIDE_GAP === "YES" ? "selected" : ""}>YES</option>
          <option value="NO" ${row.OVERRIDE_GAP === "NO" ? "selected" : ""}>NO</option>
        </select>
      </td>
    </tr>`
    )
    .join("");
}

function collectToggleList(containerId, rows) {
  const updated = rows.map((r) => ({ ...r }));
  document.querySelectorAll(`#${containerId} input[type="checkbox"]`).forEach((input) => {
    const i = parseInt(input.dataset.index, 10);
    updated[i].ACTIVE = input.checked ? "YES" : "NO";
  });
  return updated;
}

function collectDropdownList(containerId, valueField, rows) {
  const updated = rows.map((r) => ({ ...r }));
  document.querySelectorAll(`#${containerId} select`).forEach((select) => {
    const i = parseInt(select.dataset.index, 10);
    updated[i][valueField] = select.value;
  });
  return updated;
}

function collectCampaigns(rows) {
  const updated = rows.map((r) => ({ ...r }));
  document.querySelectorAll("#rules-campaigns-body input, #rules-campaigns-body select").forEach((input) => {
    const i = parseInt(input.dataset.index, 10);
    const field = input.dataset.field;
    updated[i][field] = input.type === "number" ? Number(input.value) : input.value;
  });
  return updated;
}

document.getElementById("save-rules-btn").addEventListener("click", async () => {
  const result = document.getElementById("rules-result");
  result.textContent = "Ukládám…";
  try {
    const terminal = collectToggleList("rules-terminal", currentRules.terminal);
    const market = collectToggleList("rules-market", currentRules.market);
    const category = collectDropdownList("rules-category", "RULE", currentRules.category);
    const campaigns = collectCampaigns(currentRules.campaigns);

    for (const [sheet, rows] of [
      ["TERMINAL_RULES", terminal],
      ["MARKET_RULES", market],
      ["CATEGORY_RULES", category],
      ["ACTIVITY_PLAN", campaigns],
    ]) {
      const res = await apiFetch("/api/rules", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ sheet, rows }),
      });
      if (!res.ok) {
        const body = await res.json().catch(() => ({}));
        throw new Error(body.detail || `Uložení ${sheet} se nezdařilo.`);
      }
    }
    result.textContent = "Uloženo.";
    await loadRules();
  } catch (err) {
    result.textContent = "Chyba: " + err.message;
  }
});

if (getToken()) {
  showApp();
} else {
  showLogin();
}
