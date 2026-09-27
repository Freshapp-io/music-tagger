"use strict";
/* global t, tr, LANG */   // from i18n.js
for (const b of document.querySelectorAll("[data-lang-choice]")) {
  b.setAttribute("aria-pressed", String(b.dataset.langChoice === LANG));
  b.onclick = () => { if (b.dataset.langChoice !== LANG) window.fmtI18n.set(b.dataset.langChoice); };
}
document.getElementById("login").addEventListener("submit", async (e) => {
  e.preventDefault();
  const err = document.getElementById("error");
  const btn = e.target.querySelector("button[type=submit]");
  err.textContent = "";
  btn.disabled = true;
  try {
    const r = await fetch("/api/login", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ user: e.target.user.value, password: e.target.password.value }),
    });
    if (r.ok) { location.replace("/"); return; }
    const data = await r.json().catch(() => ({}));
    err.textContent = data.detail ? tr(data.detail) : t("Erreur {code}", { code: r.status });
    e.target.password.value = "";
    e.target.password.focus();
  } catch {
    err.textContent = t("Serveur injoignable");
  } finally {
    btn.disabled = false;
  }
});
