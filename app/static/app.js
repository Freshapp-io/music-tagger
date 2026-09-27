"use strict";

// ------------------------------------------------------------------ helpers
const $ = (sel, root = document) => root.querySelector(sel);
const $$ = (sel, root = document) => [...root.querySelectorAll(sel)];
const esc = (s) => String(s ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
const fmtSize = (b) => b >= 1e9 ? (b / 1e9).toFixed(1) + " Go" : b >= 1e6 ? (b / 1e6).toFixed(0) + " Mo" : b > 0 ? Math.max(1, Math.round(b / 1e3)) + " Ko" : "0 Ko";
const fmtDur = (s) => { s = Math.round(s || 0); const m = Math.floor(s / 60); return `${m}:${String(s % 60).padStart(2, "0")}`; };
const fold = (s) => String(s ?? "").normalize("NFKD").replace(/[̀-ͯ]/g, "").toLowerCase().replace(/[^\w]+/g, " ").trim();

async function api(method, url, body) {
  const opts = { method, headers: {} };
  if (body !== undefined) { opts.headers["Content-Type"] = "application/json"; opts.body = JSON.stringify(body); }
  const r = await fetch(url, opts);
  if (r.status === 401 && !url.startsWith("/api/login")) { location.replace("/login"); throw new Error("Session expirée"); }
  const data = await r.json().catch(() => ({}));
  if (!r.ok) throw new Error(data.detail || `Erreur ${r.status}`);
  return data;
}
const qs = (o) => new URLSearchParams(Object.entries(o).filter(([, v]) => v !== "" && v !== undefined && v !== null)).toString();

let toastTimer;
function toast(msg, error = false) {
  const t = $("#toast");
  t.textContent = msg;
  t.className = "toast" + (error ? " error" : "");
  clearTimeout(toastTimer);
  toastTimer = setTimeout(() => t.classList.add("hidden"), error ? 7000 : 3500);
}
async function run(fn) {
  try { return await fn(); } catch (e) { toast(e.message, true); throw e; }
}

const ISSUE = { untagged: "Non taggué", various: "Various", inconsistent: "Incohérent", duplicate: "Doublon" };
const SUB = {
  albumartist_mixed: "Album artist différents",
  albumartist_missing: "Album artist absent, artistes multiples",
  album_mixed: "Nom d'album différent",
  year_mixed: "Années différentes",
  mbid_mixed: "ID MusicBrainz différents",
};
const CONF = { high: "sûre", medium: "probable", low: "incertaine" };
const badges = (issues) => (issues || []).filter((i) => ISSUE[i]).map((i) => `<span class="badge b-${i}">${ISSUE[i]}</span>`).join("");
const subBadges = (issues) => (issues || []).filter((i) => SUB[i]).map((i) => `<span class="chip">${SUB[i]}</span>`).join(" ");
const chips = (arr, max = 4) => {
  const a = (arr || []).map((x) => x === null ? "∅ vide" : x);
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
    el.textContent = n ? n.toLocaleString("fr") : "";
  }
  const j = status.job, box = $("#job");
  if (j) {
    box.classList.remove("hidden");
    const pct = j.total ? Math.round((100 * j.done) / j.total) : 0;
    const state = j.status === "running" ? `${pct}%` : j.status === "done" ? "terminé" : "échec";
    box.innerHTML = `<b>${esc(j.label)}</b> — ${state}
      <div class="bar"><div style="width:${j.status === "running" ? pct : 100}%"></div></div>
      <div class="msg" title="${esc(j.message)}">${esc(j.message)}</div>
      ${j.n_errors ? `<div class="small" style="color:var(--bad)">${j.n_errors} erreur(s)</div>` : ""}`;
    if (lastJobStatus === "running" && j.status !== "running") {
      toast(`${j.label} : ${j.status === "done" ? j.message || "terminé" : "échec — " + j.message}`, j.status !== "done");
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
  if (["untagged", "various", "inconsistent", "all"].includes(view)) return renderList(main, view);
  main.innerHTML = `<div class="empty">Page inconnue</div>`;
}
window.addEventListener("hashchange", render);

// --------------------------------------------------------------- dashboard
async function renderDashboard(main) {
  if (!status) await refreshStatus();
  const s = status, c = s.counts;
  const trash = await api("GET", "/api/trash").catch(() => null);
  const card = (href, n, label) => `<a class="card" href="${href}"><div class="num">${(n || 0).toLocaleString("fr")}</div><div class="lbl">${label}</div></a>`;
  main.innerHTML = `
    <h1>Tableau de bord</h1>
    <p class="lead">Bibliothèque : <span class="mono">${esc(s.music_root)}</span>
      ${s.root_exists ? "" : `<b style="color:var(--bad)"> — introuvable, vérifiez le volume Docker</b>`}<br>
      Dernier scan : ${s.last_scan ? esc(s.last_scan.replace("T", " ")) : "jamais"}</p>
    <div class="toolbar">
      <button class="primary" id="scan">Scanner (nouveaux / modifiés)</button>
      <button id="fullscan">Scan complet</button>
      <button id="reanalyze">Recalculer l'analyse</button>
      ${s.navidrome ? `<button id="navidrome">Lancer un scan Navidrome</button>` : ""}
    </div>
    <div class="cards">
      ${card("#/all", s.tracks, "fichiers mp3")}
      ${card("#/all", s.albums, "dossiers (albums)")}
      ${card("#/untagged", c.untagged, "dossiers avec fichiers non / mal taggués")}
      ${card("#/various", c.various, "dossiers « Various Artists » suspects")}
      ${card("#/inconsistent", c.inconsistent, "dossiers aux tags incohérents")}
      ${card("#/duplicates", c.duplicate_groups, "groupes d'albums en double")}
    </div>
    <h2>Comment ça marche</h2>
    <div class="panel">
      <ol style="margin:0;padding-left:20px">
        <li><b>Scanner</b> lit les tags de tous les mp3 (le 1er scan d'une grosse bibliothèque prend du temps ; les suivants ne relisent que les fichiers modifiés).</li>
        <li>Chaque dossier contenant des mp3 est considéré comme un album et analysé : fichiers sans tags, « Various Artists », album artist / nom d'album / année qui diffèrent entre pistes (ce qui fait éclater l'album dans Navidrome), doublons.</li>
        <li>Pour chaque dossier une <b>suggestion</b> est calculée (artiste majoritaire hors « feat. », nom du dossier…) avec un niveau de confiance. Vous pouvez l'appliquer en masse, éditer à la main, ou chercher l'album sur <b>MusicBrainz</b>.</li>
        <li>Chaque modification est enregistrée dans l'<a href="#/history">historique</a> et peut être <b>annulée</b>. Les doublons supprimés partent dans une corbeille, pas à la poubelle.</li>
      </ol>
    </div>
    <h2>Corbeille</h2>
    <div class="panel">
      ${trash ? `<span class="mono">${esc(trash.path)}</span> — ${trash.files} fichier(s), ${fmtSize(trash.size)}` : "—"}
      ${trash && trash.files ? `<button class="danger" id="empty-trash" style="margin-left:12px">Vider définitivement</button>` : ""}
    </div>`;
  $("#scan").onclick = () => run(async () => { await api("POST", "/api/scan", { full: false }); lastJobStatus = "running"; refreshStatus(); });
  $("#fullscan").onclick = () => confirm("Relire tous les fichiers ? (long sur une grosse bibliothèque)") &&
    run(async () => { await api("POST", "/api/scan", { full: true }); lastJobStatus = "running"; refreshStatus(); });
  $("#reanalyze").onclick = () => run(async () => { await api("POST", "/api/reanalyze"); lastJobStatus = "running"; refreshStatus(); });
  if ($("#navidrome")) $("#navidrome").onclick = () => run(async () => { await api("POST", "/api/navidrome/scan"); toast("Scan Navidrome lancé"); });
  if ($("#empty-trash")) $("#empty-trash").onclick = () =>
    confirm(`Supprimer définitivement ${trash.files} fichier(s) (${fmtSize(trash.size)}) ? Cette action est irréversible.`) &&
    run(async () => { await api("POST", "/api/trash/empty"); toast("Corbeille vidée"); render(); });
}

// -------------------------------------------------------------------- lists
const VIEW_INFO = {
  untagged: ["Fichiers non taggués", "Dossiers contenant des mp3 sans tags ou sans artiste / titre / album. La suggestion déduit les tags du nom du dossier et des fichiers."],
  various: ["Various Artists", "Dossiers dont l'artiste ou l'album artist est « Various Artists ». Si un artiste domine, la suggestion le propose comme album artist ; si c'est une vraie compilation, marquez-la comme telle."],
  inconsistent: ["Tags incohérents", "Dossiers où l'album artist, le nom d'album, l'année ou l'ID MusicBrainz diffèrent entre pistes — Navidrome affiche alors plusieurs albums au lieu d'un. Les « feat. » dans le tag artiste sont normaux ; c'est l'album artist qui compte."],
  all: ["Tous les dossiers", "Tous les dossiers contenant des mp3."],
};

async function renderList(main, view) {
  const st = stateFor(view);
  const [title, lead] = VIEW_INFO[view];
  main.innerHTML = `
    <h1>${title}</h1><p class="lead">${lead}</p>
    <div class="toolbar">
      <input type="search" id="q" placeholder="Rechercher (dossier, artiste, album)…" value="${esc(st.q)}" style="width:320px">
      ${view === "inconsistent" ? `<select id="sub"><option value="">Tous les problèmes</option>${Object.entries(SUB).map(([k, v]) => `<option value="${k}" ${st.sub === k ? "selected" : ""}>${v}</option>`).join("")}</select>` : ""}
      <select id="confidence"><option value="">Toutes confiances</option>${Object.entries(CONF).map(([k, v]) => `<option value="${k}" ${st.confidence === k ? "selected" : ""}>Suggestion ${v}</option>`).join("")}</select>
      <select id="sort">${Object.entries({ dir: "Tri : dossier", artist: "Tri : artiste", tracks: "Tri : nb pistes", bitrate: "Tri : bitrate" }).map(([k, v]) => `<option value="${k}" ${st.sort === k ? "selected" : ""}>${v}</option>`).join("")}</select>
      ${view !== "all" ? `<label><input type="checkbox" id="showIgnored" ${st.showIgnored ? "checked" : ""}> afficher les ignorés</label>` : ""}
      <span class="grow"></span>
      ${view !== "all" ? `<button class="primary" id="review">▶ Revue album par album</button>` : ""}
    </div>
    <div id="batch"></div>
    <div id="list"><div class="empty">Chargement…</div></div>`;
  let timer;
  $("#q").oninput = (e) => { clearTimeout(timer); timer = setTimeout(() => { st.q = e.target.value; st.offset = 0; loadList(view); }, 300); };
  if ($("#sub")) $("#sub").onchange = (e) => { st.sub = e.target.value; st.offset = 0; loadList(view); };
  $("#confidence").onchange = (e) => { st.confidence = e.target.value; st.offset = 0; loadList(view); };
  $("#sort").onchange = (e) => { st.sort = e.target.value; loadList(view); };
  if ($("#showIgnored")) $("#showIgnored").onchange = (e) => { st.showIgnored = e.target.checked; st.offset = 0; loadList(view); };
  if ($("#review")) $("#review").onclick = () => run(async () => {
    // Review the selection if there is one, otherwise everything matching the filters.
    let dirs = [...st.selected];
    if (!dirs.length) {
      for (let off = 0; ; off += 1000) {
        const d = await api("GET", "/api/albums?" + listParams(view, st, { offset: off, limit: 1000 }));
        dirs.push(...d.items.map((a) => a.dir));
        if (off + 1000 >= d.total) break;
      }
    }
    startReview(view, VIEW_INFO[view][0] + (st.selected.size ? " (sélection)" : ""), dirs, "#/" + view);
  });
  loadList(view);
}

function listParams(view, st, extra = {}) {
  return qs({ issue: view, q: st.q, sub: st.sub, confidence: st.confidence, show_ignored: st.showIgnored, sort: st.sort, ...extra });
}

function suggestionHtml(a) {
  const s = a.suggestion || {};
  if (!s.albumartist && !s.album) return `<span class="muted">aucune</span>`;
  const cur = (arr) => (arr || []).filter((x) => x !== null);
  const diff = (label, val, current) => {
    if (!val) return "";
    const same = current.length === 1 && fold(current[0]) === fold(val) && current[0] === val;
    return `<div>${label} : ${same ? `<span class="muted">${esc(val)}</span>` : `<b>${esc(val)}</b>`}</div>`;
  };
  return `<div class="sug">
    ${diff("Album artist", s.albumartist, cur(a.albumartists))}
    ${diff("Album", s.album, cur(a.albums))}
    ${diff("Année", s.year, cur(a.years))}
    ${s.compilation ? `<div><b>compilation</b></div>` : ""}
    <div class="small conf-${s.confidence}" title="${esc(s.reason)}">● ${CONF[s.confidence] || ""}${s.reason ? ` — ${esc(s.reason)}` : ""}</div>
  </div>`;
}

async function loadList(view) {
  const st = stateFor(view);
  const data = await run(() => api("GET", "/api/albums?" + listParams(view, st, { offset: st.offset, limit: 100 })));
  const box = $("#list");
  if (!box) return;
  if (!data.items.length) {
    box.innerHTML = `<div class="empty">${status && !status.tracks ? "Aucun fichier : lancez un scan depuis le tableau de bord." : "Rien à corriger ici"}</div>`;
    renderBatch(view, data.total);
    return;
  }
  box.innerHTML = `
    <table>
      <thead><tr>
        <th class="check"><input type="checkbox" id="checkall" title="Sélectionner la page"></th>
        <th>Dossier</th><th>Tags actuels</th><th>Suggestion</th><th>Pistes</th><th>Qualité</th>
      </tr></thead>
      <tbody>${data.items.map((a) => `
        <tr class="clickable" data-dir="${esc(a.dir)}">
          <td class="check"><input type="checkbox" class="sel" ${st.selected.has(a.dir) ? "checked" : ""}></td>
          <td><div class="dir">${esc(a.dir || "/")}</div>
            <div>${badges(a.issues)} ${a.ignored.length ? `<span class="chip">ignoré : ${a.ignored.map((k) => ISSUE[k] || k).join(", ")}</span>` : ""}</div>
            <div style="margin-top:3px">${subBadges(a.issues)}</div></td>
          <td class="small">
            <div class="muted">Artistes</div>${chips(a.artists, 3)}
            <div class="muted">Album artist</div>${chips(a.albumartists, 3)}
            <div class="muted">Album</div>${chips(a.albums, 2)}
          </td>
          <td>${suggestionHtml(a)}</td>
          <td>${a.n_tracks}${a.n_untagged ? `<div class="small conf-low">${a.n_untagged} sans tag</div>` : ""}${a.n_incomplete ? `<div class="small conf-medium">${a.n_incomplete} incomplet(s)</div>` : ""}</td>
          <td class="small">${a.avg_bitrate} kbps${a.vbr ? " VBR" : ""}<div class="muted">${fmtSize(a.total_size)}</div></td>
        </tr>`).join("")}
      </tbody>
    </table>
    <div class="pager">
      <span class="muted">${st.offset + 1}–${st.offset + data.items.length} sur ${data.total.toLocaleString("fr")}</span>
      <button id="prev" ${st.offset === 0 ? "disabled" : ""}>‹ Précédent</button>
      <button id="next" ${st.offset + 100 >= data.total ? "disabled" : ""}>Suivant ›</button>
    </div>`;
  $("#prev").onclick = () => { st.offset = Math.max(0, st.offset - 100); loadList(view); window.scrollTo(0, 0); };
  $("#next").onclick = () => { st.offset += 100; loadList(view); window.scrollTo(0, 0); };
  $("#checkall").onchange = (e) => {
    for (const a of data.items) e.target.checked ? st.selected.add(a.dir) : st.selected.delete(a.dir);
    for (const cb of $$(".sel", box)) cb.checked = e.target.checked;
    renderBatch(view, data.total);
  };
  for (const tr of $$("tbody tr", box)) {
    const dir = tr.dataset.dir;
    $(".sel", tr).onclick = (e) => {
      e.stopPropagation();
      e.target.checked ? st.selected.add(dir) : st.selected.delete(dir);
      renderBatch(view, data.total);
    };
    tr.onclick = () => openAlbum(dir, () => loadList(view));
  }
  renderBatch(view, data.total);
}

function renderBatch(view, total) {
  const st = stateFor(view), box = $("#batch");
  if (!box) return;
  const n = st.selected.size;
  box.innerHTML = `<div class="batchbar">
    <b>${n.toLocaleString("fr")} sélectionné(s)</b>
    <button class="link" id="selall">Tout sélectionner (${total.toLocaleString("fr")} résultats)</button>
    ${n ? `<button class="link" id="selnone">Désélectionner</button>` : ""}
    <span style="flex:1"></span>
    ${view !== "all" ? `
      <button class="primary" id="apply" ${n ? "" : "disabled"}>Appliquer les suggestions</button>
      ${view === "various" ? `<button id="compil" ${n ? "" : "disabled"}>Marquer comme compilations</button>` : ""}
      <button id="ignore" ${n ? "" : "disabled"}>${st.showIgnored ? "Ne plus ignorer" : "Ignorer"}</button>` : ""}
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
    if (!confirm(`Appliquer la suggestion (album artist, album, année, et titres/n° depuis les noms de fichiers pour les pistes vides) sur ${n} dossier(s) ?\n\nLes dossiers sans suggestion d'album artist ne verront que leurs champs vides complétés. Tout est annulable depuis l'historique.`)) return;
    run(async () => {
      await api("POST", "/api/batch/apply", { dirs: [...st.selected] });
      st.selected.clear(); lastJobStatus = "running"; refreshStatus(); loadList(view);
    });
  };
  if ($("#compil")) $("#compil").onclick = () => confirm(`Définir album artist = « Various Artists » et le flag compilation sur ${n} dossier(s) ?`) &&
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
  audio.play().catch((e) => toast("Lecture impossible : " + e.message, true));
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
const playBtn = (t) => `<button class="play" data-play="${esc(t.path)}" data-label="${esc((t.artist ? t.artist + " – " : "") + (t.title || t.filename))}" title="Écouter">▶</button>`;
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

const TRACK_FIELDS = ["disc", "track", "title", "artist"];

/**
 * Album editor rendered into `root`. Used by the side drawer and by review mode.
 * opts.review: hide the save/ignore buttons (review mode has its own action bar)
 * Returns { save(), album } — save() writes album fields + every changed track.
 */
async function albumEditor(root, dir, opts = {}) {
  const d = await run(() => api("GET", "/api/album?" + qs({ dir })));
  const { album: a, tracks } = d;
  const s = a.suggestion || {};
  const coverTrack = tracks.find((t) => t.has_cover);
  const allComp = tracks.length && tracks.every((t) => t.compilation);
  const isVarious = a.issues.includes("various") || s.compilation || allComp;
  const cur = {
    albumartist: majorityOf(tracks.map((t) => t.albumartist)),
    album: majorityOf(tracks.map((t) => t.album)),
    year: majorityOf(tracks.map((t) => t.year)),
    genre: majorityOf(tracks.map((t) => t.genre)),
  };
  const init = { albumartist: s.albumartist || cur.albumartist, album: s.album || cur.album, year: s.year || cur.year, genre: cur.genre };
  const distinctVals = (k) => [...new Set(tracks.map((t) => t[k]).filter(Boolean))];
  const fieldRow = (key, label) => {
    const opts2 = distinctVals(key);
    return `<label>${label}</label><div class="field">
      <input data-album="${key}" value="${esc(init[key])}" ${key === "genre" ? 'list="genre-canon"' : ""}>
      ${opts2.length ? `<div class="chips">${opts2.slice(0, 12).map((o) => `<button type="button" class="chip" data-pick="${key}" data-val="${esc(o)}" title="Utiliser cette valeur">${esc(o)}</button>`).join("")}</div>` : ""}
    </div>`;
  };

  // Track model shared by the table view and the track-by-track view.
  const orig = Object.fromEntries(tracks.map((t) => [t.path, Object.fromEntries(TRACK_FIELDS.map((f) => [f, t[f] || ""]))]));
  const vals = Object.fromEntries(tracks.map((t) => [t.path, { ...orig[t.path] }]));
  const extra = {};          // MusicBrainz ids picked per track
  const saved = new Set();   // tracks saved individually
  let view = isVarious ? "cards" : "table";

  root.innerHTML = `
    <div class="drawer-head">
      ${coverTrack ? `<img src="/api/cover?${qs({ path: coverTrack.path })}" alt="">` : `<img alt="">`}
      <div class="grow">
        <h1>${esc(a.dir || "/")}</h1>
        <div>${badges(a.issues)} ${subBadges(a.issues)}</div>
        <div class="muted small" style="margin-top:4px">${a.n_tracks} pistes · ${fmtDur(a.duration)} · ${a.avg_bitrate} kbps${a.vbr ? " VBR" : ""} · ${fmtSize(a.total_size)} · qualité ${a.quality}</div>
        ${s.reason ? `<div class="small conf-${s.confidence}">Suggestion ${CONF[s.confidence]} : ${esc(s.reason)}</div>` : ""}
      </div>
      ${opts.onClose ? `<button class="ed-close">✕</button>` : ""}
    </div>

    <div class="panel">
      <div class="form-grid">
        ${fieldRow("albumartist", "Album artist")}
        ${fieldRow("album", "Album")}
        ${fieldRow("year", "Année")}
        ${fieldRow("genre", "Genre")}
        <label>Compilation</label><div><label><input type="checkbox" class="ed-compil" ${s.compilation || allComp ? "checked" : ""}> Vraie compilation (plusieurs artistes, TCMP=1)</label></div>
      </div>
      <p class="small muted" style="margin-bottom:0">Les champs d'album s'appliquent à toutes les pistes du dossier. Un champ laissé vide n'est pas modifié (sauf si vous l'avez vidé vous-même).</p>
    </div>

    <div class="toolbar">
      <b>Pistes</b>
      <div class="seg">
        <button data-view="table">Tableau</button><button data-view="cards">Piste par piste</button>
      </div>
      <span class="grow"></span>
      <button class="ed-fill-empty">Compléter les vides depuis les noms de fichiers</button>
      <button class="ed-fill-all">Tout remplacer depuis les noms de fichiers</button>
      <button class="ed-artist-aa">Artiste = album artist</button>
    </div>
    <div class="ed-tracks"></div>

    <div class="actions">
      ${opts.review ? "" : `<button class="primary ed-save">Enregistrer les tags</button>`}
      <button class="ed-mb-open">Chercher l'album sur MusicBrainz</button>
      ${opts.review ? "" : a.issues.filter((i) => ISSUE[i]).map((i) => a.ignored.includes(i)
        ? `<button data-unignore="${i}">Ne plus ignorer (${ISSUE[i]})</button>`
        : `<button data-ignore="${i}">Ignorer (${ISSUE[i]})</button>`).join("")}
    </div>

    <div class="panel hidden ed-mb" style="margin-top:16px"></div>

    ${d.duplicates.length ? `<h2>Doublons probables</h2><div class="panel">${d.duplicates.map((o) => `
      <div style="padding:4px 0"><a href="#" data-open="${esc(o.dir)}">${esc(o.dir)}</a>
      <span class="muted small">— ${o.n_tracks} pistes, ${o.avg_bitrate} kbps${o.vbr ? " VBR" : ""}, qualité ${o.quality} (celui-ci : ${a.quality})</span></div>`).join("")}
      <a href="#/duplicates">Gérer dans la page Doublons →</a></div>` : ""}
    <datalist id="genre-canon">${genreCanon.map((g) => `<option value="${esc(g)}">`).join("")}</datalist>
    ${d.other_files.length ? `<h2>Autres fichiers du dossier</h2><div class="small muted mono">${d.other_files.map(esc).join(" · ")}</div>` : ""}
  `;

  // ---- album fields
  const dirty = new Set();
  const mark = (inp, original) => inp.classList.toggle("changed", (inp.value || "") !== (original || ""));
  for (const inp of $$("[data-album]", root)) {
    const k = inp.dataset.album;
    const all = distinctVals(k);
    inp._orig = all.length === 1 && tracks.every((t) => t[k]) ? all[0] : null;
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
  const input = (t, f, cls = "") => `<input class="${cls}" data-path="${esc(t.path)}" data-f="${f}" value="${esc(vals[t.path][f])}" placeholder="${esc(t.guess[f] || "")}">`;
  const bindInputs = () => {
    for (const inp of $$("input[data-f]", root)) {
      mark(inp, orig[inp.dataset.path][inp.dataset.f]);
      inp.oninput = () => { vals[inp.dataset.path][inp.dataset.f] = inp.value; mark(inp, orig[inp.dataset.path][inp.dataset.f]); };
    }
  };
  const guessLine = (t) => {
    const g = t.guess;
    if (!g.title && !g.artist) return "";
    return `<span class="muted">Nom de fichier :</span> ${g.track ? esc(g.track) + ". " : ""}${g.artist ? `<b>${esc(g.artist)}</b> – ` : ""}${esc(g.title || "")}
      <button class="link use-guess" data-path="${esc(t.path)}">utiliser</button>`;
  };

  function renderTracks() {
    for (const b of $$(".seg button", root)) b.classList.toggle("on", b.dataset.view === view);
    const box = $(".ed-tracks", root);
    if (view === "table") {
      box.innerHTML = `<table class="tracks">
        <thead><tr><th></th><th class="num">Disque</th><th class="num">N°</th><th>Titre</th><th>Artiste</th><th>Fichier</th><th>kbps</th></tr></thead>
        <tbody>${tracks.map((t) => `
          <tr>
            <td>${playBtn(t)}</td>
            <td>${input(t, "disc")}</td><td>${input(t, "track")}</td><td>${input(t, "title")}</td><td>${input(t, "artist")}</td>
            <td class="small mono" title="${esc(t.filename)}">${esc(t.filename)}${t.tagged ? "" : ` <span class="badge b-untagged">sans tag</span>`}${t.error ? ` <span class="badge b-untagged" title="${esc(t.error)}">erreur</span>` : ""}</td>
            <td class="small">${t.bitrate || "?"}${t.bitrate_mode === "VBR" ? "v" : ""}</td>
          </tr>`).join("")}</tbody></table>`;
    } else {
      box.innerHTML = tracks.map((t) => `
        <div class="tcard ${saved.has(t.path) ? "saved" : ""}" data-path="${esc(t.path)}">
          <div class="tcard-head">
            ${playBtn(t)}
            <span class="mono small">${esc(t.filename)}</span>
            <span class="muted small">${fmtDur(t.duration)} · ${t.bitrate || "?"} kbps</span>
            ${t.tagged ? "" : `<span class="badge b-untagged">sans tag</span>`}
            <span class="grow"></span>
            <span class="tstate small">${saved.has(t.path) ? "✓ enregistré" : ""}</span>
          </div>
          <div class="tcard-fields">
            <label>N°${input(t, "track")}</label>
            <label class="wide">Titre${input(t, "title")}</label>
            <label class="wide">Artiste${input(t, "artist")}</label>
          </div>
          <div class="small tcard-guess">${guessLine(t)}</div>
          <div class="tcard-actions">
            <button class="mb-rec">Chercher ce morceau sur MusicBrainz</button>
            <button class="primary save-one">Enregistrer ce morceau</button>
          </div>
          <div class="rec-results"></div>
        </div>`).join("");
    }
    bindInputs();
    bindPlay(box);
    for (const b of $$(".use-guess", box)) b.onclick = () => {
      const t = tracks.find((x) => x.path === b.dataset.path);
      for (const f of ["track", "title", "artist"]) if (t.guess[f]) vals[t.path][f] = t.guess[f];
      renderTracks();
    };
    for (const card of $$(".tcard", box)) {
      const path = card.dataset.path, t = tracks.find((x) => x.path === path);
      $(".save-one", card).onclick = () => run(async () => {
        const ch = trackDiff(path);
        if (!Object.keys(ch).length) return toast("Aucun changement sur ce morceau");
        await api("POST", "/api/album/save", { dir, album: {}, tracks: { [path]: ch } });
        orig[path] = { ...orig[path], ...Object.fromEntries(TRACK_FIELDS.filter((f) => f in ch).map((f) => [f, ch[f]])) };
        delete extra[path];
        saved.add(path);
        renderTracks();
        refreshStatus();
      });
      $(".mb-rec", card).onclick = () => run(async () => {
        const res = $(".rec-results", card);
        res.innerHTML = `<div class="muted small">Recherche…</div>`;
        const list = await api("GET", "/api/mb/recordings?" + qs({ artist: vals[path].artist || t.guess.artist || "", title: vals[path].title || t.guess.title || "" }));
        if (!list.length) { res.innerHTML = `<div class="muted small">Aucun résultat</div>`; return; }
        res.innerHTML = list.slice(0, 8).map((r, i) => `
          <div class="mb-result" data-i="${i}">
            <div><b>${esc(r.title)}</b> — ${esc(r.artist)} ${r.disambiguation ? `<span class="muted">(${esc(r.disambiguation)})</span>` : ""}
              <div class="small muted">${esc(r.year)} · ${esc(r.releases.join(" / "))}</div></div>
            <div class="small" style="text-align:right"><span class="${Math.abs(r.length - t.duration) <= 3 ? "conf-high" : "muted"}">${r.length ? fmtDur(r.length) : "?"}</span><div class="muted">score ${r.score}</div></div>
          </div>`).join("");
        for (const el of $$(".mb-result", res)) el.onclick = () => {
          const r = list[Number(el.dataset.i)];
          vals[path].title = r.title; vals[path].artist = r.artist;
          extra[path] = { mb_trackid: r.id, ...(r.artist_id ? { mb_artistid: r.artist_id } : {}) };
          renderTracks();
          toast("Valeurs MusicBrainz reprises — pensez à enregistrer");
        };
      });
    }
  }
  for (const b of $$(".seg button", root)) b.onclick = () => { view = b.dataset.view; renderTracks(); };
  renderTracks();

  const fill = (overwrite) => {
    for (const t of tracks) for (const f of TRACK_FIELDS) {
      if (t.guess[f] && (overwrite || !vals[t.path][f])) vals[t.path][f] = t.guess[f];
    }
    renderTracks();
  };
  $(".ed-fill-empty", root).onclick = () => fill(false);
  $(".ed-fill-all", root).onclick = () => fill(true);
  $(".ed-artist-aa", root).onclick = () => {
    const aa = albumArtist();
    if (!aa) return toast("Album artist vide", true);
    for (const t of tracks) vals[t.path].artist = aa;
    renderTracks();
  };

  const FIELD_LABEL = { albumartist: "album artist", album: "album", year: "année", genre: "genre" };
  function payload() {
    const albumFields = {};
    for (const inp of $$("[data-album]", root)) {
      const k = inp.dataset.album, v = inp.value.trim();
      if (v || dirty.has(k)) albumFields[k] = v;
    }
    albumFields.compilation = $(".ed-compil", root).checked ? "1" : "";
    const edits = {};
    for (const t of tracks) {
      const ch = trackDiff(t.path);
      if (Object.keys(ch).length) edits[t.path] = ch;
    }
    return { albumFields, edits };
  }
  /** Labels of what save() would actually change in the files ([] = nothing pending). */
  function pending() {
    const { albumFields, edits } = payload();
    const out = [];
    for (const [k, v] of Object.entries(albumFields)) {
      if (k === "compilation") {
        if (tracks.some((t) => !!t.compilation !== (v === "1"))) out.push("compilation");
      } else if (tracks.some((t) => (t[k] || "") !== v)) out.push(FIELD_LABEL[k] || k);
    }
    const n = Object.keys(edits).length;
    if (n) out.push(`${n} piste(s)`);
    return out;
  }
  async function save() {
    const { albumFields, edits } = payload();
    return api("POST", "/api/album/save", { dir, album: albumFields, tracks: edits });
  }

  if (opts.onClose) $(".ed-close", root).onclick = opts.onClose;
  for (const l of $$("[data-open]", root)) l.onclick = (e) => { e.preventDefault(); openAlbum(l.dataset.open, drawerOnClose); };
  for (const b of $$("[data-ignore],[data-unignore]", root)) b.onclick = () => run(async () => {
    const kind = b.dataset.ignore || b.dataset.unignore;
    await api("POST", "/api/ignore", { dirs: [dir], kind, value: !!b.dataset.ignore });
    toast("OK"); refreshStatus(); opts.onReload && opts.onReload();
  });
  if ($(".ed-save", root)) $(".ed-save", root).onclick = () => run(async () => {
    const r = await save();
    toast(`${r.files} fichier(s) modifié(s)`);
    refreshStatus();
    opts.onReload && opts.onReload();
  });
  const reloadAfterMb = () => { refreshStatus(); if (opts.onReload) opts.onReload(); else albumEditor(root, dir, opts); };
  $(".ed-mb-open", root).onclick = () => mbPanel(root, dir, tracks, { artist: albumArtist(), album: $('[data-album="album"]', root).value },
    reloadAfterMb, { pending, save });

  return { save, pending, album: a };
}

// ------------------------------------------------------------ musicbrainz
function mbPanel(root, dir, tracks, q, onApplied, editor) {
  const box = $(".ed-mb", root);
  box.classList.remove("hidden");
  box.innerHTML = `
    <div class="toolbar">
      <b>MusicBrainz</b>
      <input class="mb-artist" placeholder="Artiste" value="${esc(q.artist)}" style="width:220px">
      <input class="mb-album" placeholder="Album" value="${esc(q.album)}" style="width:260px">
      <button class="primary mb-search">Rechercher</button>
    </div>
    <div class="mb-results"></div><div class="mb-match"></div>`;
  box.scrollIntoView({ behavior: "smooth" });
  const search = () => run(async () => {
    const out = $(".mb-results", box);
    out.innerHTML = `<div class="muted">Recherche par nom et par durées des pistes…</div>`;
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
    const durLine = byDur.failed ? `<span class="conf-low">Recherche par durées indisponible (MusicBrainz ne répond pas)</span>`
      : nDur ? `<span class="conf-high">✓ ${nDur} édition(s) CD dont les ${tracks.length} durées correspondent, en tête de liste</span>`
      : `<span class="muted">Aucune édition CD ne correspond aux ${tracks.length} durées (il faut exactement le même nombre de pistes que le CD).</span>`;
    if (!res.length) { out.innerHTML = `<div class="small">${durLine}</div><div class="muted">Aucun résultat</div>`; return; }
    out.innerHTML = `<div class="small" style="margin-bottom:6px">${durLine}</div>` + res.slice(0, 20).map((r) => `
      <div class="mb-result" data-id="${r.id}">
        <div>${r.by_durations ? `<span class="chip g-clean" title="Nombre de pistes et durées identiques à ce dossier">durées ✓</span> ` : ""}<b>${esc(r.title)}</b> — ${esc(r.artist)} ${r.disambiguation ? `<span class="muted">(${esc(r.disambiguation)})</span>` : ""}
          <div class="small muted">${esc(r.date)} ${esc(r.country)} · ${esc(r.format)} · ${esc(r.label)} · ${esc(r.status)}</div></div>
        <div class="small" style="text-align:right"><b class="${r.tracks === tracks.length ? "conf-high" : "conf-medium"}">${r.tracks} pistes</b><div class="muted">score ${r.score}</div></div>
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
  box.innerHTML = `<div class="muted">Chargement de la tracklist…</div>`;
  const { release: rel, mapping } = await run(() => api("GET", "/api/mb/match?" + qs({ dir, release: releaseId })));
  const byPath = Object.fromEntries(tracks.map((t) => [t.path, t]));
  const opt = (i, sel) => `<option value="${i}" ${i === sel ? "selected" : ""}>${rel.tracks[i].discs > 1 ? rel.tracks[i].disc + "-" : ""}${rel.tracks[i].position}. ${esc(rel.tracks[i].title)} (${fmtDur(rel.tracks[i].length)})</option>`;
  box.innerHTML = `
    <h2>${esc(rel.albumartist)} — ${esc(rel.title)} <span class="muted small">${esc(rel.date)} · ${rel.tracks.length} pistes</span></h2>
    ${(() => {
      const diffs = mapping.filter((m) => m.index !== null && byPath[m.path].duration && rel.tracks[m.index].length)
        .map((m) => Math.abs(byPath[m.path].duration - rel.tracks[m.index].length));
      if (!diffs.length) return "";
      const avg = diffs.reduce((a, b) => a + b, 0) / diffs.length, max = Math.max(...diffs);
      const cls = max <= 3 ? "conf-high" : avg <= 8 ? "conf-medium" : "conf-low";
      const hint = max <= 3 ? "même édition très probablement" : avg <= 8 ? "même album, autre mastering ou édition possible" : "durées éloignées : vérifiez l'édition";
      return `<p class="small ${cls}">Durées : écart moyen ${avg.toFixed(1)} s, maximum ${Math.round(max)} s sur ${diffs.length} piste(s) associée(s) — ${hint}</p>`;
    })()}
    <table class="tracks"><thead><tr><th></th><th>Fichier</th><th>Durée</th><th>Piste MusicBrainz</th><th>Score</th></tr></thead>
    <tbody>${mapping.map((m) => `
      <tr data-path="${esc(m.path)}">
        <td>${playBtn(byPath[m.path])}</td>
        <td class="small mono">${esc(byPath[m.path].filename)}<div class="muted">${esc(byPath[m.path].title || "")}</div></td>
        <td class="small">${fmtDur(byPath[m.path].duration)}</td>
        <td><select class="map" style="width:100%"><option value="">— ne pas modifier —</option>${rel.tracks.map((_, i) => opt(i, m.index)).join("")}</select></td>
        <td class="small ${m.score >= 1 ? "conf-high" : m.score >= 0.7 ? "conf-medium" : "conf-low"}" title="Correspondance titre + durée">${m.index === null ? "—" : Math.round(Math.min(m.score, 1) * 100) + " %"}</td>
      </tr>`).join("")}</tbody></table>
    <div class="actions">
      <label><input type="checkbox" class="mb-cover" ${rel.cover ? "checked" : "disabled"}> Intégrer la pochette dans les fichiers qui n'en ont pas ${rel.cover ? "" : "(indisponible)"}</label>
      <span style="flex:1"></span>
      <button class="primary mb-apply">Appliquer les tags MusicBrainz</button>
    </div>`;
  bindPlay(box);
  $(".mb-apply", box).onclick = () => run(async () => {
    const map = $$("tbody tr", box).map((tr) => {
      const v = $(".map", tr).value;
      return { path: tr.dataset.path, index: v === "" ? null : Number(v) };
    });
    const n = map.filter((m) => m.index !== null).length;
    if (!n) return toast("Aucun fichier associé à une piste MusicBrainz : choisissez les correspondances dans le tableau", true);
    const todo = editor ? editor.pending() : [];
    const msg = todo.length
      ? `Vos modifications non enregistrées (${todo.join(", ")}) vont d'abord être enregistrées, ` +
        `puis MusicBrainz écrira artiste, album, titres, n° et année sur ${n} fichier(s).\n\nLe genre n'est pas modifié par MusicBrainz.`
      : `Écrire les tags MusicBrainz sur ${n} fichier(s) ?\n\nLe genre n'est pas modifié par MusicBrainz.`;
    if (!confirm(msg)) return;
    if (todo.length) await editor.save();
    const r = await api("POST", "/api/mb/apply", { dir, release: releaseId, mapping: map, cover: $(".mb-cover", box).checked });
    toast(`${r.files} fichier(s) taggué(s)${r.cover ? " avec pochette" : ""}`);
    onApplied();
  });
}

// ------------------------------------------------------------------ review
const review = { kind: null, label: "", items: [], idx: 0, status: {}, returnTo: "#/" };
const REVIEW_STATUS = { done: "✓ validé", saved: "✎ enregistré", skip: "→ passé", ignore: "⊘ ne plus proposer" };

async function startReview(kind, label, items, returnTo) {
  if (!items.length) return toast("Rien à revoir avec ce filtre");
  Object.assign(review, { kind, label, items, idx: 0, status: {}, returnTo });
  location.hash = "#/review";
}

function reviewCounts() {
  const c = { done: 0, saved: 0, skip: 0, ignore: 0 };
  Object.values(review.status).forEach((s) => c[s]++);
  return c;
}

async function renderReview(main) {
  if (!review.items.length) { location.hash = "#/"; return; }
  const n = review.items.length, i = review.idx;
  if (i >= n) {
    const c = reviewCounts();
    main.innerHTML = `<h1>Revue terminée — ${esc(review.label)}</h1>
      <div class="cards" style="margin:16px 0">
        <div class="card"><div class="num">${c.done}</div><div class="lbl">validés</div></div>
        <div class="card"><div class="num">${c.skip}</div><div class="lbl">passés</div></div>
        <div class="card"><div class="num">${c.ignore}</div><div class="lbl">ne plus proposer</div></div>
      </div>
      <div class="toolbar"><button id="rv-prev">← Revenir au dernier</button><a class="btn primary" href="${review.returnTo}">Retour à la liste</a></div>`;
    $("#rv-prev").onclick = () => { review.idx = n - 1; renderReview(main); };
    return;
  }
  const item = review.items[i];
  const st = review.status[i];
  const c = reviewCounts();
  main.innerHTML = `
    <div class="review-bar">
      <div class="review-top">
        <b>Revue — ${esc(review.label)}</b>
        <span class="muted">${i + 1} / ${n}</span>
        <span class="small muted">✓ ${c.done} · ✎ ${c.saved} · → ${c.skip} · ⊘ ${c.ignore}</span>
        ${st ? `<span class="chip">${REVIEW_STATUS[st]}</span>` : ""}
        <span class="grow"></span>
        <a href="${review.returnTo}" class="small">Quitter la revue</a>
      </div>
      <div class="progress"><div style="width:${Math.round((100 * i) / n)}%"></div></div>
      <div class="review-actions">
        <button id="rv-prev" ${i === 0 ? "disabled" : ""} title="Ctrl+←">← Précédent</button>
        <span class="grow"></span>
        <button id="rv-ignore" title="Ctrl+I">Ne plus proposer</button>
        <button id="rv-skip" title="Ctrl+→">Passer (ne pas traiter) →</button>
        ${review.kind === "duplicate" ? "" : `<button id="rv-save" title="Ctrl+S — enregistre sans passer au suivant">✎ Enregistrer</button>`}
        <button class="primary" id="rv-ok" title="Ctrl+Entrée">${review.kind === "duplicate" ? "✓ Garder la version choisie et suivant" : "✓ Valider et suivant"}</button>
      </div>
    </div>
    <div id="rv-body"><div class="empty">Chargement…</div></div>`;

  const go = (delta) => { review.idx = Math.max(0, i + delta); renderReview(main); window.scrollTo(0, 0); };
  let validate, ignore;
  const body = $("#rv-body");

  if (review.kind === "duplicate") {
    const g = item;
    let keep = g.keep || g.best;
    body.innerHTML = `
      <p class="lead">Choisissez la version à garder ; les autres iront dans la corbeille (annulable depuis l'historique).</p>
      <div class="group"><table><thead><tr><th>Garder</th><th></th><th>Dossier</th><th>Pistes</th><th>Bitrate</th><th>Taille</th><th>Pochette</th><th>Tags</th><th>MBID</th><th>Qualité</th></tr></thead>
      <tbody>${g.members.map((m) => `
        <tr class="${m.dir === keep ? "keep" : "remove"}" data-dir="${esc(m.dir)}">
          <td><input type="radio" name="rv-keep" value="${esc(m.dir)}" ${m.dir === keep ? "checked" : ""}></td>
          <td><button class="play-first" title="Écouter la 1re piste">▶</button></td>
          <td><a href="#" class="open">${esc(m.dir)}</a> ${m.dir === g.best ? `<span class="badge" style="background:var(--ok-soft);color:var(--ok)">meilleure</span>` : ""}</td>
          <td>${m.n_tracks}</td><td>${m.avg_bitrate}${m.vbr ? " VBR" : ""}</td><td>${fmtSize(m.total_size)}</td>
          <td>${m.has_cover ? "✓" : "—"}</td>
          <td>${m.n_untagged || m.n_incomplete ? `<span class="conf-low">${m.n_untagged + m.n_incomplete} manquant(s)</span>` : "✓"}</td>
          <td>${m.has_mbid ? "✓" : "—"}</td><td class="q">${m.quality}</td>
        </tr>`).join("")}</tbody></table></div>`;
    for (const r of $$("input[type=radio]", body)) r.onchange = () => {
      keep = g.keep = r.value;
      for (const tr of $$("tbody tr", body)) tr.className = tr.dataset.dir === keep ? "keep" : "remove";
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
    const ed = await albumEditor(body, item, { review: true, onReload: () => renderReview(main) });
    validate = () => ed.save();
    $("#rv-save").onclick = () => run(async () => {
      if (!ed.pending().length) return toast("Rien à enregistrer");
      const btns = $$(".review-actions button", main);
      btns.forEach((b) => (b.disabled = true));
      try {
        const r = await ed.save();
        review.status[i] = review.status[i] || "saved";
        toast(`${r.files} fichier(s) enregistré(s) — vous restez sur cet album`);
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
    <h1>Doublons d'albums</h1>
    <p class="lead">Albums présents dans plusieurs dossiers (même artiste + même album, ou mêmes titres). La version gardée par défaut est celle de meilleure qualité :
      bitrate moyen (VBR ≥ 180 kbps bonifié), complétude (nombre de pistes), puis pochette, tags complets et IDs MusicBrainz. Les autres partent dans la corbeille.</p>
    <div class="toolbar">
      <input type="search" id="dq" placeholder="Filtrer…" value="${esc(dupState.q)}" style="width:300px">
      <label><input type="checkbox" id="dign" ${dupState.showIgnored ? "checked" : ""}> afficher les ignorés</label>
      <span class="grow"></span>
      <button class="primary" id="dreview">▶ Revue groupe par groupe</button>
    </div>
    <div id="dbatch"></div><div id="dlist"><div class="empty">Chargement…</div></div>`;
  let timer;
  $("#dq").oninput = (e) => { clearTimeout(timer); timer = setTimeout(() => { dupState.q = e.target.value; dupState.offset = 0; loadDuplicates(); }, 300); };
  $("#dign").onchange = (e) => { dupState.showIgnored = e.target.checked; loadDuplicates(); };
  $("#dreview").onclick = () => run(async () => {
    const all = (await api("GET", "/api/duplicates?" + qs({ q: dupState.q, show_ignored: dupState.showIgnored, limit: 100000 }))).items;
    const sel = dupState.selected.size ? all.filter((g) => dupState.selected.has(g.id)) : all;
    startReview("duplicate", "Doublons" + (dupState.selected.size ? " (sélection)" : ""), sel, "#/duplicates");
  });
  loadDuplicates();
}

async function loadDuplicates() {
  const data = await run(() => api("GET", "/api/duplicates?" + qs({ q: dupState.q, show_ignored: dupState.showIgnored, offset: dupState.offset, limit: 50 })));
  const box = $("#dlist");
  if (!box) return;
  const groups = data.items;
  if (!groups.length) { box.innerHTML = `<div class="empty">Aucun doublon détecté</div>`; $("#dbatch").innerHTML = ""; return; }
  box.innerHTML = groups.map((g) => {
    const keep = dupState.keep[g.id] || g.best;
    const m0 = g.members.find((m) => m.dir === keep) || g.members[0];
    return `<div class="group" data-gid="${g.id}">
      <div class="group-head">
        <input type="checkbox" class="gsel" ${dupState.selected.has(g.id) ? "checked" : ""}>
        <b>${esc(m0.main_artist || "?")} — ${esc(m0.main_album || "?")}</b>
        <span class="muted small">${g.members.length} versions</span>
        <span style="flex:1"></span>
        <button class="link gignore">${g.members.every((m) => m.ignored.includes("duplicate")) ? "Ne plus ignorer" : "Pas des doublons (ignorer)"}</button>
      </div>
      <table><thead><tr><th>Garder</th><th>Dossier</th><th>Pistes</th><th>Bitrate</th><th>Taille</th><th>Pochette</th><th>Tags</th><th>MBID</th><th>Qualité</th></tr></thead>
      <tbody>${g.members.map((m) => `
        <tr class="${m.dir === keep ? "keep" : "remove"}" data-dir="${esc(m.dir)}">
          <td><input type="radio" name="keep-${g.id}" value="${esc(m.dir)}" ${m.dir === keep ? "checked" : ""}></td>
          <td><a href="#" class="open">${esc(m.dir)}</a> ${m.dir === g.best ? `<span class="badge" style="background:var(--ok-soft);color:var(--ok)">meilleure</span>` : ""}</td>
          <td>${m.n_tracks}</td>
          <td>${m.avg_bitrate}${m.vbr ? " VBR" : ""}</td>
          <td>${fmtSize(m.total_size)}</td>
          <td>${m.has_cover ? "✓" : "—"}</td>
          <td>${m.n_untagged || m.n_incomplete ? `<span class="conf-low">${m.n_untagged + m.n_incomplete} manquant(s)</span>` : "✓"}</td>
          <td>${m.has_mbid ? "✓" : "—"}</td>
          <td class="q">${m.quality}</td>
        </tr>`).join("")}</tbody></table>
    </div>`;
  }).join("") + `
    <div class="pager">
      <span class="muted">${dupState.offset + 1}–${dupState.offset + groups.length} sur ${data.total}</span>
      <button id="dprev" ${dupState.offset === 0 ? "disabled" : ""}>‹ Précédent</button>
      <button id="dnext" ${dupState.offset + 50 >= data.total ? "disabled" : ""}>Suivant ›</button>
    </div>`;
  $("#dprev").onclick = () => { dupState.offset -= 50; loadDuplicates(); window.scrollTo(0, 0); };
  $("#dnext").onclick = () => { dupState.offset += 50; loadDuplicates(); window.scrollTo(0, 0); };
  for (const el of $$(".group", box)) {
    const g = groups.find((x) => String(x.id) === el.dataset.gid);
    $(".gsel", el).onchange = (e) => { e.target.checked ? dupState.selected.add(g.id) : dupState.selected.delete(g.id); renderDupBatch(groups); };
    for (const r of $$("input[type=radio]", el)) r.onchange = () => {
      dupState.keep[g.id] = r.value;
      for (const tr of $$("tbody tr", el)) tr.className = tr.dataset.dir === r.value ? "keep" : "remove";
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
    <b>${n} groupe(s) sélectionné(s)</b>
    <button class="link" id="dselpage">Sélectionner les groupes de la page</button>
    ${n ? `<button class="link" id="dselnone">Désélectionner</button>` : ""}
    <span style="flex:1"></span>
    <button class="danger solid" id="dresolve" ${n ? "" : "disabled"}>Garder la version choisie, mettre les autres à la corbeille</button>
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
    if (!confirm(`Déplacer ${nd} dossier(s) dans la corbeille et garder ${payload.length} version(s) ?\n(annulable depuis l'historique tant que la corbeille n'est pas vidée)`)) return;
    await api("POST", "/api/duplicates/resolve", { groups: payload });
    dupState.selected.clear(); dupState.keep = {};
    lastJobStatus = "running"; refreshStatus();
  });
}

// ------------------------------------------------------------------- genres
const G_INFER = "__infer__", G_REMOVE = "__remove__", G_KEEP = "__keep__";
const G_STATUS = { map: "à harmoniser", junk: "générique / parasite", weird: "farfelu", empty: "sans genre", clean: "propre" };
const G_FILTERS = { todo: "À traiter", weird: "Farfelus / génériques", empty: "Sans genre", clean: "Déjà propres", all: "Tous" };
const genreState = { filter: "todo", q: "", selected: new Set(), choice: {}, data: null };
let genreCanon = [];

function genreTargetLabel(t, row) {
  if (t === G_INFER) return "Genre habituel de l'artiste";
  if (t === G_REMOVE) return "Supprimer le genre";
  if (t === G_KEEP) return "Laisser tel quel";
  return t;
}

async function renderGenres(main) {
  main.innerHTML = `
    <h1>Genres</h1>
    <p class="lead">Chaque ligne est une valeur de genre trouvée dans vos fichiers. La proposition ramène chaque valeur vers un <b>genre cible</b>.
      Pour les genres farfelus, génériques ou vides, elle reprend le genre le plus fréquent du même artiste ailleurs dans la bibliothèque.
      Quand on remplace par Hip-Hop, les artistes habituellement classés Rap français le restent.
      Vos choix sont mémorisés ; chaque application est annulable depuis l'historique.</p>
    <div class="panel">
      <div class="toolbar" style="margin-bottom:6px"><b>Genres cibles</b><span class="grow"></span>
        <input id="g-add" placeholder="Ajouter un genre…" style="width:200px"><button id="g-add-btn">Ajouter</button></div>
      <div class="chips" id="g-canon"></div>
    </div>
    <div class="toolbar">
      <div class="seg" id="g-filter">${Object.entries(G_FILTERS).map(([k, v]) => `<button data-f="${k}">${v}</button>`).join("")}</div>
      <input type="search" id="g-q" placeholder="Filtrer…" value="${esc(genreState.q)}" style="width:240px">
      <span class="grow"></span>
      <button id="g-mb" title="Pour les artistes dont aucun morceau n'a de genre exploitable">Chercher les genres inconnus sur MusicBrainz</button>
    </div>
    <div id="g-batch"></div>
    <div id="g-list"><div class="empty">Chargement…</div></div>`;
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
  $("#g-mb").onclick = () => confirm("Interroger MusicBrainz pour les artistes dont le genre est inconnu ? (≈ 1 s par artiste, en tâche de fond ; les fichiers ne sont pas modifiés)") &&
    run(async () => { await api("POST", "/api/genres/musicbrainz"); lastJobStatus = "running"; refreshStatus(); });
  $("#g-add").onkeydown = (e) => e.key === "Enter" && addCanon();
  loadGenres();
}

async function loadGenres() {
  const data = await run(() => api("GET", "/api/genres"));
  genreState.data = data;
  genreCanon = data.canon;
  $("#g-canon").innerHTML = genreCanon.map((g) => `<span class="chip canon">${esc(g)} <button class="link g-rm" data-g="${esc(g)}" title="Retirer">✕</button></span>`).join("");
  for (const b of $$(".g-rm")) b.onclick = () => run(async () => {
    if (!confirm(`Retirer « ${b.dataset.g} » des genres cibles ? (les fichiers ne sont pas modifiés)`)) return;
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
  if (!rows.length) { box.innerHTML = `<div class="empty">Rien ici</div>`; drawGenreBatch(rows); return; }
  const choiceOf = (r) => genreState.choice[r.raw] ?? r.proposal;
  const options = (r) => {
    const sel = choiceOf(r);
    const inf = r.inferred.filter(([g]) => g !== "?").map(([g, n]) => `${g} ${n}`).join(", ");
    const canon = genreCanon.includes(sel) || [G_INFER, G_REMOVE, G_KEEP].includes(sel) ? genreCanon : [sel, ...genreCanon];
    return `<option value="${G_INFER}" ${sel === G_INFER ? "selected" : ""}>↳ Genre habituel de l'artiste${inf ? ` (${esc(inf)})` : " (inconnu)"}</option>
      ${canon.map((g) => `<option value="${esc(g)}" ${g === sel ? "selected" : ""}>${esc(g)}</option>`).join("")}
      <option value="${G_REMOVE}" ${sel === G_REMOVE ? "selected" : ""}>✕ Supprimer le genre</option>
      <option value="${G_KEEP}" ${sel === G_KEEP ? "selected" : ""}>= Laisser tel quel</option>`;
  };
  box.innerHTML = `<table>
    <thead><tr><th class="check"><input type="checkbox" id="g-all" title="Tout sélectionner"></th>
      <th>Genre actuel</th><th>Pistes</th><th>Dossiers</th><th>Artistes</th><th style="width:320px">Remplacer par</th><th></th></tr></thead>
    <tbody>${rows.slice(0, 600).map((r) => `
      <tr data-raw="${esc(r.raw)}">
        <td class="check"><input type="checkbox" class="g-sel" ${genreState.selected.has(r.raw) ? "checked" : ""}></td>
        <td><b>${r.raw ? esc(r.raw) : `<span class="muted">(aucun genre)</span>`}</b>
          <div><span class="chip g-${r.status}">${G_STATUS[r.status]}</span> <span class="small muted">${esc(r.reason)}</span>
          ${r.user_choice ? `<span class="chip">votre choix</span>` : ""}</div></td>
        <td>${r.tracks.toLocaleString("fr")}</td><td>${r.dirs}</td>
        <td class="small">${r.artists.map(esc).join(", ")}</td>
        <td><select class="g-target" style="width:100%">${options(r)}</select></td>
        <td style="white-space:nowrap;text-align:right">
          <button class="link g-detail">Dossiers</button>
          <button class="g-apply">Appliquer</button></td>
      </tr><tr class="hidden"><td colspan="7" class="g-detail-box"></td></tr>`).join("")}
    </tbody></table>
    ${rows.length > 600 ? `<div class="muted small">… ${rows.length - 600} valeurs de plus, affinez le filtre</div>` : ""}`;
  $("#g-all").onchange = (e) => { rows.forEach((r) => e.target.checked ? genreState.selected.add(r.raw) : genreState.selected.delete(r.raw)); drawGenres(); };
  for (const tr of $$("tr[data-raw]", box)) {
    const raw = tr.dataset.raw, r = rows.find((x) => x.raw === raw), next = tr.nextElementSibling;
    $(".g-sel", tr).onchange = (e) => { e.target.checked ? genreState.selected.add(raw) : genreState.selected.delete(raw); drawGenreBatch(rows); };
    $(".g-target", tr).onchange = (e) => { genreState.choice[raw] = e.target.value; };
    $(".g-apply", tr).onclick = () => applyGenres([{ raw, target: choiceOf(r) }]);
    $(".g-detail", tr).onclick = () => run(async () => {
      if (!next.classList.contains("hidden")) return next.classList.add("hidden");
      const dirs = await api("GET", "/api/genres/detail?" + qs({ raw }));
      $(".g-detail-box", next).innerHTML = `<table class="tracks"><thead><tr><th>Dossier</th><th>Artiste</th><th>Pistes</th><th>Genre habituel de l'artiste</th></tr></thead><tbody>
        ${dirs.slice(0, 300).map((d) => `<tr><td><a href="#" data-open="${esc(d.dir)}">${esc(d.dir)}</a></td><td>${esc(d.artist || "")}</td><td>${d.tracks}</td>
          <td>${d.inferred === "?" ? `<span class="muted">inconnu</span>` : esc(d.inferred)}</td></tr>`).join("")}</tbody></table>
        <div class="small muted">Pour traiter un album à part, ouvrez-le et modifiez son champ Genre.</div>`;
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
    <b>${sel.length} genre(s) sélectionné(s)</b> <span class="muted">${n.toLocaleString("fr")} pistes</span>
    <span style="flex:1"></span>
    <button class="primary" id="g-apply-sel" ${sel.length ? "" : "disabled"}>Appliquer le remplacement choisi</button></div>`;
  $("#g-apply-sel").onclick = () => applyGenres(sel.map((r) => ({ raw: r.raw, target: genreState.choice[r.raw] ?? r.proposal })));
}

function applyGenres(items) {
  const txt = items.slice(0, 8).map((i) => `• ${i.raw || "(aucun genre)"} → ${genreTargetLabel(i.target)}`).join("\n") + (items.length > 8 ? `\n… et ${items.length - 8} autres` : "");
  if (!confirm(`Appliquer ?\n\n${txt}`)) return;
  run(async () => {
    const r = await api("POST", "/api/genres/apply", { items });
    genreState.selected.clear();
    if (r.status === "done") { toast("Choix mémorisé"); loadGenres(); return; }
    lastJobStatus = "running"; refreshStatus();
  });
}

// ----------------------------------------------------------------- history
async function renderHistory(main) {
  const rows = await run(() => api("GET", "/api/history?limit=200"));
  main.innerHTML = `
    <h1>Historique</h1>
    <p class="lead">Chaque lot de modifications peut être annulé : les tags reprennent leur valeur précédente, les dossiers mis à la corbeille reviennent à leur place.</p>
    ${rows.length ? `<table><thead><tr><th>Date</th><th>Action</th><th>Fichiers</th><th>État</th><th></th></tr></thead><tbody>
      ${rows.map((r) => `<tr data-batch="${esc(r.batch)}">
        <td class="small">${esc(r.ts)}</td>
        <td>${esc(r.label)}</td>
        <td>${r.n}${r.n_trash ? ` <span class="muted small">(${r.n_trash} dossier(s) en corbeille)</span>` : ""}</td>
        <td>${r.purged === 2 ? `<span class="muted">définitif</span>` : r.undone ? `<span class="muted">annulé</span>` : `<span class="conf-high">appliqué</span>`}</td>
        <td style="text-align:right"><button class="link detail">Détails</button>
          ${!r.undone ? `<button class="undo">Annuler</button>` : ""}</td>
      </tr><tr class="hidden"><td colspan="5" class="detail-box"></td></tr>`).join("")}
    </tbody></table>` : `<div class="empty">Aucune modification pour l'instant</div>`}`;
  for (const tr of $$("tr[data-batch]", main)) {
    const batch = tr.dataset.batch, next = tr.nextElementSibling;
    $(".detail", tr).onclick = () => run(async () => {
      if (!next.classList.contains("hidden")) return next.classList.add("hidden");
      const items = await api("GET", `/api/history/${encodeURIComponent(batch)}`);
      const fmt = (o) => Object.entries(o).map(([k, v]) => `${k}=${v === null ? "∅" : v}`).join(", ");
      $(".detail-box", next).innerHTML = items.slice(0, 500).map((i) => `<div class="small"><span class="mono">${esc(i.path)}</span><br>
        <span class="muted">${i.action === "trash" ? "→ " + esc(i.after.dst) : esc(fmt(i.before)) + " <span class='arrow'>→</span> " + esc(fmt(i.after))}</span></div>`).join("") +
        (items.length > 500 ? `<div class="muted">… ${items.length - 500} de plus</div>` : "");
      next.classList.remove("hidden");
    });
    const u = $(".undo", tr);
    if (u) u.onclick = () => confirm("Annuler ce lot de modifications ?") && run(async () => {
      const r = await api("POST", `/api/history/${encodeURIComponent(batch)}/undo`);
      toast(`${r.undone} opération(s) annulée(s)${r.errors.length ? `, ${r.errors.length} erreur(s) : ${r.errors[0]}` : ""}`, r.errors.length > 0);
      refreshStatus(); render();
    });
  }
}

// -------------------------------------------------------------------- boot
function syncThemeButtons() {
  const t = window.fmtTheme ? window.fmtTheme.get() : "auto";
  for (const b of $$("[data-theme-choice]")) b.setAttribute("aria-pressed", String(b.dataset.themeChoice === t));
}
for (const b of $$("[data-theme-choice]")) b.onclick = () => { window.fmtTheme.set(b.dataset.themeChoice); syncThemeButtons(); };
syncThemeButtons();
$("#logout").onclick = () => api("POST", "/api/logout").finally(() => location.replace("/login"));
api("GET", "/api/genres/canon").then((r) => { genreCanon = r.canon; }).catch(() => {});
refreshStatus().then(render);
