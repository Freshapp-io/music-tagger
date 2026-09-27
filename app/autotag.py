"""Automatic tagging from MusicBrainz, in two steps:

1. analyse(): for each folder, look releases up by track lengths (CD table of
   contents) and by name, match the files to each candidate and give it a
   0-100 confidence score. Results are stored in the `autotag` table.
2. apply(): write the chosen release on the folders the user kept, with the
   options (album artist, genre, cover, only fill empty fields).
"""
import difflib
import json
from datetime import datetime
from statistics import mean

from . import analysis, db, fixes, genres, musicbrainz
from .parsing import fold, norm_album, norm_title, parse_dir, parse_filename, primary_artist

MAX_CANDIDATES = 4
AMBIGUITY_GAP = 5          # two different albums closer than this: do not auto-apply
DEFAULT_OPTIONS = {
    "min_score": 95, "albumartist_mode": "mb", "albumartist_value": "",
    "fill_genre": True, "cover": True, "only_empty": False, "strict_count": False,
}


def _ratio(a, b):
    return difflib.SequenceMatcher(None, a, b).ratio() if a and b else 0.0


def _duration_score(diff):
    """1 up to 2 s apart, 0 from 12 s."""
    return max(0.0, min(1.0, 1 - (diff - 2) / 10))


def evaluate(files, rel, hints, via_toc=False):
    """Score (0-100) of `rel` for these files, with the details behind it."""
    mapping = musicbrainz.match(files, rel)
    by_path = {f["path"]: f for f in files}
    mapped = [m for m in mapping if m["index"] is not None]
    coverage = len(mapped) / len(files) if files else 0

    diffs, dur_scores, title_scores = [], [], []
    for m in mapped:
        f, t = by_path[m["path"]], rel["tracks"][m["index"]]
        if f.get("duration") and t["length"]:
            d = abs(f["duration"] - t["length"])
            diffs.append(d)
            dur_scores.append(_duration_score(d))
        own = f.get("title") or parse_filename(f["filename"]).get("title")
        if own:
            title_scores.append(_ratio(norm_title(own), norm_title(t["title"])))

    parts = {}
    if dur_scores:
        parts["durations"] = mean(dur_scores)
    if title_scores and len(title_scores) >= max(1, len(mapped) // 2):
        parts["titles"] = mean(title_scores)
    names = []
    if hints.get("artist"):
        names.append(_ratio(fold(primary_artist(hints["artist"])), fold(primary_artist(rel["albumartist"]))))
    if hints.get("album"):
        names.append(_ratio(norm_album(hints["album"]), norm_album(rel["title"])))
    if names:
        parts["names"] = mean(names)

    weights = {"durations": 45, "titles": 30, "names": 25}
    if parts:
        score = sum(weights[k] * v for k, v in parts.items()) / sum(weights[k] for k in parts) * 100
    else:
        score = 0.0
    score *= coverage
    if set(parts) == {"durations"}:
        # Lengths alone: convincing for a full album, much less for 3 tracks.
        score = min(score, 70 + 3 * len(files))
    medium = [t for t in rel["tracks"] if t["disc"] == (rel["tracks"][mapped[0]["index"]]["disc"] if mapped else 1)]
    if mapped and len(files) < len(medium):
        score *= 0.95        # incomplete album: tags are right, but be careful
    return round(score, 1), {
        "medium_tracks": len(medium),
        "coverage": round(coverage, 3),
        "durations": round(parts["durations"] * 100) if "durations" in parts else None,
        "titles": round(parts["titles"] * 100) if "titles" in parts else None,
        "names": round(parts["names"] * 100) if "names" in parts else None,
        "avg_gap": round(mean(diffs), 1) if diffs else None,
        "max_gap": round(max(diffs)) if diffs else None,
        "via_durations": via_toc,
        "mapping": [{"path": m["path"], "index": m["index"]} for m in mapping],
    }


def analyse_dir(rel_dir):
    """Best candidate for a folder: dict ready for the autotag table."""
    with db.session() as c:
        files = fixes.tracks_of(c, rel_dir)
        a = c.execute("SELECT suggestion FROM albums WHERE dir=?", (rel_dir,)).fetchone()
    sug = json.loads(a["suggestion"]) if a else {}
    hints = {"artist": sug.get("albumartist") if sug.get("albumartist") != analysis.VARIOUS else None,
             "album": sug.get("album")}
    if not files:
        return {"status": "none", "score": 0, "details": {"reason": "aucun fichier"}}
    durations = [f["duration"] for f in files]

    candidates = []                       # (release id, found by durations)
    if all(durations):
        candidates += [(r["id"], True) for r in musicbrainz.by_durations(durations)[:MAX_CANDIDATES]]
    if hints["album"] or hints["artist"]:
        found = musicbrainz.search(hints["artist"] or "", hints["album"] or "", len(files))
        found.sort(key=lambda r: (r["tracks"] != len(files), -r["score"]))   # same track count first
        for r in found:
            if r["tracks"] >= len(files) and r["score"] >= 80 and r["id"] not in {c[0] for c in candidates}:
                candidates.append((r["id"], False))
            if len(candidates) >= MAX_CANDIDATES + 2:
                break
    candidates = candidates[:MAX_CANDIDATES + 1]
    if not candidates:
        return {"status": "none", "score": 0,
                "details": {"reason": "aucune édition trouvée (ni par les durées, ni par le nom)"}}

    scored = []
    for rid, via_toc in candidates:
        rel = musicbrainz.release(rid)
        score, details = evaluate(files, rel, hints, via_toc)
        scored.append((score, rel, details))
    scored.sort(key=lambda x: (-x[0], x[2]["avg_gap"] if x[2]["avg_gap"] is not None else 99))
    status, best_score, best, details = _decide(scored)
    details["hints"] = hints
    details["candidates"] = [{"id": r["id"], "title": r["title"], "artist": r["albumartist"],
                              "date": r["date"], "score": s, "tracks": d["medium_tracks"]} for s, r, d in scored]
    # Same decision restricted to releases whose disc has exactly as many tracks
    # as the folder: used when the "respect the track count" option is on.
    exact = [x for x in scored if x[2]["medium_tracks"] == len(files)]
    if exact:
        s_status, s_score, s_best, s_details = _decide(exact)
        details["strict"] = {"status": s_status, "score": s_score, "release_id": s_best["id"], "details": s_details}
    else:
        details["strict"] = {"status": "rejected", "score": 0, "release_id": None,
                             "details": {"reason": f"aucune édition avec {len(files)} pistes"}}
    return {"status": status, "score": best_score, "release_id": best["id"], "details": details}


def _decide(scored):
    """Best of the scored candidates, flagged ambiguous / partial when needed."""
    best_score, best, details = scored[0]
    details = dict(details)
    # Another *album* (different release group and title) almost as good -> ambiguous.
    rival = next((s for s in scored[1:] if s[1]["release_group_id"] != best["release_group_id"]
                  and norm_album(s[1]["title"]) != norm_album(best["title"])), None)
    status = "ok"
    if rival and rival[0] >= best_score - AMBIGUITY_GAP:
        status = "ambiguous"
        details["rival"] = {"id": rival[1]["id"], "title": rival[1]["title"],
                            "artist": rival[1]["albumartist"], "score": rival[0]}
    if details["coverage"] < 1:
        status = "partial"
    details["release"] = {"id": best["id"], "title": best["title"], "artist": best["albumartist"],
                          "date": best["date"], "tracks": len(best["tracks"])}
    return status, best_score, best, details


def effective(row, strict_count):
    """(status, score, release_id, details) of a stored analysis, for the given
    option. Analyses made before the option existed fall back on the release
    total when it equals the folder's track count."""
    details = row["details"] if isinstance(row["details"], dict) else json.loads(row["details"] or "{}")
    if row["status"] in ("applied", "none", "error") or not strict_count:
        return row["status"], row["score"], row["release_id"], details
    strict = details.get("strict")
    if strict:
        common = {k: v for k, v in details.items() if k in ("hints", "candidates", "strict")}
        return strict["status"], strict["score"], strict["release_id"], {**common, **strict["details"]}
    rel = details.get("release") or {}
    n = len(details.get("mapping") or [])
    if rel and n and rel.get("tracks") == n:
        return row["status"], row["score"], row["release_id"], details
    return "rejected", 0, None, {**details, "reason": "nombre de pistes différent (réanalyser pour chercher une autre édition)"}


def analyse(job, dirs, force=False):
    with db.session() as c:
        done = {r[0] for r in c.execute("SELECT dir FROM autotag WHERE status <> 'error'")}
    todo = [d for d in dirs if force or d not in done]
    job.total = len(todo)
    counts = {}
    for d in todo:
        job.step(f"Analyse : {d}")
        try:
            res = analyse_dir(d)
        except Exception as e:
            job.error(d, e)
            res = {"status": "error", "score": 0, "details": {"reason": str(e)[:200]}}
        counts[res["status"]] = counts.get(res["status"], 0) + 1
        with db.session() as c:
            c.execute("INSERT OR REPLACE INTO autotag(dir, analyzed, release_id, score, status, details, applied) "
                      "VALUES (?,?,?,?,?,?,NULL)",
                      (d, datetime.now().isoformat(timespec="seconds"), res.get("release_id"), res["score"],
                       res["status"], json.dumps(res["details"], ensure_ascii=False)))
    job.message = f"{len(todo)} dossier(s) analysé(s) : " + ", ".join(f"{v} {k}" for k, v in counts.items())
    return counts


def apply(job, dirs, options):
    opts = {**DEFAULT_OPTIONS, **(options or {})}
    with db.session() as c:
        db.set_meta(c, "autotag_options", opts)
        rows = {r["dir"]: dict(r) for r in c.execute("SELECT * FROM autotag WHERE status <> 'applied'")}
    todo = [d for d in dirs if d in rows]
    job.total = len(todo)
    infer = None
    if opts["fill_genre"]:
        infer = genres.Inferer(genres._load(), genres.canon_list())
    batch = fixes.new_batch()
    touched, n_files = set(), 0
    for d in todo:
        job.step(f"Tag auto : {d}")
        status, _, release_id, details = effective(rows[d], opts["strict_count"])
        if status not in ("ok", "ambiguous", "partial") or not release_id:
            job.error(d, details.get("reason") or status)
            continue
        try:
            rel = musicbrainz.release(release_id)
            changes = musicbrainz.changes_for(rel, details["mapping"])
            with db.session() as c:
                current = {t["path"]: t for t in fixes.tracks_of(c, d)}
            folder_artist = (details.get("hints") or {}).get("artist")
            for path, ch in changes.items():
                t = current.get(path, {})
                if opts["albumartist_mode"] == "fixed" and opts["albumartist_value"].strip():
                    ch["albumartist"] = opts["albumartist_value"].strip()
                elif opts["albumartist_mode"] == "folder":
                    fa = folder_artist or parse_dir(d).get("artist")
                    if fa:
                        ch["albumartist"] = fa
                if infer and not t.get("genre"):
                    g = infer({**t, "albumartist": ch.get("albumartist"), "artist": ch.get("artist")})
                    if g:
                        ch["genre"] = g
                if opts["only_empty"]:
                    for k in list(ch):
                        if k in ("albumartist", "artist", "album", "title", "track", "disc", "year", "genre") and t.get(k):
                            ch.pop(k)
            cover = musicbrainz.cover(rel["id"]) if opts["cover"] and rel.get("cover") else None
            _, n, ds = fixes.write_many(changes, f"Tag auto MusicBrainz ({len(todo)} dossiers)", batch,
                                        cover=cover, job=job)
            n_files += n
            touched |= ds | {d}
            with db.session() as c:
                c.execute("UPDATE autotag SET status='applied', applied=? WHERE dir=?",
                          (datetime.now().isoformat(timespec="seconds"), d))
        except Exception as e:
            job.error(d, e)
    job.message = "Mise à jour de l'analyse…"
    analysis.analyze_dirs(touched)
    job.message = f"{n_files} fichier(s) taggué(s) dans {len(touched)} dossier(s)"
    return {"batch": batch, "files": n_files, "dirs": len(touched)}
