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

async function loadDraft() {
  const head = document.getElementById("draft-head");
  const body = document.getElementById("draft-body");
  head.innerHTML = "";
  body.innerHTML = "<tr><td>Načítám…</td></tr>";
  try {
    const res = await apiFetch("/api/plan/draft");
    const data = await res.json();
    const rows = data.rows || [];
    if (rows.length === 0) {
      body.innerHTML = "<tr><td>Zatím žádný návrh - vygeneruj tour plán výše.</td></tr>";
      return;
    }
    const columns = Object.keys(rows[0]);
    head.innerHTML = columns.map((c) => `<th>${c}</th>`).join("");
    body.innerHTML = rows
      .map((row) => "<tr>" + columns.map((c) => `<td>${row[c] ?? ""}</td>`).join("") + "</tr>")
      .join("");
  } catch (err) {
    body.innerHTML = `<tr><td>Chyba: ${err.message}</td></tr>`;
  }
}

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

if (getToken()) {
  showApp();
} else {
  showLogin();
}
