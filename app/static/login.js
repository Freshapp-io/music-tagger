"use strict";
document.getElementById("login").addEventListener("submit", async (e) => {
  e.preventDefault();
  const err = document.getElementById("error");
  const btn = e.target.querySelector("button");
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
    err.textContent = data.detail || `Erreur ${r.status}`;
    e.target.password.value = "";
    e.target.password.focus();
  } catch {
    err.textContent = "Serveur injoignable";
  } finally {
    btn.disabled = false;
  }
});
