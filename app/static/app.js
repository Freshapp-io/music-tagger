"use strict";
/* global t, tr, LANG, LOCALE */   // from i18n.js

// ------------------------------------------------------------------ helpers
const $ = (sel, root = document) => root.querySelector(sel);
const $$ = (sel, root = document) => [...root.querySelectorAll(sel)];
const esc = (s) => String(s ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
const fmtNum = (n) => (n || 0).toLocaleString(LOCALE);
const fmtSize = (b) => b >= 1e9 ? (b / 1e9).toFixed(1) + " " + t("Go") : b >= 1e6 ? (b / 1e6).toFixed(0) + " " + t("Mo")
  : (b > 0 ? Math.max(1, Math.round(b / 1e3)) : 0) + " " + t("Ko");
const fmtDur = (s) => { s = Math.round(s || 0); const m = Math.floor(s / 60); return `${m}:${String(s % 60).padStart(2, "0")}`; };
const fold = (s) => String(s ?? "").normalize("NFKD").replace(/[̀-ͯ]/g, "").toLowerCase().replace(/[^\w]+/g, " ").trim();
const COLON = LANG === "fr" ? "\u00a0: " : ": ";   // French typography puts a space before ":"
const newWindow = () => `<span class="visually-hidden"> ${t("(nouvelle fenêtre)")}</span>`;

async function api(method, url, body) {
  const opts = { method, headers: {} };
  if (body !== undefined) { opts.headers["Content-Type"] = "application/json"; opts.body = JSON.stringify(body); }
  const r = await fetch(url, opts);
  if (r.status === 401 && !url.startsWith("/api/login")) { location.replace("/login"); throw new Error(t("Session expirée")); }
  const data = await r.json().catch(() => ({}));
  if (!r.ok) throw new Error(data.detail ? tr(data.detail) : t("Erreur {code}", { code: r.status }));
  return data;
}
const qs = (o) => new URLSearchParams(Object.entries(o).filter(([, v]) => v !== "" && v !== undefined && v !== null)).toString();

let toastTimer;
function toast(msg, error = false) {
  const el = $("#toast");
  el.textContent = msg;
  el.className = "toast" + (error ? " error" : "");
  clearTimeout(toastTimer);
  toastTimer = setTimeout(() => el.classList.add("hidden"), error ? 7000 : 3500);
}
async function run(fn) {
  try { return await fn(); } catch (e) { toast(e.message, true); throw e; }
}

const ISSUE = { untagged: t("Non taggué"), various: t("Various"), inconsistent: t("Incohérent"), duplicate: t("Doublon"), misplaced: t("Mauvais dossier") };
const SUB = {
  albumartist_mixed: t("Album artist différents"),
  albumartist_missing: t("Album artist absent, artistes multiples"),
  album_mixed: t("Nom d'album différent"),
  year_mixed: t("Années différentes"),
  mbid_mixed: t("ID MusicBrainz différents"),
};
const CONF = { high: t("sûre"), medium: t("probable"), low: t("incertaine") };
const badges = (issues) => (issues || []).filter((i) => ISSUE[i]).map((i) => `<span class="badge b-${i}">${ISSUE[i]}</span>`).join("");
const subBadges = (issues) => (issues || []).filter((i) => SUB[i]).map((i) => `<span class="chip">${SUB[i]}</span>`).join(" ");
const chips = (arr, max = 4) => {
  const a = (arr || []).map((x) => x === null ? t("∅ vide") : x);
  const more = a.length > max ? `<span class="chip">+${a.length - max}</span>` : "";
  return `<div class="chips">${a.slice(0, max).map((x) => `<span class="chip" title="${esc(x)}">${esc(x)}</span>`).join("")}${more}</div>`;
};

// ------------------------------------------------------------------ status
let status = null;
let lastJobStatus = null;

async function refreshStatus() {
  try { status = await api("GET", "/api/status"); } catch { setTimeout(refreshStatus, 5000); return; }
  $("#version").textContent = status.version || "";
  $("#who").textContent = status.user || "";
  for (const el of $$("[data-count]")) {
    const n = status.counts[el.dataset.count];
    el.textContent = n ? fmtNum(n) : "";
  }
  const j = status.job, box = $("#job");
  if (j) {
    box.classList.remove("hidden");
    const pct = j.total ? Math.round((100 * j.done) / j.total) : 0;
    const state = j.status === "running" ? `${pct}%` : j.status === "done" ? t("terminé") : t("échec");
    box.innerHTML = `<b>${esc(tr(j.label))}</b> — ${state}
      <div class="bar"><div style="width:${j.status === "running" ? pct : 100}%"></div></div>
      <div class="msg" title="${esc(tr(j.message))}">${esc(tr(j.message))}</div>
      ${j.n_errors ? `<div class="small" style="color:var(--bad)">${t("{n} erreur(s)", { n: j.n_errors })}</div>` : ""}`;
    // Phones: the sidebar is hidden, so mirror a running job in the top bar.
    $("#topbar-job").innerHTML = j.status === "running"
      ? `${esc(tr(j.label))} · ${pct}%<div class="bar"><div style="width:${pct}%"></div></div>` : "";
    if (lastJobStatus === "running" && j.status !== "running") {
      toast(`${tr(j.label)}${COLON}${j.status === "done" ? tr(j.message) || t("terminé") : t("échec") + " — " + tr(j.message)}`, j.status !== "done");
      if (currentView() !== "review") render();   // never wipe edits in progress
    }
    lastJobStatus = j.status;
  }
  setTimeout(refreshStatus, j && j.status === "running" ? 1000 : 8000);
}

// ------------------------------------------------------------------ router
const listState = {};
function stateFor(view) {
  return listState[view] ||= { q: "", sub: "", confidence: "", showIgnored: false, sort: "dir", offset: 0, selected: new Set() };
}

function currentView() {
  return location.hash.replace(/^#\/?/, "").split("?")[0] || "dashboard";
}

async function render() {
  const view = currentView();
  for (const a of $$("#nav a")) a.classList.toggle("active", a.dataset.view === view);
  const main = $("#main");
  if (view === "dashboard") return renderDashboard(main);
  if (view === "duplicates") return renderDuplicates(main);
  if (view === "history") return renderHistory(main);
  if (view === "review") return renderReview(main);
  if (view === "genres") return renderGenres(main);
  if (view === "autotag") return renderAutotag(main);
  if (view === "errors") return renderErrors(main);
  if (["untagged", "various", "inconsistent", "misplaced", "all"].includes(view)) return renderList(main, view);
  main.innerHTML = `<div class="empty">${t("Page inconnue")}</div>`;
}
window.addEventListener("hashchange", render);

// --------------------------------------------------------------- dashboard
async function renderDashboard(main) {
  if (!status) await refreshStatus();
  const s = status, c = s.counts;
  const trash = await api("GET", "/api/trash").catch(() => null);
  const card = (href, n, label) => `<a class="card" href="${href}"><div class="num">${fmtNum(n)}</div><div class="lbl">${label}</div></a>`;
  main.innerHTML = `
    <h1>${t("Tableau de bord")}</h1>
    <p class="lead">${t("Bibliothèque :")} <span class="mono">${esc(s.music_root)}</span>
      ${s.root_exists ? "" : `<b style="color:var(--bad)"> — ${t("introuvable, vérifiez le volume Docker")}</b>`}<br>
      ${t("Dernier scan :")} ${s.last_scan ? esc(s.last_scan.replace("T", " ")) : t("jamais")}</p>
    <div class="toolbar">
      <button class="primary" id="scan">${t("Scanner (nouveaux / modifiés)")}</button>
      <button id="fullscan">${t("Scan complet")}</button>
      <button id="reanalyze">${t("Recalculer l'analyse")}</button>
      ${s.navidrome ? `<button id="navidrome">${t("Lancer un scan Navidrome")}</button>` : ""}
    </div>
    <div class="cards">
      ${card("#/all", s.tracks, t("fichiers mp3"))}
      ${card("#/all", s.albums, t("dossiers (albums)"))}
      ${card("#/untagged", c.untagged, t("dossiers avec fichiers non / mal taggués"))}
      ${card("#/various", c.various, t("dossiers « Various Artists » suspects"))}
      ${card("#/inconsistent", c.inconsistent, t("dossiers aux tags incohérents"))}
      ${card("#/duplicates", c.duplicate_groups, t("groupes d'albums en double"))}
      ${card("#/misplaced", c.misplaced, t("albums rangés chez un autre artiste"))}
      ${card("#/errors", c.errors, t("dossiers avec fichiers illisibles"))}
    </div>
    <h2>${t("Comment ça marche")}</h2>
    <div class="panel">
      <ol style="margin:0;padding-left:20px">
        <li>${t("help.scan")}</li>
        <li>${t("help.analyse")}</li>
        <li>${t("help.suggest")}</li>
        <li>${t("help.undo")}</li>
      </ol>
    </div>
    <h2>${t("Corbeille")}</h2>
    <div class="panel">
      ${trash ? `<span class="mono">${esc(trash.path)}</span> — ${t("{n} fichier(s)", { n: trash.files })}, ${fmtSize(trash.size)}` : "—"}
      ${trash && trash.files ? `<button class="danger" id="empty-trash" style="margin-left:12px">${t("Vider définitivement")}</button>` : ""}
    </div>`;
  $("#scan").onclick = () => run(async () => { await api("POST", "/api/scan", { full: false }); lastJobStatus = "running"; refreshStatus(); });
  $("#fullscan").onclick = () => confirm(t("Relire tous les fichiers ? (long sur une grosse bibliothèque)")) &&
    run(async () => { await api("POST", "/api/scan", { full: true }); lastJobStatus = "running"; refreshStatus(); });
  $("#reanalyze").onclick = () => run(async () => { await api("POST", "/api/reanalyze"); lastJobStatus = "running"; refreshStatus(); });
  if ($("#navidrome")) $("#navidrome").onclick = () => run(async () => { await api("POST", "/api/navidrome/scan"); toast(t("Scan Navidrome lancé")); });
  if ($("#empty-trash")) $("#empty-trash").onclick = () =>
    confirm(t("Supprimer définitivement {n} fichier(s) ({size}) ? Cette action est irréversible.", { n: trash.files, size: fmtSize(trash.size) })) &&
    run(async () => { await api("POST", "/api/trash/empty"); toast(t("Corbeille vidée")); render(); });
}

// -------------------------------------------------------------------- lists
const VIEW_INFO = {
  untagged: [t("Fichiers non taggués"), t("lead.untagged")],
  various: [t("Various Artists"), t("lead.various")],
  inconsistent: [t("Tags incohérents"), t("lead.inconsistent")],
  misplaced: [t("Albums dans un mauvais dossier"), t("lead.misplaced")],
  all: [t("Tous les dossiers"), t("Tous les dossiers contenant des mp3.")],
};

// Views without "apply the suggestion" / auto-tag: the tags may well be right there.
const NO_FIX = ["all", "misplaced"];

async function renderList(main, view) {
  const st = stateFor(view);
  const [title, lead] = VIEW_INFO[view];
  const sorts = { dir: t("Tri : dossier"), artist: t("Tri : artiste"), tracks: t("Tri : nb pistes"), bitrate: t("Tri : bitrate") };
  main.innerHTML = `
    <h1>${title}</h1><p class="lead">${lead}</p>
    <div class="toolbar">
      <input type="search" id="q" placeholder="${t("Rechercher (dossier, artiste, album)…")}" value="${esc(st.q)}" style="width:320px">
      ${view === "inconsistent" ? `<select id="sub"><option value="">${t("Tous les problèmes")}</option>${Object.entries(SUB).map(([k, v]) => `<option value="${k}" ${st.sub === k ? "selected" : ""}>${v}</option>`).join("")}</select>` : ""}
      <select id="confidence"><option value="">${t("Toutes confiances")}</option>${Object.entries(CONF).map(([k, v]) => `<option value="${k}" ${st.confidence === k ? "selected" : ""}>${t("Suggestion {conf}", { conf: v })}</option>`).join("")}</select>
      <select id="sort">${Object.entries(sorts).map(([k, v]) => `<option value="${k}" ${st.sort === k ? "selected" : ""}>${v}</option>`).join("")}</select>
      ${view !== "all" ? `<label><input type="checkbox" id="showIgnored" ${st.showIgnored ? "checked" : ""}> ${t("afficher les ignorés")}</label>` : ""}
      <span class="grow"></span>
      ${!NO_FIX.includes(view) ? `<button id="autotag" title="${t("Chercher chaque album sur MusicBrainz et le noter")}">⚡ ${t("Tag auto")}</button>` : ""}
      ${view !== "all" ? `<button class="primary" id="review">▶ ${t("Revue album par album")}</button>` : ""}
    </div>
    <div id="batch"></div>
    <div id="list"><div class="empty">${t("Chargement…")}</div></div>`;
  let timer;
  $("#q").oninput = (e) => { clearTimeout(timer); timer = setTimeout(() => { st.q = e.target.value; st.offset = 0; loadList(view); }, 300); };
  if ($("#sub")) $("#sub").onchange = (e) => { st.sub = e.target.value; st.offset = 0; loadList(view); };
  $("#confidence").onchange = (e) => { st.confidence = e.target.value; st.offset = 0; loadList(view); };
  $("#sort").onchange = (e) => { st.sort = e.target.value; loadList(view); };
  if ($("#showIgnored")) $("#showIgnored").onchange = (e) => { st.showIgnored = e.target.checked; st.offset = 0; loadList(view); };
  // The selection if there is one, otherwise every folder matching the filters.
  const matchingDirs = async () => {
    if (st.selected.size) return [...st.selected];
    const dirs = [];
    for (let off = 0; ; off += 1000) {
      const d = await api("GET", "/api/albums?" + listParams(view, st, { offset: off, limit: 1000 }));
      dirs.push(...d.items.map((a) => a.dir));
      if (off + 1000 >= d.total) break;
    }
    return dirs;
  };
  if ($("#autotag")) $("#autotag").onclick = () => run(async () => {
    startAutotag(await matchingDirs(), st.selected.size ? t("sélection") : VIEW_INFO[view][0]);
  });
  if ($("#review")) $("#review").onclick = () => run(async () => {
    startReview(view, VIEW_INFO[view][0] + (st.selected.size ? " " + t("(sélection)") : ""), await matchingDirs(), "#/" + view);
  });
  loadList(view);
}

function listParams(view, st, extra = {}) {
  return qs({ issue: view, q: st.q, sub: st.sub, confidence: st.confidence, show_ignored: st.showIgnored, sort: st.sort, ...extra });
}

function misplacedHtml(m) {
  if (!m) return "";
  return `<div class="sug">
    <div>${t("Dossier de")}${COLON}<b>${esc(m.folder_artist)}</b></div>
    <div>${t("Tags")}${COLON}<b>${esc(m.tag_artist)}</b></div>
    <div class="small">${m.target ? `${t("Place attendue")}${COLON}<span class="mono">${esc(m.target)}</span>`
      : `<span class="muted">${t("Aucun dossier « {name} » dans la bibliothèque", { name: esc(m.tag_artist) })}</span>`}</div>
  </div>`;
}

function suggestionHtml(a) {
  const s = a.suggestion || {};
  if (!s.albumartist && !s.album) return `<span class="muted">${t("aucune")}</span>`;
  const cur = (arr) => (arr || []).filter((x) => x !== null);
  const diff = (label, val, current) => {
    if (!val) return "";
    const same = current.length === 1 && fold(current[0]) === fold(val) && current[0] === val;
    return `<div>${label}${COLON}${same ? `<span class="muted">${esc(val)}</span>` : `<b>${esc(val)}</b>`}</div>`;
  };
  return `<div class="sug">
    ${diff(t("Album artist"), s.albumartist, cur(a.albumartists))}
    ${diff(t("Album"), s.album, cur(a.albums))}
    ${diff(t("Année"), s.year, cur(a.years))}
    ${s.compilation ? `<div><b>${t("compilation")}</b></div>` : ""}
    <div class="small conf-${s.confidence}" title="${esc(tr(s.reason))}">● ${CONF[s.confidence] || ""}${s.reason ? ` — ${esc(tr(s.reason))}` : ""}</div>
  </div>`;
}

async function loadList(view) {
  const st = stateFor(view);
  const data = await run(() => api("GET", "/api/albums?" + listParams(view, st, { offset: st.offset, limit: 100 })));
  const box = $("#list");
  if (!box) return;
  if (!data.items.length) {
    box.innerHTML = `<div class="empty">${status && !status.tracks ? t("Aucun fichier : lancez un scan depuis le tableau de bord.") : t("Rien à corriger ici")}</div>`;
    renderBatch(view, data.total);
    return;
  }
  box.innerHTML = `
    <table>
      <thead><tr>
        <th class="check"><input type="checkbox" id="checkall" title="${t("Sélectionner la page")}"></th>
        <th>${t("Dossier")}</th><th>${t("Tags actuels")}</th><th>${view === "misplaced" ? t("Emplacement") : t("Suggestion")}</th><th>${t("Pistes")}</th><th>${t("Qualité")}</th>
      </tr></thead>
      <tbody>${data.items.map((a) => `
        <tr class="clickable" data-dir="${esc(a.dir)}">
          <td class="check"><input type="checkbox" class="sel" ${st.selected.has(a.dir) ? "checked" : ""}></td>
          <td><div class="dir">${esc(a.dir || "/")}</div>
            <div>${badges(a.issues)} ${a.ignored.length ? `<span class="chip">${t("ignoré :")} ${a.ignored.map((k) => ISSUE[k] || k).join(", ")}</span>` : ""}</div>
            <div style="margin-top:3px">${subBadges(a.issues)}</div></td>
          <td class="small">
            <div class="muted">${t("Artistes")}</div>${chips(a.artists, 3)}
            <div class="muted">${t("Album artist")}</div>${chips(a.albumartists, 3)}
            <div class="muted">${t("Album")}</div>${chips(a.albums, 2)}
          </td>
          <td>${view === "misplaced" ? misplacedHtml(a.misplaced) : suggestionHtml(a)}</td>
          <td>${a.n_tracks}${a.n_untagged ? `<div class="small conf-low">${t("{n} sans tag", { n: a.n_untagged })}</div>` : ""}${a.n_incomplete ? `<div class="small conf-medium">${t("{n} incomplet(s)", { n: a.n_incomplete })}</div>` : ""}</td>
          <td class="small">${a.avg_bitrate} kbps${a.vbr ? " VBR" : ""}<div class="muted">${fmtSize(a.total_size)}</div></td>
        </tr>`).join("")}
      </tbody>
    </table>
    <div class="pager">
      <span class="muted">${t("{from}–{to} sur {total}", { from: st.offset + 1, to: st.offset + data.items.length, total: fmtNum(data.total) })}</span>
      <button id="prev" ${st.offset === 0 ? "disabled" : ""}>‹ ${t("Précédent")}</button>
      <button id="next" ${st.offset + 100 >= data.total ? "disabled" : ""}>${t("Suivant")} ›</button>
    </div>`;
  $("#prev").onclick = () => { st.offset = Math.max(0, st.offset - 100); loadList(view); window.scrollTo(0, 0); };
  $("#next").onclick = () => { st.offset += 100; loadList(view); window.scrollTo(0, 0); };
  $("#checkall").onchange = (e) => {
    for (const a of data.items) e.target.checked ? st.selected.add(a.dir) : st.selected.delete(a.dir);
    for (const cb of $$(".sel", box)) cb.checked = e.target.checked;
    renderBatch(view, data.total);
  };
  for (const row of $$("tbody tr", box)) {
    const dir = row.dataset.dir;
    $(".sel", row).onclick = (e) => {
      e.stopPropagation();
      e.target.checked ? st.selected.add(dir) : st.selected.delete(dir);
      renderBatch(view, data.total);
    };
    row.onclick = () => openAlbum(dir, () => loadList(view));
  }
  renderBatch(view, data.total);
}

function renderBatch(view, total) {
  const st = stateFor(view), box = $("#batch");
  if (!box) return;
  const n = st.selected.size;
  box.innerHTML = `<div class="batchbar">
    <b>${t("{n} sélectionné(s)", { n: fmtNum(n) })}</b>
    <button class="link" id="selall">${t("Tout sélectionner ({n} résultats)", { n: fmtNum(total) })}</button>
    ${n ? `<button class="link" id="selnone">${t("Désélectionner")}</button>` : ""}
    <span style="flex:1"></span>
    ${view !== "all" ? `
      ${!NO_FIX.includes(view) ? `<button class="primary" id="apply" ${n ? "" : "disabled"}>${t("Appliquer les suggestions")}</button>` : ""}
      ${view === "various" ? `<button id="compil" ${n ? "" : "disabled"}>${t("Marquer comme compilations")}</button>` : ""}
      <button id="ignore" ${n ? "" : "disabled"}>${st.showIgnored ? t("Ne plus ignorer") : t("Ignorer")}</button>` : ""}
  </div>`;
  $("#selall").onclick = () => run(async () => {
    for (let off = 0; off < total; off += 1000) {
      const d = await api("GET", "/api/albums?" + listParams(view, st, { offset: off, limit: 1000 }));
      d.items.forEach((a) => st.selected.add(a.dir));
    }
    loadList(view);
  });
  if ($("#selnone")) $("#selnone").onclick = () => { st.selected.clear(); loadList(view); };
  if ($("#apply")) $("#apply").onclick = () => {
    if (!confirm(t("confirm.apply", { n }))) return;
    run(async () => {
      await api("POST", "/api/batch/apply", { dirs: [...st.selected] });
      st.selected.clear(); lastJobStatus = "running"; refreshStatus(); loadList(view);
    });
  };
  if ($("#compil")) $("#compil").onclick = () => confirm(t("Définir album artist = « Various Artists » et le flag compilation sur {n} dossier(s) ?", { n })) &&
    run(async () => {
      await api("POST", "/api/batch/compilation", { dirs: [...st.selected] });
      st.selected.clear(); lastJobStatus = "running"; refreshStatus();
    });
  if ($("#ignore")) $("#ignore").onclick = () => run(async () => {
    await api("POST", "/api/ignore", { dirs: [...st.selected], kind: view, value: !st.showIgnored });
    st.selected.clear(); toast("OK"); loadList(view); refreshStatus();
  });
}

// ------------------------------------------------------------ audio player
function playTrack(path, label) {
  const bar = $("#player"), audio = $("#pl-audio");
  bar.classList.remove("hidden");
  document.body.classList.add("has-player");
  $("#pl-label").textContent = label;
  audio.src = "/api/audio?" + qs({ path });
  audio.play().catch((e) => toast(t("Lecture impossible :") + " " + e.message, true));
  $$(".play").forEach((b) => b.classList.toggle("playing", b.dataset.play === path));
}
$("#pl-close").onclick = () => {
  const audio = $("#pl-audio");
  audio.pause(); audio.removeAttribute("src"); audio.load();
  $("#player").classList.add("hidden");
  document.body.classList.remove("has-player");
  $$(".play").forEach((b) => b.classList.remove("playing"));
};
$("#pl-audio").addEventListener("ended", () => $$(".play").forEach((b) => b.classList.remove("playing")));
const playBtn = (tk) => `<button class="play" data-play="${esc(tk.path)}" data-label="${esc((tk.artist ? tk.artist + " – " : "") + (tk.title || tk.filename))}" title="${t("Écouter")}">▶</button>`;
function bindPlay(root) {
  for (const b of $$(".play", root)) b.onclick = (e) => { e.stopPropagation(); playTrack(b.dataset.play, b.dataset.label); };
}

// ------------------------------------------------------------ album drawer
let drawerOnClose = null;
function closeDrawer() {
  $("#drawer").classList.add("hidden");
  document.body.style.overflow = "";
  if (drawerOnClose) drawerOnClose();
  drawerOnClose = null;
}
$("#drawer").addEventListener("mousedown", (e) => { if (e.target.id === "drawer") closeDrawer(); });
document.addEventListener("keydown", (e) => { if (e.key === "Escape" && !$("#drawer").classList.contains("hidden")) closeDrawer(); });

function majorityOf(values) {
  const c = {};
  values.filter(Boolean).forEach((v) => (c[v] = (c[v] || 0) + 1));
  return Object.entries(c).sort((a, b) => b[1] - a[1])[0]?.[0] || "";
}

async function openAlbum(dir, onClose) {
  drawerOnClose = onClose || null;
  const body = $("#drawer-body");
  $("#drawer").classList.remove("hidden");
  document.body.style.overflow = "hidden";
  await albumEditor(body, dir, { onClose: closeDrawer, onReload: () => openAlbum(dir, drawerOnClose) });
  body.scrollTop = 0;
}

/** Show every folder under `prefix` in the "all folders" list. */
function browseFolder(prefix) {
  const st = stateFor("all");
  Object.assign(st, { q: prefix, offset: 0, sub: "", confidence: "" });
  if (!$("#drawer").classList.contains("hidden")) closeDrawer();
  if (currentView() === "all") render(); else location.hash = "#/all";
}

/** Where the album lives: library › folder › … › album, plus the other albums of its folder. */
function folderContext(d) {
  const parts = (d.album.dir || "").split("/").filter(Boolean);
  const crumbs = [`<button class="link crumb" data-browse="" title="${t("Voir tous les dossiers")}">${esc(d.library)}</button>`];
  parts.forEach((p, i) => {
    const path = parts.slice(0, i + 1).join("/");
    crumbs.push(i === parts.length - 1 ? `<b>${esc(p)}</b>`
      : `<button class="link crumb" data-browse="${esc(path)}" title="${esc(t("Voir les dossiers de « {name} »", { name: p }))}">${esc(p)}</button>`);
  });
  const parent = parts.slice(0, -1).join("/");
  let others = "";
  if (!parent) {
    others = `<div class="small muted">${t("Album rangé directement à la racine de la bibliothèque.")}</div>`;
  } else if (d.n_siblings) {
    const shown = d.siblings.map((s) => `<button class="chip sibling" data-open="${esc(s.dir)}" title="${esc(s.dir)}">${esc(s.name)}
      <span class="muted">· ${s.n_tracks}</span>${s.issues.filter((i) => ISSUE[i]).length ? " ⚠" : ""}</button>`).join("");
    others = `<div class="small muted">${t("Aussi dans « {name} » ({n}) :", { name: esc(parts[parts.length - 2]), n: d.n_siblings })}</div>
      <div class="chips">${shown}${d.n_siblings > d.siblings.length ? `<span class="chip">+${d.n_siblings - d.siblings.length}</span>` : ""}</div>`;
  } else {
    others = `<div class="small muted">${t("Seul album du dossier « {name} ».", { name: esc(parts[parts.length - 2]) })}</div>`;
  }
  const icon = `<svg class="folder-ico" viewBox="0 0 24 24" aria-hidden="true"><path d="M3 6.5A1.5 1.5 0 0 1 4.5 5h4.3l2 2h8.7A1.5 1.5 0 0 1 21 8.5v9a1.5 1.5 0 0 1-1.5 1.5h-15A1.5 1.5 0 0 1 3 17.5z"/></svg>`;
  const m = d.album.misplaced;
  const wrong = m ? `<div class="notice small">⚠ ${t("Rangé dans le dossier de {folder}, mais les tags indiquent {tags}.", { folder: `<b>${esc(m.folder_artist)}</b>`, tags: `<b>${esc(m.tag_artist)}</b>` })}
    ${m.target ? `${t("Place attendue")}${COLON}<span class="mono">${esc(m.target)}</span>` : ""}
    <div class="muted">${t("hint.misplaced")}</div></div>` : "";
  return `<nav class="crumbs" aria-label="${t("Emplacement")}">${icon} ${crumbs.join(' <span class="muted">›</span> ')}</nav>${others}${wrong}`;
}

const TRACK_FIELDS = ["disc", "track", "title", "artist"];

/**
 * Album editor rendered into `root`. Used by the side drawer and by review mode.
 * opts.review: hide the save/ignore buttons (review mode has its own action bar)
 * Returns { save(), pending(), album } — save() writes album fields + every changed track.
 */
async function albumEditor(root, dir, opts = {}) {
  const d = await run(() => api("GET", "/api/album?" + qs({ dir })));
  const { album: a, tracks } = d;
  const s = a.suggestion || {};
  const coverTrack = tracks.find((tk) => tk.has_cover);
  const allComp = tracks.length && tracks.every((tk) => tk.compilation);
  const isVarious = a.issues.includes("various") || s.compilation || allComp;
  const cur = {
    albumartist: majorityOf(tracks.map((tk) => tk.albumartist)),
    album: majorityOf(tracks.map((tk) => tk.album)),
    year: majorityOf(tracks.map((tk) => tk.year)),
    genre: majorityOf(tracks.map((tk) => tk.genre)),
  };
  const init = { albumartist: s.albumartist || cur.albumartist, album: s.album || cur.album, year: s.year || cur.year, genre: cur.genre };
  const distinctVals = (k) => [...new Set(tracks.map((tk) => tk[k]).filter(Boolean))];
  const fieldRow = (key, label) => {
    const opts2 = distinctVals(key);
    return `<label>${label}</label><div class="field">
      <input data-album="${key}" value="${esc(init[key])}" ${key === "genre" ? 'list="genre-canon"' : ""}>
      ${opts2.length ? `<div class="chips">${opts2.slice(0, 12).map((o) => `<button type="button" class="chip" data-pick="${key}" data-val="${esc(o)}" title="${t("Utiliser cette valeur")}">${esc(o)}</button>`).join("")}</div>` : ""}
    </div>`;
  };

  // Track model shared by the table view and the track-by-track view.
  const orig = Object.fromEntries(tracks.map((tk) => [tk.path, Object.fromEntries(TRACK_FIELDS.map((f) => [f, tk[f] || ""]))]));
  const vals = Object.fromEntries(tracks.map((tk) => [tk.path, { ...orig[tk.path] }]));
  const extra = {};          // MusicBrainz ids picked per track
  const saved = new Set();   // tracks saved individually
  let view = isVarious ? "cards" : "table";

  root.innerHTML = `
    <div class="drawer-head">
      ${coverTrack ? `<img src="/api/cover?${qs({ path: coverTrack.path })}" alt="">` : `<img alt="">`}
      <div class="grow">
        <h1>${esc((a.dir || "/").split("/").pop())}</h1>
        <div>${badges(a.issues)} ${subBadges(a.issues)}</div>
        <div class="muted small" style="margin-top:4px">${t("{n} pistes", { n: a.n_tracks })} · ${fmtDur(a.duration)} · ${a.avg_bitrate} kbps${a.vbr ? " VBR" : ""} · ${fmtSize(a.total_size)} · ${t("qualité {q}", { q: a.quality })}</div>
        ${s.reason ? `<div class="small conf-${s.confidence}">${t("Suggestion {conf} :", { conf: CONF[s.confidence] })} ${esc(tr(s.reason))}</div>` : ""}
      </div>
      ${opts.onClose ? `<button class="ed-close" title="${t("Fermer")}">✕</button>` : ""}
    </div>
    <div class="folder-context">${folderContext(d)}</div>

    <div class="panel">
      <div class="form-grid">
        ${fieldRow("albumartist", t("Album artist"))}
        ${fieldRow("album", t("Album"))}
        ${fieldRow("year", t("Année"))}
        ${fieldRow("genre", t("Genre"))}
        <label>${t("Compilation")}</label><div><label><input type="checkbox" class="ed-compil" ${s.compilation || allComp ? "checked" : ""}> ${t("Vraie compilation (plusieurs artistes, TCMP=1)")}</label></div>
      </div>
      <p class="small muted" style="margin-bottom:0">${t("hint.albumfields")}</p>
    </div>

    <div class="toolbar">
      <b>${t("Pistes")}</b>
      <div class="seg">
        <button data-view="table">${t("Tableau")}</button><button data-view="cards">${t("Piste par piste")}</button>
      </div>
      <span class="grow"></span>
      <button class="ed-fill-empty">${t("Compléter les vides depuis les noms de fichiers")}</button>
      <button class="ed-fill-all">${t("Tout remplacer depuis les noms de fichiers")}</button>
      <button class="ed-artist-aa">${t("Artiste = album artist")}</button>
    </div>
    <div class="ed-tracks"></div>

    <div class="actions">
      ${opts.review ? "" : `<button class="primary ed-save">${t("Enregistrer les tags")}</button>`}
      <button class="ed-mb-open">${t("Chercher l'album sur MusicBrainz")}</button>
      ${opts.review ? "" : a.issues.filter((i) => ISSUE[i]).map((i) => a.ignored.includes(i)
        ? `<button data-unignore="${i}">${t("Ne plus ignorer ({issue})", { issue: ISSUE[i] })}</button>`
        : `<button data-ignore="${i}">${t("Ignorer ({issue})", { issue: ISSUE[i] })}</button>`).join("")}
      <button class="danger ed-delete">${t("Supprimer l'album")}</button>
    </div>

    <div class="panel hidden ed-mb" style="margin-top:16px"></div>

    ${d.duplicates.length ? `<h2>${t("Doublons probables")}</h2><div class="panel">${d.duplicates.map((o) => `
      <div style="padding:4px 0"><a href="#" data-open="${esc(o.dir)}">${esc(o.dir)}</a>
      <span class="muted small">— ${t("{n} pistes", { n: o.n_tracks })}, ${o.avg_bitrate} kbps${o.vbr ? " VBR" : ""}, ${t("qualité {q}", { q: o.quality })} (${t("celui-ci : {q}", { q: a.quality })})</span></div>`).join("")}
      <a href="#/duplicates">${t("Gérer dans la page Doublons")} →</a></div>` : ""}
    <datalist id="genre-canon">${genreCanon.map((g) => `<option value="${esc(g)}">`).join("")}</datalist>
    ${d.other_files.length ? `<h2>${t("Autres fichiers du dossier")}</h2><div class="small muted mono">${d.other_files.map(esc).join(" · ")}</div>` : ""}
  `;

  // ---- album fields
  const dirty = new Set();
  const mark = (inp, original) => inp.classList.toggle("changed", (inp.value || "") !== (original || ""));
  for (const inp of $$("[data-album]", root)) {
    const k = inp.dataset.album;
    const all = distinctVals(k);
    inp._orig = all.length === 1 && tracks.every((tk) => tk[k]) ? all[0] : null;
    mark(inp, inp._orig);
    inp.oninput = () => { dirty.add(k); mark(inp, inp._orig); };
  }
  for (const b of $$("[data-pick]", root)) b.onclick = () => {
    const inp = $(`[data-album="${b.dataset.pick}"]`, root);
    inp.value = b.dataset.val; dirty.add(b.dataset.pick); mark(inp, inp._orig);
  };
  const albumArtist = () => $('[data-album="albumartist"]', root).value.trim();

  // ---- tracks
  const trackDiff = (path) => {
    const ch = {};
    for (const f of TRACK_FIELDS) if ((vals[path][f] || "").trim() !== orig[path][f]) ch[f] = (vals[path][f] || "").trim();
    return Object.assign(ch, extra[path] || {});
  };
  const input = (tk, f, cls = "") => `<input class="${cls}" data-path="${esc(tk.path)}" data-f="${f}" value="${esc(vals[tk.path][f])}" placeholder="${esc(tk.guess[f] || "")}">`;
  const bindInputs = () => {
    for (const inp of $$("input[data-f]", root)) {
      mark(inp, orig[inp.dataset.path][inp.dataset.f]);
      inp.oninput = () => { vals[inp.dataset.path][inp.dataset.f] = inp.value; mark(inp, orig[inp.dataset.path][inp.dataset.f]); };
    }
  };
  const guessLine = (tk) => {
    const g = tk.guess;
    if (!g.title && !g.artist) return "";
    return `<span class="muted">${t("Nom de fichier :")}</span> ${g.track ? esc(g.track) + ". " : ""}${g.artist ? `<b>${esc(g.artist)}</b> – ` : ""}${esc(g.title || "")}
      <button class="link use-guess" data-path="${esc(tk.path)}">${t("utiliser")}</button>`;
  };
  const untaggedBadge = `<span class="badge b-untagged">${t("sans tag")}</span>`;

  function renderTracks() {
    for (const b of $$(".seg button", root)) b.classList.toggle("on", b.dataset.view === view);
    const box = $(".ed-tracks", root);
    if (view === "table") {
      box.innerHTML = `<table class="tracks">
        <thead><tr><th></th><th class="num">${t("Disque")}</th><th class="num">${t("N°")}</th><th>${t("Titre")}</th><th>${t("Artiste")}</th><th>${t("Fichier")}</th><th>kbps</th></tr></thead>
        <tbody>${tracks.map((tk) => `
          <tr>
            <td>${playBtn(tk)}</td>
            <td>${input(tk, "disc")}</td><td>${input(tk, "track")}</td><td>${input(tk, "title")}</td><td>${input(tk, "artist")}</td>
            <td class="small mono" title="${esc(tk.filename)}">${esc(tk.filename)}${tk.tagged ? "" : " " + untaggedBadge}${tk.error ? ` <span class="badge b-untagged" title="${esc(tk.error)}">${t("erreur")}</span>` : ""}</td>
            <td class="small">${tk.bitrate || "?"}${tk.bitrate_mode === "VBR" ? "v" : ""}</td>
          </tr>`).join("")}</tbody></table>`;
    } else {
      box.innerHTML = tracks.map((tk) => `
        <div class="tcard ${saved.has(tk.path) ? "saved" : ""}" data-path="${esc(tk.path)}">
          <div class="tcard-head">
            ${playBtn(tk)}
            <span class="mono small">${esc(tk.filename)}</span>
            <span class="muted small">${fmtDur(tk.duration)} · ${tk.bitrate || "?"} kbps</span>
            ${tk.tagged ? "" : untaggedBadge}
            <span class="grow"></span>
            <span class="tstate small">${saved.has(tk.path) ? "✓ " + t("enregistré") : ""}</span>
          </div>
          <div class="tcard-fields">
            <label>${t("N°")}${input(tk, "track")}</label>
            <label class="wide">${t("Titre")}${input(tk, "title")}</label>
            <label class="wide">${t("Artiste")}${input(tk, "artist")}</label>
          </div>
          <div class="small tcard-guess">${guessLine(tk)}</div>
          <div class="tcard-actions">
            <button class="mb-rec">${t("Chercher ce morceau sur MusicBrainz")}</button>
            <button class="primary save-one">${t("Enregistrer ce morceau")}</button>
          </div>
          <div class="rec-results"></div>
        </div>`).join("");
    }
    bindInputs();
    bindPlay(box);
    for (const b of $$(".use-guess", box)) b.onclick = () => {
      const tk = tracks.find((x) => x.path === b.dataset.path);
      for (const f of ["track", "title", "artist"]) if (tk.guess[f]) vals[tk.path][f] = tk.guess[f];
      renderTracks();
    };
    for (const card of $$(".tcard", box)) {
      const path = card.dataset.path, tk = tracks.find((x) => x.path === path);
      $(".save-one", card).onclick = () => run(async () => {
        const ch = trackDiff(path);
        if (!Object.keys(ch).length) return toast(t("Aucun changement sur ce morceau"));
        await api("POST", "/api/album/save", { dir, album: {}, tracks: { [path]: ch } });
        orig[path] = { ...orig[path], ...Object.fromEntries(TRACK_FIELDS.filter((f) => f in ch).map((f) => [f, ch[f]])) };
        delete extra[path];
        saved.add(path);
        renderTracks();
        refreshStatus();
      });
      $(".mb-rec", card).onclick = () => run(async () => {
        const res = $(".rec-results", card);
        res.innerHTML = `<div class="muted small">${t("Recherche…")}</div>`;
        const list = await api("GET", "/api/mb/recordings?" + qs({ artist: vals[path].artist || tk.guess.artist || "", title: vals[path].title || tk.guess.title || "" }));
        if (!list.length) { res.innerHTML = `<div class="muted small">${t("Aucun résultat")}</div>`; return; }
        res.innerHTML = list.slice(0, 8).map((r, i) => `
          <div class="mb-result" data-i="${i}">
            <div><b>${esc(r.title)}</b> — ${esc(r.artist)} ${r.disambiguation ? `<span class="muted">(${esc(r.disambiguation)})</span>` : ""}
              <div class="small muted">${esc(r.year)} · ${esc(r.releases.join(" / "))}</div></div>
            <div class="small" style="text-align:right"><span class="${Math.abs(r.length - tk.duration) <= 3 ? "conf-high" : "muted"}">${r.length ? fmtDur(r.length) : "?"}</span><div class="muted">score ${r.score}</div></div>
          </div>`).join("");
        for (const el of $$(".mb-result", res)) el.onclick = () => {
          const r = list[Number(el.dataset.i)];
          vals[path].title = r.title; vals[path].artist = r.artist;
          extra[path] = { mb_trackid: r.id, ...(r.artist_id ? { mb_artistid: r.artist_id } : {}) };
          renderTracks();
          toast(t("Valeurs MusicBrainz reprises — pensez à enregistrer"));
        };
      });
    }
  }
  for (const b of $$(".seg button", root)) b.onclick = () => { view = b.dataset.view; renderTracks(); };
  renderTracks();

  const fill = (overwrite) => {
    for (const tk of tracks) for (const f of TRACK_FIELDS) {
      if (tk.guess[f] && (overwrite || !vals[tk.path][f])) vals[tk.path][f] = tk.guess[f];
    }
    renderTracks();
  };
  $(".ed-fill-empty", root).onclick = () => fill(false);
  $(".ed-fill-all", root).onclick = () => fill(true);
  $(".ed-artist-aa", root).onclick = () => {
    const aa = albumArtist();
    if (!aa) return toast(t("Album artist vide"), true);
    for (const tk of tracks) vals[tk.path].artist = aa;
    renderTracks();
  };

  const FIELD_LABEL = { albumartist: t("album artist"), album: t("album"), year: t("année"), genre: t("genre") };
  function payload() {
    const albumFields = {};
    for (const inp of $$("[data-album]", root)) {
      const k = inp.dataset.album, v = inp.value.trim();
      if (v || dirty.has(k)) albumFields[k] = v;
    }
    albumFields.compilation = $(".ed-compil", root).checked ? "1" : "";
    const edits = {};
    for (const tk of tracks) {
      const ch = trackDiff(tk.path);
      if (Object.keys(ch).length) edits[tk.path] = ch;
    }
    return { albumFields, edits };
  }
  /** Labels of what save() would actually change in the files ([] = nothing pending). */
  function pending() {
    const { albumFields, edits } = payload();
    const out = [];
    for (const [k, v] of Object.entries(albumFields)) {
      if (k === "compilation") {
        if (tracks.some((tk) => !!tk.compilation !== (v === "1"))) out.push(t("compilation"));
      } else if (tracks.some((tk) => (tk[k] || "") !== v)) out.push(FIELD_LABEL[k] || k);
    }
    const n = Object.keys(edits).length;
    if (n) out.push(t("{n} piste(s)", { n }));
    return out;
  }
  async function save() {
    const { albumFields, edits } = payload();
    return api("POST", "/api/album/save", { dir, album: albumFields, tracks: edits });
  }

  if (opts.onClose) $(".ed-close", root).onclick = opts.onClose;
  $(".ed-delete", root).onclick = async () => {
    if (!await deleteAlbum(dir, a)) return;
    if (opts.onDeleted) opts.onDeleted(); else if (opts.onClose) opts.onClose();
  };
  for (const l of $$("[data-open]", root)) l.onclick = (e) => { e.preventDefault(); openAlbum(l.dataset.open, drawerOnClose); };
  for (const b of $$("[data-browse]", root)) b.onclick = () => browseFolder(b.dataset.browse);
  for (const b of $$("[data-ignore],[data-unignore]", root)) b.onclick = () => run(async () => {
    const kind = b.dataset.ignore || b.dataset.unignore;
    await api("POST", "/api/ignore", { dirs: [dir], kind, value: !!b.dataset.ignore });
    toast("OK"); refreshStatus(); opts.onReload && opts.onReload();
  });
  if ($(".ed-save", root)) $(".ed-save", root).onclick = () => run(async () => {
    const r = await save();
    toast(t("{n} fichier(s) modifié(s)", { n: r.files }));
    refreshStatus();
    opts.onReload && opts.onReload();
  });
  const reloadAfterMb = () => { refreshStatus(); if (opts.onReload) opts.onReload(); else albumEditor(root, dir, opts); };
  $(".ed-mb-open", root).onclick = () => mbPanel(root, dir, tracks, { artist: albumArtist(), album: $('[data-album="album"]', root).value },
    reloadAfterMb, { pending, save });

  return { save, pending, album: a };
}

/** Ask, then move the folder's files to the trash. Resolves to true once deleted. */
async function deleteAlbum(dir, a) {
  const what = a ? t("{n} pistes, {size}", { n: a.n_tracks, size: fmtSize(a.total_size) }) : t("fichiers illisibles compris");
  if (!confirm(t("confirm.delete", { dir: dir || "/", what }))) return false;
  const r = await run(() => api("POST", "/api/album/delete", { dirs: [dir] }));
  if (r.errors.length) { toast(tr(r.errors[0]), true); return false; }
  toast(t("Album supprimé — annulable depuis l'historique tant que la corbeille n'est pas vidée"));
  refreshStatus();
  return true;
}

// ------------------------------------------------------------ musicbrainz
function mbPanel(root, dir, tracks, q, onApplied, editor) {
  const box = $(".ed-mb", root);
  box.classList.remove("hidden");
  box.innerHTML = `
    <div class="toolbar">
      <b>MusicBrainz</b>
      <input class="mb-artist" placeholder="${t("Artiste")}" value="${esc(q.artist)}" style="width:220px">
      <input class="mb-album" placeholder="${t("Album")}" value="${esc(q.album)}" style="width:260px">
      <button class="primary mb-search">${t("Rechercher")}</button>
    </div>
    <div class="mb-results"></div><div class="mb-match"></div>`;
  box.scrollIntoView({ behavior: "smooth" });
  const search = () => run(async () => {
    const out = $(".mb-results", box);
    out.innerHTML = `<div class="muted">${t("Recherche par nom et par durées des pistes…")}</div>`;
    const artist = $(".mb-artist", box).value.trim(), album = $(".mb-album", box).value.trim();
    // Both lookups at once: by name, and by track lengths + order (CD table of contents).
    const [byName, byDur] = await Promise.all([
      artist || album ? api("GET", "/api/mb/search?" + qs({ artist, album, n: tracks.length })) : Promise.resolve([]),
      api("GET", "/api/mb/by-durations?" + qs({ dir })).catch(() => ({ releases: [], failed: true })),
    ]);
    const merged = new Map(byDur.releases.map((r) => [r.id, r]));
    for (const r of byName) merged.set(r.id, merged.has(r.id) ? { ...r, by_durations: true } : r);
    const res = [...merged.values()].sort((a, b) => (!!b.by_durations - !!a.by_durations) || (b.score - a.score));
    const nDur = byDur.releases.length;
    const durLine = byDur.failed ? `<span class="conf-low">${t("Recherche par durées indisponible (MusicBrainz ne répond pas)")}</span>`
      : nDur ? `<span class="conf-high">✓ ${t("{n} édition(s) CD dont les {k} durées correspondent, en tête de liste", { n: nDur, k: tracks.length })}</span>`
      : `<span class="muted">${t("Aucune édition CD ne correspond aux {k} durées (il faut exactement le même nombre de pistes que le CD).", { k: tracks.length })}</span>`;
    if (!res.length) { out.innerHTML = `<div class="small">${durLine}</div><div class="muted">${t("Aucun résultat")}</div>`; return; }
    out.innerHTML = `<div class="small" style="margin-bottom:6px">${durLine}</div>` + res.slice(0, 20).map((r) => `
      <div class="mb-result" data-id="${r.id}">
        <div>${r.by_durations ? `<span class="chip g-clean" title="${t("Nombre de pistes et durées identiques à ce dossier")}">${t("durées ✓")}</span> ` : ""}<b>${esc(r.title)}</b> — ${esc(r.artist)} ${r.disambiguation ? `<span class="muted">(${esc(r.disambiguation)})</span>` : ""}
          <div class="small muted">${esc(r.date)} ${esc(r.country)} · ${esc(r.format)} · ${esc(r.label)} · ${esc(r.status)}</div></div>
        <div class="small" style="text-align:right"><b class="${r.tracks === tracks.length ? "conf-high" : "conf-medium"}">${t("{n} pistes", { n: r.tracks })}</b><div class="muted">score ${r.score}</div></div>
      </div>`).join("");
    for (const el of $$(".mb-result", out)) el.onclick = () => {
      $$(".mb-result", out).forEach((x) => x.classList.remove("sel"));
      el.classList.add("sel");
      mbMatch(box, dir, tracks, el.dataset.id, onApplied, editor);
    };
  });
  $(".mb-search", box).onclick = search;
  for (const inp of $$(".mb-artist, .mb-album", box)) inp.onkeydown = (e) => e.key === "Enter" && search();
  search();          // durations work even when nothing is typed
}

async function mbMatch(panel, dir, tracks, releaseId, onApplied, editor) {
  const box = $(".mb-match", panel);
  box.innerHTML = `<div class="muted">${t("Chargement de la tracklist…")}</div>`;
  const { release: rel, mapping } = await run(() => api("GET", "/api/mb/match?" + qs({ dir, release: releaseId })));
  const byPath = Object.fromEntries(tracks.map((tk) => [tk.path, tk]));
  const opt = (i, sel) => `<option value="${i}" ${i === sel ? "selected" : ""}>${rel.tracks[i].discs > 1 ? rel.tracks[i].disc + "-" : ""}${rel.tracks[i].position}. ${esc(rel.tracks[i].title)} (${fmtDur(rel.tracks[i].length)})</option>`;
  box.innerHTML = `
    <h2>${esc(rel.albumartist)} — ${esc(rel.title)} <span class="muted small">${esc(rel.date)} · ${t("{n} pistes", { n: rel.tracks.length })}</span></h2>
    ${(() => {
      const diffs = mapping.filter((m) => m.index !== null && byPath[m.path].duration && rel.tracks[m.index].length)
        .map((m) => Math.abs(byPath[m.path].duration - rel.tracks[m.index].length));
      if (!diffs.length) return "";
      const avg = diffs.reduce((a, b) => a + b, 0) / diffs.length, max = Math.max(...diffs);
      const cls = max <= 3 ? "conf-high" : avg <= 8 ? "conf-medium" : "conf-low";
      const hint = max <= 3 ? t("même édition très probablement") : avg <= 8 ? t("même album, autre mastering ou édition possible") : t("durées éloignées : vérifiez l'édition");
      return `<p class="small ${cls}">${t("Durées : écart moyen {avg} s, maximum {max} s sur {n} piste(s) associée(s)", { avg: avg.toFixed(1), max: Math.round(max), n: diffs.length })} — ${hint}</p>`;
    })()}
    <table class="tracks"><thead><tr><th></th><th>${t("Fichier")}</th><th>${t("Durée")}</th><th>${t("Piste MusicBrainz")}</th><th>${t("Score")}</th></tr></thead>
    <tbody>${mapping.map((m) => `
      <tr data-path="${esc(m.path)}">
        <td>${playBtn(byPath[m.path])}</td>
        <td class="small mono">${esc(byPath[m.path].filename)}<div class="muted">${esc(byPath[m.path].title || "")}</div></td>
        <td class="small">${fmtDur(byPath[m.path].duration)}</td>
        <td><select class="map" style="width:100%"><option value="">— ${t("ne pas modifier")} —</option>${rel.tracks.map((_, i) => opt(i, m.index)).join("")}</select></td>
        <td class="small ${m.score >= 1 ? "conf-high" : m.score >= 0.7 ? "conf-medium" : "conf-low"}" title="${t("Correspondance titre + durée")}">${m.index === null ? "—" : Math.round(Math.min(m.score, 1) * 100) + " %"}</td>
      </tr>`).join("")}</tbody></table>
    <div class="actions">
      <label><input type="checkbox" class="mb-cover" ${rel.cover ? "checked" : "disabled"}> ${t("Intégrer la pochette dans les fichiers qui n'en ont pas")} ${rel.cover ? "" : t("(indisponible)")}</label>
      <span style="flex:1"></span>
      <button class="primary mb-apply">${t("Appliquer les tags MusicBrainz")}</button>
    </div>`;
  bindPlay(box);
  $(".mb-apply", box).onclick = () => run(async () => {
    const map = $$("tbody tr", box).map((row) => {
      const v = $(".map", row).value;
      return { path: row.dataset.path, index: v === "" ? null : Number(v) };
    });
    const n = map.filter((m) => m.index !== null).length;
    if (!n) return toast(t("Aucun fichier associé à une piste MusicBrainz : choisissez les correspondances dans le tableau"), true);
    const todo = editor ? editor.pending() : [];
    const msg = todo.length ? t("confirm.mb.pending", { list: todo.join(", "), n }) : t("confirm.mb", { n });
    if (!confirm(msg)) return;
    if (todo.length) await editor.save();
    const r = await api("POST", "/api/mb/apply", { dir, release: releaseId, mapping: map, cover: $(".mb-cover", box).checked });
    toast(t("{n} fichier(s) taggué(s)", { n: r.files }) + (r.cover ? " " + t("avec pochette") : ""));
    onApplied();
  });
}

// ------------------------------------------------------------------ review
const review = { kind: null, label: "", items: [], idx: 0, status: {}, returnTo: "#/" };
const REVIEW_STATUS = { done: "✓ " + t("validé"), saved: "✎ " + t("enregistré"), skip: "→ " + t("passé"), ignore: "⊘ " + t("ne plus proposer"), deleted: "✕ " + t("supprimé") };

async function startReview(kind, label, items, returnTo) {
  if (!items.length) return toast(t("Rien à revoir avec ce filtre"));
  Object.assign(review, { kind, label, items, idx: 0, status: {}, returnTo });
  location.hash = "#/review";
}

function reviewCounts() {
  const c = { done: 0, saved: 0, skip: 0, ignore: 0, deleted: 0 };
  Object.values(review.status).forEach((s) => c[s]++);
  return c;
}

const dupHeaders = () => `<th>${t("Dossier")}</th><th>${t("Pistes")}</th><th>${t("Bitrate")}</th><th>${t("Taille")}</th><th>${t("Pochette")}</th><th>${t("Tags")}</th><th>MBID</th><th>${t("Qualité")}</th>`;
const bestBadge = () => `<span class="badge" style="background:var(--ok-soft);color:var(--ok)">${t("meilleure")}</span>`;
const missing = (m) => m.n_untagged || m.n_incomplete ? `<span class="conf-low">${t("{n} manquant(s)", { n: m.n_untagged + m.n_incomplete })}</span>` : "✓";

async function renderReview(main) {
  if (!review.items.length) { location.hash = "#/"; return; }
  const n = review.items.length, i = review.idx;
  if (i >= n) {
    const c = reviewCounts();
    main.innerHTML = `<h1>${t("Revue terminée — {label}", { label: esc(review.label) })}</h1>
      <div class="cards" style="margin:16px 0">
        <div class="card"><div class="num">${c.done}</div><div class="lbl">${t("validés")}</div></div>
        <div class="card"><div class="num">${c.saved}</div><div class="lbl">${t("enregistrés")}</div></div>
        <div class="card"><div class="num">${c.skip}</div><div class="lbl">${t("passés")}</div></div>
        <div class="card"><div class="num">${c.ignore}</div><div class="lbl">${t("ne plus proposer")}</div></div>
        ${c.deleted ? `<div class="card"><div class="num">${c.deleted}</div><div class="lbl">${t("supprimés")}</div></div>` : ""}
      </div>
      <div class="toolbar"><button id="rv-prev">← ${t("Revenir au dernier")}</button><a class="btn primary" href="${review.returnTo}">${t("Retour à la liste")}</a></div>`;
    $("#rv-prev").onclick = () => { review.idx = n - 1; renderReview(main); };
    return;
  }
  const item = review.items[i];
  const st = review.status[i];
  const c = reviewCounts();
  main.innerHTML = `
    <div class="review-bar">
      <div class="review-top">
        <b>${t("Revue — {label}", { label: esc(review.label) })}</b>
        <span class="muted">${i + 1} / ${n}</span>
        <span class="small muted">✓ ${c.done} · ✎ ${c.saved} · → ${c.skip} · ⊘ ${c.ignore}${c.deleted ? ` · ✕ ${c.deleted}` : ""}</span>
        ${st ? `<span class="chip">${REVIEW_STATUS[st]}</span>` : ""}
        <span class="grow"></span>
        <a href="${review.returnTo}" class="small">${t("Quitter la revue")}</a>
      </div>
      <div class="progress"><div style="width:${Math.round((100 * i) / n)}%"></div></div>
      <div class="review-actions">
        <button id="rv-prev" ${i === 0 ? "disabled" : ""} title="Ctrl+←">← ${t("Précédent")}</button>
        <span class="grow"></span>
        <button id="rv-ignore" title="Ctrl+I">${t("Ne plus proposer")}</button>
        <button id="rv-skip" title="Ctrl+→">${t("Passer (ne pas traiter)")} →</button>
        ${review.kind === "duplicate" ? "" : `<button id="rv-save" title="${t("Ctrl+S — enregistre sans passer au suivant")}">✎ ${t("Enregistrer")}</button>`}
        <button class="primary" id="rv-ok" title="${t("Ctrl+Entrée")}">✓ ${review.kind === "duplicate" ? t("Garder la version choisie et suivant") : t("Valider et suivant")}</button>
      </div>
    </div>
    <div id="rv-body"><div class="empty">${t("Chargement…")}</div></div>`;

  const go = (delta) => { review.idx = Math.max(0, i + delta); renderReview(main); window.scrollTo(0, 0); };
  let validate, ignore;
  const body = $("#rv-body");

  if (review.kind === "duplicate") {
    const g = item;
    let keep = g.keep || g.best;
    body.innerHTML = `
      <p class="lead">${t("Choisissez la version à garder ; les autres iront dans la corbeille (annulable depuis l'historique).")}</p>
      <div class="group"><table><thead><tr><th>${t("Garder")}</th><th></th>${dupHeaders()}</tr></thead>
      <tbody>${g.members.map((m) => `
        <tr class="${m.dir === keep ? "keep" : "remove"}" data-dir="${esc(m.dir)}">
          <td><input type="radio" name="rv-keep" value="${esc(m.dir)}" ${m.dir === keep ? "checked" : ""}></td>
          <td><button class="play-first" title="${t("Écouter la 1re piste")}">▶</button></td>
          <td><a href="#" class="open">${esc(m.dir)}</a> ${m.dir === g.best ? bestBadge() : ""}</td>
          <td>${m.n_tracks}</td><td>${m.avg_bitrate}${m.vbr ? " VBR" : ""}</td><td>${fmtSize(m.total_size)}</td>
          <td>${m.has_cover ? "✓" : "—"}</td><td>${missing(m)}</td>
          <td>${m.has_mbid ? "✓" : "—"}</td><td class="q">${m.quality}</td>
        </tr>`).join("")}</tbody></table></div>`;
    for (const r of $$("input[type=radio]", body)) r.onchange = () => {
      keep = g.keep = r.value;
      for (const row of $$("tbody tr", body)) row.className = row.dataset.dir === keep ? "keep" : "remove";
    };
    for (const a of $$("a.open", body)) a.onclick = (e) => { e.preventDefault(); openAlbum(a.closest("tr").dataset.dir); };
    for (const b of $$(".play-first", body)) b.onclick = () => run(async () => {
      const dir = b.closest("tr").dataset.dir;
      const d = await api("GET", "/api/album?" + qs({ dir }));
      if (d.tracks[0]) playTrack(d.tracks[0].path, `${dir} — ${d.tracks[0].title || d.tracks[0].filename}`);
    });
    validate = () => api("POST", "/api/duplicates/resolve", { sync: true, groups: [{ keep, remove: g.members.map((m) => m.dir).filter((x) => x !== keep) }] });
    ignore = () => api("POST", "/api/ignore", { dirs: g.members.map((m) => m.dir), kind: "duplicate", value: true });
  } else {
    const ed = await albumEditor(body, item, {
      review: true, onReload: () => renderReview(main),
      onDeleted: () => { review.status[i] = "deleted"; go(1); },
    });
    validate = () => ed.save();
    $("#rv-save").onclick = () => run(async () => {
      if (!ed.pending().length) return toast(t("Rien à enregistrer"));
      const btns = $$(".review-actions button", main);
      btns.forEach((b) => (b.disabled = true));
      try {
        const r = await ed.save();
        review.status[i] = review.status[i] || "saved";
        toast(t("{n} fichier(s) enregistré(s) — vous restez sur cet album", { n: r.files }));
        refreshStatus();
        const y = window.scrollY;
        await renderReview(main);          // reload from the files, same album
        window.scrollTo(0, y);
      } finally {
        btns.forEach((b) => (b.disabled = false));
      }
    });
    ignore = () => api("POST", "/api/ignore", { dirs: [item], kind: review.kind, value: true });
  }

  const act = (fn, statusKey) => run(async () => {
    for (const b of $$(".review-actions button", main)) b.disabled = true;
    try {
      if (fn) await fn();
      review.status[i] = statusKey;
      refreshStatus();
      go(1);
    } finally {
      for (const b of $$(".review-actions button", main)) b.disabled = false;
    }
  });
  $("#rv-prev").onclick = () => go(-1);
  $("#rv-skip").onclick = () => act(null, "skip");
  $("#rv-ignore").onclick = () => act(ignore, "ignore");
  $("#rv-ok").onclick = () => act(validate, "done");
}

document.addEventListener("keydown", (e) => {
  if (currentView() !== "review" || !e.ctrlKey || !$("#drawer").classList.contains("hidden")) return;
  const map = { Enter: "#rv-ok", ArrowRight: "#rv-skip", ArrowLeft: "#rv-prev", i: "#rv-ignore", I: "#rv-ignore", s: "#rv-save", S: "#rv-save" };
  const btn = map[e.key] && $(map[e.key]);
  if (btn && !btn.disabled) { e.preventDefault(); btn.click(); }
});

// -------------------------------------------------------------- duplicates
const dupState = { q: "", offset: 0, showIgnored: false, keep: {}, selected: new Set() };

async function renderDuplicates(main) {
  main.innerHTML = `
    <h1>${t("Doublons d'albums")}</h1>
    <p class="lead">${t("lead.duplicates")}</p>
    <div class="toolbar">
      <input type="search" id="dq" placeholder="${t("Filtrer…")}" value="${esc(dupState.q)}" style="width:300px">
      <label><input type="checkbox" id="dign" ${dupState.showIgnored ? "checked" : ""}> ${t("afficher les ignorés")}</label>
      <span class="grow"></span>
      <button class="primary" id="dreview">▶ ${t("Revue groupe par groupe")}</button>
    </div>
    <div id="dbatch"></div><div id="dlist"><div class="empty">${t("Chargement…")}</div></div>`;
  let timer;
  $("#dq").oninput = (e) => { clearTimeout(timer); timer = setTimeout(() => { dupState.q = e.target.value; dupState.offset = 0; loadDuplicates(); }, 300); };
  $("#dign").onchange = (e) => { dupState.showIgnored = e.target.checked; loadDuplicates(); };
  $("#dreview").onclick = () => run(async () => {
    const all = (await api("GET", "/api/duplicates?" + qs({ q: dupState.q, show_ignored: dupState.showIgnored, limit: 100000 }))).items;
    const sel = dupState.selected.size ? all.filter((g) => dupState.selected.has(g.id)) : all;
    startReview("duplicate", t("Doublons") + (dupState.selected.size ? " " + t("(sélection)") : ""), sel, "#/duplicates");
  });
  loadDuplicates();
}

async function loadDuplicates() {
  const data = await run(() => api("GET", "/api/duplicates?" + qs({ q: dupState.q, show_ignored: dupState.showIgnored, offset: dupState.offset, limit: 50 })));
  const box = $("#dlist");
  if (!box) return;
  const groups = data.items;
  if (!groups.length) { box.innerHTML = `<div class="empty">${t("Aucun doublon détecté")}</div>`; $("#dbatch").innerHTML = ""; return; }
  box.innerHTML = groups.map((g) => {
    const keep = dupState.keep[g.id] || g.best;
    const m0 = g.members.find((m) => m.dir === keep) || g.members[0];
    return `<div class="group" data-gid="${g.id}">
      <div class="group-head">
        <input type="checkbox" class="gsel" ${dupState.selected.has(g.id) ? "checked" : ""}>
        <b>${esc(m0.main_artist || "?")} — ${esc(m0.main_album || "?")}</b>
        <span class="muted small">${t("{n} versions", { n: g.members.length })}</span>
        <span style="flex:1"></span>
        <button class="link gignore">${g.members.every((m) => m.ignored.includes("duplicate")) ? t("Ne plus ignorer") : t("Pas des doublons (ignorer)")}</button>
      </div>
      <table><thead><tr><th>${t("Garder")}</th>${dupHeaders()}</tr></thead>
      <tbody>${g.members.map((m) => `
        <tr class="${m.dir === keep ? "keep" : "remove"}" data-dir="${esc(m.dir)}">
          <td><input type="radio" name="keep-${g.id}" value="${esc(m.dir)}" ${m.dir === keep ? "checked" : ""}></td>
          <td><a href="#" class="open">${esc(m.dir)}</a> ${m.dir === g.best ? bestBadge() : ""}</td>
          <td>${m.n_tracks}</td>
          <td>${m.avg_bitrate}${m.vbr ? " VBR" : ""}</td>
          <td>${fmtSize(m.total_size)}</td>
          <td>${m.has_cover ? "✓" : "—"}</td>
          <td>${missing(m)}</td>
          <td>${m.has_mbid ? "✓" : "—"}</td>
          <td class="q">${m.quality}</td>
        </tr>`).join("")}</tbody></table>
    </div>`;
  }).join("") + `
    <div class="pager">
      <span class="muted">${t("{from}–{to} sur {total}", { from: dupState.offset + 1, to: dupState.offset + groups.length, total: data.total })}</span>
      <button id="dprev" ${dupState.offset === 0 ? "disabled" : ""}>‹ ${t("Précédent")}</button>
      <button id="dnext" ${dupState.offset + 50 >= data.total ? "disabled" : ""}>${t("Suivant")} ›</button>
    </div>`;
  $("#dprev").onclick = () => { dupState.offset -= 50; loadDuplicates(); window.scrollTo(0, 0); };
  $("#dnext").onclick = () => { dupState.offset += 50; loadDuplicates(); window.scrollTo(0, 0); };
  for (const el of $$(".group", box)) {
    const g = groups.find((x) => String(x.id) === el.dataset.gid);
    $(".gsel", el).onchange = (e) => { e.target.checked ? dupState.selected.add(g.id) : dupState.selected.delete(g.id); renderDupBatch(groups); };
    for (const r of $$("input[type=radio]", el)) r.onchange = () => {
      dupState.keep[g.id] = r.value;
      for (const row of $$("tbody tr", el)) row.className = row.dataset.dir === r.value ? "keep" : "remove";
    };
    for (const a of $$("a.open", el)) a.onclick = (e) => { e.preventDefault(); openAlbum(a.closest("tr").dataset.dir, loadDuplicates); };
    $(".gignore", el).onclick = () => run(async () => {
      const unignore = g.members.every((m) => m.ignored.includes("duplicate"));
      await api("POST", "/api/ignore", { dirs: g.members.map((m) => m.dir), kind: "duplicate", value: !unignore });
      loadDuplicates(); refreshStatus();
    });
  }
  renderDupBatch(groups);
}

function renderDupBatch(groups) {
  const n = dupState.selected.size;
  $("#dbatch").innerHTML = `<div class="batchbar">
    <b>${t("{n} groupe(s) sélectionné(s)", { n })}</b>
    <button class="link" id="dselpage">${t("Sélectionner les groupes de la page")}</button>
    ${n ? `<button class="link" id="dselnone">${t("Désélectionner")}</button>` : ""}
    <span style="flex:1"></span>
    <button class="danger solid" id="dresolve" ${n ? "" : "disabled"}>${t("Garder la version choisie, mettre les autres à la corbeille")}</button>
  </div>`;
  $("#dselpage").onclick = () => { groups.forEach((g) => dupState.selected.add(g.id)); loadDuplicates(); };
  if ($("#dselnone")) $("#dselnone").onclick = () => { dupState.selected.clear(); loadDuplicates(); };
  $("#dresolve").onclick = () => run(async () => {
    // Groups may span several pages: fetch the ones not on screen.
    const all = (await api("GET", "/api/duplicates?" + qs({ show_ignored: true, limit: 100000 }))).items;
    const payload = all.filter((g) => dupState.selected.has(g.id)).map((g) => {
      const keep = dupState.keep[g.id] || g.best;
      return { keep, remove: g.members.map((m) => m.dir).filter((d) => d !== keep) };
    });
    const nd = payload.reduce((s, g) => s + g.remove.length, 0);
    if (!confirm(t("confirm.dupes", { n: nd, k: payload.length }))) return;
    await api("POST", "/api/duplicates/resolve", { groups: payload });
    dupState.selected.clear(); dupState.keep = {};
    lastJobStatus = "running"; refreshStatus();
  });
}

// ------------------------------------------------------------------- genres
const G_INFER = "__infer__", G_REMOVE = "__remove__", G_KEEP = "__keep__";
const G_STATUS = { map: t("à harmoniser"), junk: t("générique / parasite"), weird: t("farfelu"), empty: t("sans genre"), clean: t("propre") };
const G_FILTERS = { todo: t("À traiter"), weird: t("Farfelus / génériques"), empty: t("Sans genre"), clean: t("Déjà propres"), all: t("Tous") };
const genreState = { filter: "todo", q: "", selected: new Set(), choice: {}, data: null };
let genreCanon = [];

function genreTargetLabel(target) {
  if (target === G_INFER) return t("Genre habituel de l'artiste");
  if (target === G_REMOVE) return t("Supprimer le genre");
  if (target === G_KEEP) return t("Laisser tel quel");
  return target;
}

async function renderGenres(main) {
  main.innerHTML = `
    <h1>${t("Genres")}</h1>
    <p class="lead">${t("lead.genres")}</p>
    <div class="panel">
      <div class="toolbar" style="margin-bottom:6px"><b>${t("Genres cibles")}</b><span class="grow"></span>
        <input id="g-add" placeholder="${t("Ajouter un genre…")}" style="width:200px"><button id="g-add-btn">${t("Ajouter")}</button></div>
      <div class="chips" id="g-canon"></div>
    </div>
    <div class="toolbar">
      <div class="seg" id="g-filter">${Object.entries(G_FILTERS).map(([k, v]) => `<button data-f="${k}">${v}</button>`).join("")}</div>
      <input type="search" id="g-q" placeholder="${t("Filtrer…")}" value="${esc(genreState.q)}" style="width:240px">
      <span class="grow"></span>
      <button id="g-mb" title="${t("Pour les artistes dont aucun morceau n'a de genre exploitable")}">${t("Chercher les genres inconnus sur MusicBrainz")}</button>
    </div>
    <div id="g-batch"></div>
    <div id="g-list"><div class="empty">${t("Chargement…")}</div></div>`;
  let timer;
  $("#g-q").oninput = (e) => { clearTimeout(timer); timer = setTimeout(() => { genreState.q = e.target.value; drawGenres(); }, 200); };
  for (const b of $$("#g-filter button")) b.onclick = () => { genreState.filter = b.dataset.f; genreState.selected.clear(); drawGenres(); };
  const addCanon = () => run(async () => {
    const v = $("#g-add").value.trim();
    if (!v) return;
    genreCanon = (await api("PUT", "/api/genres/canon", { genres: [...genreCanon, v] })).canon;
    $("#g-add").value = ""; loadGenres();
  });
  $("#g-add-btn").onclick = addCanon;
  $("#g-mb").onclick = () => confirm(t("confirm.genres.mb")) &&
    run(async () => { await api("POST", "/api/genres/musicbrainz"); lastJobStatus = "running"; refreshStatus(); });
  $("#g-add").onkeydown = (e) => e.key === "Enter" && addCanon();
  loadGenres();
}

async function loadGenres() {
  const data = await run(() => api("GET", "/api/genres"));
  genreState.data = data;
  genreCanon = data.canon;
  $("#g-canon").innerHTML = genreCanon.map((g) => `<span class="chip canon">${esc(g)} <button class="link g-rm" data-g="${esc(g)}" title="${t("Retirer")}">✕</button></span>`).join("");
  for (const b of $$(".g-rm")) b.onclick = () => run(async () => {
    if (!confirm(t("Retirer « {g} » des genres cibles ? (les fichiers ne sont pas modifiés)", { g: b.dataset.g }))) return;
    genreCanon = (await api("PUT", "/api/genres/canon", { genres: genreCanon.filter((x) => x !== b.dataset.g) })).canon;
    loadGenres();
  });
  drawGenres();
}

function genreRows() {
  const { data, filter, q } = genreState;
  return data.items.filter((r) => {
    if (q && !fold(r.raw + " " + r.artists.join(" ")).includes(fold(q))) return false;
    if (filter === "todo") return r.status !== "clean" && r.proposal !== G_KEEP;
    if (filter === "weird") return r.status === "weird" || r.status === "junk";
    if (filter === "empty") return r.status === "empty";
    if (filter === "clean") return r.status === "clean" || r.proposal === G_KEEP;
    return true;
  });
}

function drawGenres() {
  for (const b of $$("#g-filter button")) b.classList.toggle("on", b.dataset.f === genreState.filter);
  const rows = genreRows();
  const box = $("#g-list");
  if (!box) return;
  if (!rows.length) { box.innerHTML = `<div class="empty">${t("Rien ici")}</div>`; drawGenreBatch(rows); return; }
  const choiceOf = (r) => genreState.choice[r.raw] ?? r.proposal;
  const options = (r) => {
    const sel = choiceOf(r);
    const inf = r.inferred.filter(([g]) => g !== "?").map(([g, n]) => `${g} ${n}`).join(", ");
    const canon = genreCanon.includes(sel) || [G_INFER, G_REMOVE, G_KEEP].includes(sel) ? genreCanon : [sel, ...genreCanon];
    return `<option value="${G_INFER}" ${sel === G_INFER ? "selected" : ""}>↳ ${t("Genre habituel de l'artiste")}${inf ? ` (${esc(inf)})` : " " + t("(inconnu)")}</option>
      ${canon.map((g) => `<option value="${esc(g)}" ${g === sel ? "selected" : ""}>${esc(g)}</option>`).join("")}
      <option value="${G_REMOVE}" ${sel === G_REMOVE ? "selected" : ""}>✕ ${t("Supprimer le genre")}</option>
      <option value="${G_KEEP}" ${sel === G_KEEP ? "selected" : ""}>= ${t("Laisser tel quel")}</option>`;
  };
  const noGenre = `<span class="muted">${t("(aucun genre)")}</span>`;
  box.innerHTML = `<table>
    <thead><tr><th class="check"><input type="checkbox" id="g-all" title="${t("Tout sélectionner")}"></th>
      <th>${t("Genre actuel")}</th><th>${t("Pistes")}</th><th>${t("Dossiers")}</th><th>${t("Artistes")}</th><th style="width:320px">${t("Remplacer par")}</th><th></th></tr></thead>
    <tbody>${rows.slice(0, 600).map((r) => `
      <tr data-raw="${esc(r.raw)}">
        <td class="check"><input type="checkbox" class="g-sel" ${genreState.selected.has(r.raw) ? "checked" : ""}></td>
        <td><b>${r.raw ? esc(r.raw) : noGenre}</b>
          <div><span class="chip g-${r.status}">${G_STATUS[r.status]}</span> <span class="small muted">${esc(tr(r.reason))}</span>
          ${r.user_choice ? `<span class="chip">${t("votre choix")}</span>` : ""}</div></td>
        <td>${fmtNum(r.tracks)}</td><td>${r.dirs}</td>
        <td class="small">${r.artists.map(esc).join(", ")}</td>
        <td><select class="g-target" style="width:100%">${options(r)}</select></td>
        <td style="white-space:nowrap;text-align:right">
          <button class="link g-detail">${t("Dossiers")}</button>
          <button class="g-apply">${t("Appliquer")}</button></td>
      </tr><tr class="hidden"><td colspan="7" class="g-detail-box"></td></tr>`).join("")}
    </tbody></table>
    ${rows.length > 600 ? `<div class="muted small">… ${t("{n} valeurs de plus, affinez le filtre", { n: rows.length - 600 })}</div>` : ""}`;
  $("#g-all").onchange = (e) => { rows.forEach((r) => e.target.checked ? genreState.selected.add(r.raw) : genreState.selected.delete(r.raw)); drawGenres(); };
  for (const row of $$("tr[data-raw]", box)) {
    const raw = row.dataset.raw, r = rows.find((x) => x.raw === raw), next = row.nextElementSibling;
    $(".g-sel", row).onchange = (e) => { e.target.checked ? genreState.selected.add(raw) : genreState.selected.delete(raw); drawGenreBatch(rows); };
    $(".g-target", row).onchange = (e) => { genreState.choice[raw] = e.target.value; };
    $(".g-apply", row).onclick = () => applyGenres([{ raw, target: choiceOf(r) }]);
    $(".g-detail", row).onclick = () => run(async () => {
      if (!next.classList.contains("hidden")) return next.classList.add("hidden");
      const dirs = await api("GET", "/api/genres/detail?" + qs({ raw }));
      $(".g-detail-box", next).innerHTML = `<table class="tracks"><thead><tr><th>${t("Dossier")}</th><th>${t("Artiste")}</th><th>${t("Pistes")}</th><th>${t("Genre habituel de l'artiste")}</th></tr></thead><tbody>
        ${dirs.slice(0, 300).map((d) => `<tr><td><a href="#" data-open="${esc(d.dir)}">${esc(d.dir)}</a></td><td>${esc(d.artist || "")}</td><td>${d.tracks}</td>
          <td>${d.inferred === "?" ? `<span class="muted">${t("inconnu")}</span>` : esc(d.inferred)}</td></tr>`).join("")}</tbody></table>
        <div class="small muted">${t("Pour traiter un album à part, ouvrez-le et modifiez son champ Genre.")}</div>`;
      for (const a of $$("[data-open]", next)) a.onclick = (e) => { e.preventDefault(); openAlbum(a.dataset.open, loadGenres); };
      next.classList.remove("hidden");
    });
  }
  drawGenreBatch(rows);
}

function drawGenreBatch(rows) {
  const sel = rows.filter((r) => genreState.selected.has(r.raw));
  const n = sel.reduce((s, r) => s + r.tracks, 0);
  $("#g-batch").innerHTML = `<div class="batchbar">
    <b>${t("{n} genre(s) sélectionné(s)", { n: sel.length })}</b> <span class="muted">${t("{n} pistes", { n: fmtNum(n) })}</span>
    <span style="flex:1"></span>
    <button class="primary" id="g-apply-sel" ${sel.length ? "" : "disabled"}>${t("Appliquer le remplacement choisi")}</button></div>`;
  $("#g-apply-sel").onclick = () => applyGenres(sel.map((r) => ({ raw: r.raw, target: genreState.choice[r.raw] ?? r.proposal })));
}

function applyGenres(items) {
  const txt = items.slice(0, 8).map((i) => `• ${i.raw || t("(aucun genre)")} → ${genreTargetLabel(i.target)}`).join("\n") +
    (items.length > 8 ? "\n" + t("… et {n} autres", { n: items.length - 8 }) : "");
  if (!confirm(`${t("Appliquer ?")}\n\n${txt}`)) return;
  run(async () => {
    const r = await api("POST", "/api/genres/apply", { items });
    genreState.selected.clear();
    if (r.status === "done") { toast(t("Choix mémorisé")); loadGenres(); return; }
    lastJobStatus = "running"; refreshStatus();
  });
}

// ----------------------------------------------------------------- autotag
const AT_STATUS = {
  ok: [t("à appliquer"), "g-clean"], ambiguous: [t("ambigu"), "g-weird"], partial: [t("incomplet"), "g-weird"],
  none: [t("aucun résultat"), "g-empty"], error: [t("erreur"), "g-empty"], applied: [t("appliqué"), "g-clean"],
  rejected: [t("rejeté : nombre de pistes"), "g-empty"],
};
const AT_INACTIVE = ["none", "error", "applied", "rejected"];

/** Proposal to show / apply for a row, depending on the "respect the track count" option. */
function atView(r) {
  const o = atState.options;
  if (["applied", "none", "error"].includes(r.status) || !o.strict_count) return { status: r.status, score: r.score, d: r.details };
  const strict = r.details.strict;
  if (strict) return { status: strict.status, score: strict.score, d: { hints: r.details.hints, ...strict.details } };
  const rel = r.details.release;          // analysis made before the option existed
  if (rel && rel.tracks === r.n_mapped) return { status: r.status, score: r.score, d: r.details };
  return { status: "rejected", score: 0, d: { reason: "nombre de pistes différent (réanalyser pour chercher une autre édition)" } };
}
const AT_FILTERS = { ready: t("Prêts (≥ note min.)"), below: t("Sous le seuil"), doubt: t("Ambigus / incomplets"), none: t("Sans résultat"), applied: t("Appliqués"), all: t("Tous") };
const atState = { filter: "ready", selected: null, options: null, data: null, sig: "" };

async function startAutotag(dirs, label) {
  if (!dirs.length) return toast(t("Aucun dossier à analyser"));
  const min = Math.ceil((dirs.length * 5) / 60);
  if (!confirm(t("confirm.autotag", { n: dirs.length, label, min }))) return;
  await run(() => api("POST", "/api/autotag/analyse", { dirs }));
  lastJobStatus = "running"; refreshStatus();
  atState.selected = null;
  location.hash = "#/autotag";
}

function atDefaultSelection(items, o) {
  return new Set(items.filter((r) => { const v = atView(r); return v.status === "ok" && v.score >= o.min_score; }).map((r) => r.dir));
}

async function renderAutotag(main) {
  const data = await run(() => api("GET", "/api/autotag"));
  atState.data = data;
  atState.options = atState.options || { ...data.options };
  const o = atState.options;
  // New or updated analysis results -> start again from the default selection.
  const sig = data.items.map((r) => r.dir + r.analyzed + r.status).join("|");
  if (!atState.selected || atState.sig !== sig) atState.selected = atDefaultSelection(data.items, o);
  atState.sig = sig;
  main.innerHTML = `
    <h1>${t("Tag auto")}</h1>
    <p class="lead">${t("lead.autotag")}</p>
    <div class="panel at-options">
      <label>${t("Note minimale")} <input type="number" id="at-min" min="50" max="100" step="1" value="${o.min_score}" style="width:70px"></label>
      <fieldset><legend>${t("Album artist")}</legend>
        <label><input type="radio" name="at-aa" value="mb" ${o.albumartist_mode === "mb" ? "checked" : ""}> ${t("celui de MusicBrainz")}</label>
        <label><input type="radio" name="at-aa" value="folder" ${o.albumartist_mode === "folder" ? "checked" : ""}> ${t("l'artiste du dossier")}</label>
        <label><input type="radio" name="at-aa" value="fixed" ${o.albumartist_mode === "fixed" ? "checked" : ""}> ${t("imposé :")}</label>
        <input id="at-aa-value" value="${esc(o.albumartist_value)}" placeholder="${t("ex. Various Artists")}" style="width:190px">
      </fieldset>
      <label><input type="checkbox" id="at-genre" ${o.fill_genre ? "checked" : ""}> ${t("Compléter le genre vide (genre habituel de l'artiste)")}</label>
      <label><input type="checkbox" id="at-cover" ${o.cover ? "checked" : ""}> ${t("Intégrer la pochette si absente")}</label>
      <label><input type="checkbox" id="at-empty" ${o.only_empty ? "checked" : ""}> ${t("Ne remplir que les champs vides")}</label>
      <label title="${esc(t("hint.strict"))}"><input type="checkbox" id="at-strict" ${o.strict_count ? "checked" : ""}> ${t("Respecter le nombre de pistes")}</label>
    </div>
    <div class="toolbar"><div class="seg" id="at-filter">${Object.entries(AT_FILTERS).map(([k, v]) => `<button data-f="${k}">${v}</button>`).join("")}</div></div>
    <div id="at-batch"></div>
    <div id="at-list"></div>`;
  const readOptions = () => {
    o.min_score = Number($("#at-min").value) || 95;
    o.albumartist_mode = ($('input[name="at-aa"]:checked') || {}).value || "mb";
    o.albumartist_value = $("#at-aa-value").value;
    o.fill_genre = $("#at-genre").checked; o.cover = $("#at-cover").checked; o.only_empty = $("#at-empty").checked;
    o.strict_count = $("#at-strict").checked;
  };
  for (const el of $$(".at-options input")) el.onchange = () => {
    const before = [o.min_score, o.strict_count].join();
    readOptions();
    if ([o.min_score, o.strict_count].join() !== before) atState.selected = atDefaultSelection(data.items, o);
    drawAutotag();
  };
  for (const b of $$("#at-filter button")) b.onclick = () => { atState.filter = b.dataset.f; drawAutotag(); };
  drawAutotag();
}

function atRows() {
  const { data, options: o, filter } = atState;
  return data.items.filter((r) => {
    const v = atView(r);
    if (filter === "ready") return v.status === "ok" && v.score >= o.min_score;
    if (filter === "below") return v.status === "ok" && v.score < o.min_score;
    if (filter === "doubt") return v.status === "ambiguous" || v.status === "partial";
    if (filter === "none") return ["none", "error", "rejected"].includes(v.status);
    if (filter === "applied") return v.status === "applied";
    return true;
  });
}

function drawAutotag() {
  for (const b of $$("#at-filter button")) b.classList.toggle("on", b.dataset.f === atState.filter);
  const rows = atRows(), box = $("#at-list");
  if (!atState.data.items.length) {
    box.innerHTML = `<div class="empty">${t("empty.autotag")}</div>`;
    drawAutotagBatch(rows); return;
  }
  if (!rows.length) { box.innerHTML = `<div class="empty">${t("Rien dans cette catégorie")}</div>`; drawAutotagBatch(rows); return; }
  const pct = (v) => v === null || v === undefined ? "—" : v + " %";
  box.innerHTML = `<table><thead><tr><th class="check"><input type="checkbox" id="at-all"></th>
      <th>${t("Dossier")}</th><th>${t("Proposition MusicBrainz")}</th><th>${t("Note")}</th><th>${t("Détail")}</th><th>${t("Statut")}</th></tr></thead><tbody>
    ${rows.map((r) => {
      const v = atView(r), d = v.d, rel = v.status === "rejected" ? null : d.release;
      const cls = v.score >= atState.options.min_score ? "conf-high" : v.score >= 80 ? "conf-medium" : "conf-low";
      const discTracks = d.medium_tracks || (rel && rel.tracks);
      const countCls = discTracks && discTracks !== r.n_tracks ? "conf-medium" : "";
      return `<tr data-dir="${esc(r.dir)}">
        <td class="check"><input type="checkbox" class="at-sel" ${atState.selected.has(r.dir) ? "checked" : ""} ${AT_INACTIVE.includes(v.status) ? "disabled" : ""}></td>
        <td><a href="#" class="open dir">${esc(r.dir)}</a><div class="small muted">${t("{n} pistes", { n: r.n_tracks || "?" })}</div></td>
        <td>${rel ? `<a href="https://musicbrainz.org/release/${esc(rel.id)}" target="_blank" rel="noopener">${esc(rel.artist)} — ${esc(rel.title)}${newWindow()}</a>
          <div class="small muted">${esc(rel.date || "")} · <span class="${countCls}">${t("{n} pistes (dossier : {k})", { n: discTracks, k: r.n_tracks })}</span> ${d.via_durations ? `<span class="chip g-clean">${t("durées ✓")}</span>` : ""}</div>` : `<span class="muted small">${esc(tr(d.reason || ""))}</span>`}</td>
        <td><b class="${cls}">${v.score ? Math.round(v.score) : "—"}</b>
          ${v.score ? `<div class="at-bar"><div style="width:${Math.min(100, v.score)}%"></div></div>` : ""}</td>
        <td class="small">${t("durées")} ${pct(d.durations)}${d.avg_gap !== null && d.avg_gap !== undefined ? ` (±${d.avg_gap} s, max ${d.max_gap} s)` : ""}<br>
          ${t("titres")} ${pct(d.titles)} · ${t("noms")} ${pct(d.names)}
          ${d.rival ? `<div class="conf-medium">${t("aussi proche :")} ${esc(d.rival.artist)} — ${esc(d.rival.title)} (${Math.round(d.rival.score)})</div>` : ""}</td>
        <td><span class="chip ${AT_STATUS[v.status][1]}">${AT_STATUS[v.status][0]}</span></td>
      </tr>`;
    }).join("")}</tbody></table>`;
  $("#at-all").onchange = (e) => {
    for (const r of rows) if (!AT_INACTIVE.includes(atView(r).status)) e.target.checked ? atState.selected.add(r.dir) : atState.selected.delete(r.dir);
    drawAutotag();
  };
  for (const row of $$("tbody tr", box)) {
    const dir = row.dataset.dir;
    $(".at-sel", row).onchange = (e) => { e.target.checked ? atState.selected.add(dir) : atState.selected.delete(dir); drawAutotagBatch(rows); };
    $("a.open", row).onclick = (e) => { e.preventDefault(); openAlbum(dir); };
  }
  drawAutotagBatch(rows);
}

function drawAutotagBatch(rows) {
  const sel = [...atState.selected].filter((d) => atState.data.items.some((r) => r.dir === d && !AT_INACTIVE.includes(atView(r).status)));
  const risky = sel.filter((d) => { const v = atView(atState.data.items.find((x) => x.dir === d)); return v.status !== "ok" || v.score < atState.options.min_score; });
  $("#at-batch").innerHTML = `<div class="batchbar">
    <b>${t("{n} dossier(s) sélectionné(s)", { n: sel.length })}</b>
    ${risky.length ? `<span class="conf-medium small">${t("dont {n} sous le seuil ou ambigu(s)", { n: risky.length })}</span>` : ""}
    <span style="flex:1"></span>
    <button id="at-reanalyse" ${sel.length ? "" : "disabled"}>${t("Réanalyser")}</button>
    <button class="primary" id="at-apply" ${sel.length ? "" : "disabled"}>${t("Appliquer à la sélection")}</button></div>`;
  $("#at-reanalyse").onclick = () => run(async () => {
    await api("POST", "/api/autotag/analyse", { dirs: sel, force: true });
    lastJobStatus = "running"; refreshStatus();
  });
  $("#at-apply").onclick = () => {
    const o = atState.options;
    const yes = (b) => (b ? t("oui") : t("non"));
    const aa = o.albumartist_mode === "fixed" ? t("imposé « {v} »", { v: o.albumartist_value }) : o.albumartist_mode === "folder" ? t("artiste du dossier") : "MusicBrainz";
    let msg = t("confirm.autotag.apply", { n: sel.length, aa, genre: yes(o.fill_genre), cover: yes(o.cover), empty: yes(o.only_empty), strict: yes(o.strict_count) });
    if (risky.length) msg += "\n\n⚠ " + t("{n} dossier(s) sous le seuil ou ambigu(s) inclus.", { n: risky.length });
    if (!confirm(msg + "\n\n" + t("Annulable depuis l'historique."))) return;
    run(async () => {
      await api("POST", "/api/autotag/apply", { dirs: sel, options: o });
      atState.selected = new Set(); lastJobStatus = "running"; refreshStatus();
    });
  };
}

// ------------------------------------------------------------------ errors
const errState = { q: "" };

async function renderErrors(main) {
  main.innerHTML = `
    <h1>${t("Fichiers en erreur")}</h1>
    <p class="lead">${t("lead.errors")}</p>
    <div class="toolbar">
      <input type="search" id="eq" placeholder="${t("Filtrer…")}" value="${esc(errState.q)}" style="width:300px">
      <span class="grow"></span>
      <button id="eretry-all">${t("Tout réessayer")}</button>
    </div>
    <div id="elist"><div class="empty">${t("Chargement…")}</div></div>`;
  let timer;
  $("#eq").oninput = (e) => { clearTimeout(timer); timer = setTimeout(() => { errState.q = e.target.value; loadErrors(); }, 300); };
  $("#eretry-all").onclick = () => run(async () => {
    const d = await api("GET", "/api/errors?" + qs({ q: errState.q }));
    await retryErrors(d.items.map((x) => x.dir));
  });
  loadErrors();
}

async function retryErrors(dirs) {
  if (!dirs.length) return;
  const r = await api("POST", "/api/errors/retry", { dirs });
  toast(r.remaining ? t("{n} fichier(s) toujours illisible(s)", { n: r.remaining }) : t("Tout est lisible maintenant"), r.remaining > 0);
  refreshStatus();
  loadErrors();
}

async function loadErrors() {
  const data = await run(() => api("GET", "/api/errors?" + qs({ q: errState.q })));
  const box = $("#elist");
  if (!box) return;
  if (!data.items.length) { box.innerHTML = `<div class="empty">${t("Aucun fichier en erreur")}</div>`; return; }
  const MAX = 20;
  box.innerHTML = `<table><thead><tr><th>${t("Dossier")}</th><th>${t("Fichiers illisibles")}</th><th></th></tr></thead><tbody>
    ${data.items.map((d) => `<tr data-dir="${esc(d.dir)}">
      <td><div class="dir">${esc(d.dir || "/")}</div>
        <div class="small muted">${d.album ? `${t("{n} piste(s) lisible(s)", { n: d.album.n_tracks })} · ${fmtSize(d.album.total_size)}` : t("aucune piste lisible")}</div></td>
      <td class="small">
        ${d.unlisted ? `<div class="err-msg">${t("Dossier illisible :")} ${esc(d.error)}</div>` : ""}
        ${d.files.length ? `<ul class="err-files">${d.files.slice(0, MAX).map((f) => `<li><span class="mono">${esc(f.filename)}</span> — <span class="err-msg">${esc(f.error)}</span></li>`).join("")}</ul>` : ""}
        ${d.files.length > MAX ? `<div class="muted">… ${t("{n} de plus", { n: d.files.length - MAX })}</div>` : ""}</td>
      <td style="white-space:nowrap;text-align:right">
        ${d.album ? `<button class="link e-open">${t("Ouvrir")}</button>` : ""}
        <button class="e-retry" title="${t("Relire les fichiers de ce dossier")}">${t("Réessayer")}</button>
        ${d.unlisted ? "" : `<button class="danger e-delete">${t("Supprimer")}</button>`}</td>
    </tr>`).join("")}</tbody></table>
    <div class="small muted" style="margin-top:8px">${t("{n} dossier(s)", { n: fmtNum(data.total) })}</div>`;
  for (const row of $$("tr[data-dir]", box)) {
    const dir = row.dataset.dir, d = data.items.find((x) => x.dir === dir);
    if ($(".e-open", row)) $(".e-open", row).onclick = () => openAlbum(dir, loadErrors);
    $(".e-retry", row).onclick = () => run(() => retryErrors([dir]));
    if ($(".e-delete", row)) $(".e-delete", row).onclick = async () => { if (await deleteAlbum(dir, d.album)) loadErrors(); };
  }
}

// ----------------------------------------------------------------- history
const histState = { selected: new Set() };

async function renderHistory(main) {
  const { items: rows, stats } = await run(() => api("GET", "/api/history?limit=200"));
  const sel = histState.selected;
  for (const b of [...sel]) if (!rows.some((r) => r.batch === b)) sel.delete(b);
  main.innerHTML = `
    <h1>${t("Historique")}</h1>
    <p class="lead">${t("lead.history")}</p>
    <div class="panel small">
      ${t("{b} lot(s), {n} opération(s), environ {size} dans la base (fichier de la base : {db}).", {
        b: fmtNum(stats.batches), n: fmtNum(stats.entries), size: fmtSize(stats.bytes), db: fmtSize(stats.db_size) })}
      ${stats.oldest ? t("Plus ancienne entrée : {d}.", { d: esc(stats.oldest) }) : ""}
      ${stats.batches > rows.length ? `<span class="muted">${t("Seuls les {n} lots les plus récents sont affichés.", { n: rows.length })}</span>` : ""}
    </div>
    ${rows.length ? `<div class="batchbar">
      <b id="h-count"></b>
      <button class="link" id="h-selall">${t("Tout sélectionner")}</button>
      <button class="link" id="h-selnone">${t("Désélectionner")}</button>
      <span style="flex:1"></span>
      <button class="danger" id="h-delete" ${sel.size ? "" : "disabled"}>${t("Supprimer de l'historique")}</button>
      <button class="danger solid" id="h-clear">${t("Vider l'historique")}</button>
    </div>
    <table><thead><tr><th class="check"></th><th>${t("Date")}</th><th>${t("Action")}</th><th>${t("Fichiers")}</th><th class="hide-sm">${t("Taille")}</th><th>${t("État")}</th><th></th></tr></thead><tbody>
      ${rows.map((r) => `<tr data-batch="${esc(r.batch)}">
        <td class="check"><input type="checkbox" class="h-sel" ${sel.has(r.batch) ? "checked" : ""}></td>
        <td class="small">${esc(r.ts)}</td>
        <td>${esc(tr(r.label))}</td>
        <td>${r.n}${r.n_trash ? ` <span class="muted small">(${t("{n} dossier(s) en corbeille", { n: r.n_trash })})</span>` : ""}</td>
        <td class="small muted hide-sm">${fmtSize(r.bytes)}</td>
        <td>${r.purged === 2 ? `<span class="muted">${t("définitif")}</span>` : r.undone ? `<span class="muted">${t("annulé")}</span>` : `<span class="conf-high">${t("appliqué")}</span>`}</td>
        <td style="text-align:right;white-space:nowrap"><button class="link detail">${t("Détails")}</button>
          ${!r.undone ? `<button class="undo">${t("Annuler")}</button>` : ""}
          <button class="link h-del" title="${t("Supprimer de l'historique")}">✕</button></td>
      </tr><tr class="hidden"><td colspan="7" class="detail-box"></td></tr>`).join("")}
    </tbody></table>` : `<div class="empty">${t("Aucune modification pour l'instant")}</div>`}`;

  // Deleting a batch whose folders are still in the trash loses the way back.
  const inTrash = (batches) => rows.some((r) => batches.includes(r.batch) && r.n_trash && !r.undone);
  const forget = (batches, all = false) => {
    const n = all ? stats.batches : batches.length;
    let msg = t("confirm.history.delete", { n });
    if (all ? stats.restorable : inTrash(batches)) msg += "\n\n⚠ " + t("confirm.history.trash");
    if (!confirm(msg)) return;
    run(async () => {
      const r = await api("POST", "/api/history/delete", all ? { all: true } : { batches });
      sel.clear();
      toast(t("{n} opération(s) supprimée(s) de l'historique", { n: r.deleted }));
      render();
    });
  };
  if (!rows.length) return;
  const syncSelection = () => {
    $("#h-count").textContent = t("{n} sélectionné(s)", { n: sel.size });
    $("#h-selnone").classList.toggle("hidden", !sel.size);
    $("#h-delete").disabled = !sel.size;
    for (const row of $$("tr[data-batch]", main)) $(".h-sel", row).checked = sel.has(row.dataset.batch);
  };
  $("#h-selall").onclick = () => { rows.forEach((r) => sel.add(r.batch)); syncSelection(); };
  $("#h-selnone").onclick = () => { sel.clear(); syncSelection(); };
  $("#h-delete").onclick = () => forget([...sel]);
  $("#h-clear").onclick = () => forget([], true);
  for (const row of $$("tr[data-batch]", main)) {
    const batch = row.dataset.batch, next = row.nextElementSibling;
    $(".h-sel", row).onchange = (e) => { e.target.checked ? sel.add(batch) : sel.delete(batch); syncSelection(); };
    $(".h-del", row).onclick = () => forget([batch]);
    $(".detail", row).onclick = () => run(async () => {
      if (!next.classList.contains("hidden")) return next.classList.add("hidden");
      const items = await api("GET", `/api/history/${encodeURIComponent(batch)}`);
      const fmt = (o) => Object.entries(o).map(([k, v]) => `${k}=${v === null ? "∅" : v}`).join(", ");
      $(".detail-box", next).innerHTML = items.slice(0, 500).map((i) => `<div class="small"><span class="mono">${esc(i.path)}</span><br>
        <span class="muted">${i.action === "trash" ? "→ " + esc(i.after.dst) : esc(fmt(i.before)) + " <span class='arrow'>→</span> " + esc(fmt(i.after))}</span></div>`).join("") +
        (items.length > 500 ? `<div class="muted">… ${t("{n} de plus", { n: items.length - 500 })}</div>` : "");
      next.classList.remove("hidden");
    });
    const u = $(".undo", row);
    if (u) u.onclick = () => confirm(t("Annuler ce lot de modifications ?")) && run(async () => {
      const r = await api("POST", `/api/history/${encodeURIComponent(batch)}/undo`);
      toast(t("{n} opération(s) annulée(s)", { n: r.undone }) + (r.errors.length ? ", " + t("{n} erreur(s)", { n: r.errors.length }) + " : " + r.errors[0] : ""), r.errors.length > 0);
      refreshStatus(); render();
    });
  }
  syncSelection();
}

// ------------------------------------------------------------ mobile menu
function setNav(open) {
  document.body.classList.toggle("nav-open", open);
  $("#burger").setAttribute("aria-expanded", String(open));
  if (open) ($("#nav a.active") || $("#nav a")).focus();
}
$("#burger").onclick = () => setNav(!document.body.classList.contains("nav-open"));
$("#nav-backdrop").onclick = () => setNav(false);
for (const a of $$("#nav a")) a.addEventListener("click", () => setNav(false));
document.addEventListener("keydown", (e) => { if (e.key === "Escape" && document.body.classList.contains("nav-open")) { setNav(false); $("#burger").focus(); } });

// -------------------------------------------------------------------- boot
function syncChoiceButtons() {
  const theme = window.fmtTheme ? window.fmtTheme.get() : "auto";
  for (const b of $$("[data-theme-choice]")) b.setAttribute("aria-pressed", String(b.dataset.themeChoice === theme));
  for (const b of $$("[data-lang-choice]")) b.setAttribute("aria-pressed", String(b.dataset.langChoice === LANG));
}
for (const b of $$("[data-theme-choice]")) b.onclick = () => { window.fmtTheme.set(b.dataset.themeChoice); syncChoiceButtons(); };
for (const b of $$("[data-lang-choice]")) b.onclick = () => { if (b.dataset.langChoice !== LANG) window.fmtI18n.set(b.dataset.langChoice); };
syncChoiceButtons();
$("#logout").onclick = () => api("POST", "/api/logout").finally(() => location.replace("/login"));
api("GET", "/api/genres/canon").then((r) => { genreCanon = r.canon; }).catch(() => {});
refreshStatus().then(render);
