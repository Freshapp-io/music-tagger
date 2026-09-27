import hashlib
import json
import os
import secrets
import time
from contextlib import asynccontextmanager
from typing import Optional

import httpx
from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import FileResponse, JSONResponse, RedirectResponse, Response
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from . import analysis, auth, config, db, fixes, genres, jobs, musicbrainz, scanner

STATIC = os.path.join(os.path.dirname(__file__), "static")


@asynccontextmanager
async def lifespan(_app):
    db.init()
    auth.password_setting()      # prints a generated password on first start
    yield


app = FastAPI(title=config.APP_NAME, version=config.VERSION, lifespan=lifespan)
app.mount("/static", StaticFiles(directory=STATIC), name="static")

ISSUE_KINDS = ("untagged", "various", "inconsistent", "duplicate")


@app.exception_handler(jobs.Busy)
def busy_handler(_: Request, exc: jobs.Busy):
    return JSONResponse({"detail": str(exc)}, status_code=409)


@app.exception_handler(ValueError)
def value_handler(_: Request, exc: ValueError):
    return JSONResponse({"detail": str(exc)}, status_code=400)


# --------------------------------------------------------------------- auth

@app.middleware("http")
async def require_login(request: Request, call_next):
    path = request.url.path
    if auth.is_public(path) or auth.read_token(request.cookies.get(auth.COOKIE, "")):
        response = await call_next(request)
    elif path.startswith("/api/"):
        response = JSONResponse({"detail": "Authentification requise"}, status_code=401)
    else:
        response = RedirectResponse("/login", status_code=303)
    response.headers.setdefault("X-Frame-Options", "DENY")
    response.headers.setdefault("X-Content-Type-Options", "nosniff")
    response.headers.setdefault("Referrer-Policy", "same-origin")
    return response


def _client_ip(request: Request):
    return request.client.host if request.client else "?"


def _secure(request: Request):
    if config.COOKIE_SECURE in ("true", "1", "yes"):
        return True
    if config.COOKIE_SECURE in ("false", "0", "no"):
        return False
    proto = request.headers.get("x-forwarded-proto", request.url.scheme)
    return proto.split(",")[0].strip() == "https"


@app.get("/login")
def login_page():
    return FileResponse(os.path.join(STATIC, "login.html"))


@app.get("/healthz")
def healthz():
    return {"ok": True}


class LoginReq(BaseModel):
    user: str
    password: str


@app.post("/api/login")
def login(req: LoginReq, request: Request):
    ip = _client_ip(request)
    wait = auth.seconds_locked(ip)
    if wait:
        raise HTTPException(429, f"Trop de tentatives. Réessayez dans {wait // 60 + 1} min.")
    if not auth.check_credentials(req.user, req.password):
        auth.record_failure(ip)
        time.sleep(0.5)
        raise HTTPException(401, "Identifiant ou mot de passe incorrect")
    auth.clear_failures(ip)
    response = JSONResponse({"ok": True})
    response.set_cookie(auth.COOKIE, auth.make_token(req.user), max_age=auth.SESSION_DAYS * 86400,
                        httponly=True, samesite="strict", secure=_secure(request), path="/")
    return response


@app.post("/api/logout")
def logout():
    response = JSONResponse({"ok": True})
    response.delete_cookie(auth.COOKIE, path="/")
    return response


@app.get("/")
def index():
    return FileResponse(os.path.join(STATIC, "index.html"))


def _album(r, ignored=()):
    d = dict(r)
    for k in ("artists", "albumartists", "albums", "years", "issues", "suggestion"):
        if k in d and d[k] is not None:
            d[k] = json.loads(d[k])
    d.pop("titles", None)
    d["ignored"] = sorted(ignored)
    return d


def _ignores(c):
    out = {}
    for r in c.execute("SELECT dir, kind FROM ignores"):
        out.setdefault(r["dir"], set()).add(r["kind"])
    return out


# ------------------------------------------------------------------- status

@app.get("/api/status")
def status():
    with db.session() as c:
        n_tracks = c.execute("SELECT COUNT(*) FROM tracks").fetchone()[0]
        n_albums = c.execute("SELECT COUNT(*) FROM albums").fetchone()[0]
        ign = _ignores(c)
        counts = {k: 0 for k in ISSUE_KINDS}
        dup_groups = set()
        for r in c.execute("SELECT dir, issues, dup_group FROM albums"):
            issues = json.loads(r["issues"])
            for k in ISSUE_KINDS:
                if k in issues and k not in ign.get(r["dir"], ()):
                    counts[k] += 1
            if r["dup_group"] and "duplicate" not in ign.get(r["dir"], ()):
                dup_groups.add(r["dup_group"])
        counts["duplicate_groups"] = len(dup_groups)
        last_scan = db.get_meta(c, "last_scan")
    j = jobs.current()
    return {
        "music_root": str(config.MUSIC_ROOT), "root_exists": config.MUSIC_ROOT.is_dir(),
        "trash_dir": str(config.TRASH_DIR),
        "tracks": n_tracks, "albums": n_albums, "counts": counts, "last_scan": last_scan,
        "job": j.as_dict() if j else None,
        "navidrome": bool(config.NAVIDROME_URL), "version": config.VERSION, "user": config.APP_USER,
    }


class ScanReq(BaseModel):
    full: bool = False


@app.post("/api/scan")
def scan(req: ScanReq):
    if not config.MUSIC_ROOT.is_dir():
        raise HTTPException(400, f"{config.MUSIC_ROOT} introuvable : vérifiez le volume monté")
    j = jobs.start("scan", "Scan complet" if req.full else "Scan", scanner.scan, full=req.full)
    return j.as_dict()


@app.post("/api/reanalyze")
def reanalyze():
    return jobs.start("analyze", "Analyse", lambda job: analysis.analyze_all()).as_dict()


# ------------------------------------------------------------------- albums

SORTS = {
    "dir": "dir COLLATE NOCASE",
    "tracks": "n_tracks DESC",
    "bitrate": "avg_bitrate DESC",
    "artist": "main_artist COLLATE NOCASE",
}


@app.get("/api/albums")
def albums(issue: str = "all", q: str = "", sub: str = "", confidence: str = "",
           show_ignored: bool = False, sort: str = "dir", offset: int = 0, limit: int = 100):
    where, params = [], []
    if issue != "all":
        where.append("issues LIKE ?")
        params.append(f'%"{issue}"%')
    if sub:
        where.append("issues LIKE ?")
        params.append(f'%"{sub}"%')
    if confidence:
        where.append("json_extract(suggestion, '$.confidence') = ?")
        params.append(confidence)
    if q:
        for word in q.split():
            where.append("(dir LIKE ? OR main_artist LIKE ? OR main_album LIKE ? OR artists LIKE ?)")
            params += [f"%{word}%"] * 4
    if not show_ignored and issue != "all":
        where.append("NOT EXISTS (SELECT 1 FROM ignores i WHERE i.dir = albums.dir AND i.kind = ?)")
        params.append(issue)
    sql = "FROM albums" + (" WHERE " + " AND ".join(where) if where else "")
    with db.session() as c:
        total = c.execute("SELECT COUNT(*) " + sql, params).fetchone()[0]
        rows = c.execute(f"SELECT * {sql} ORDER BY {SORTS.get(sort, SORTS['dir'])} LIMIT ? OFFSET ?",
                         params + [min(limit, 1000), offset]).fetchall()
        ign = _ignores(c)
    return {"total": total, "items": [_album(r, ign.get(r["dir"], ())) for r in rows]}


@app.get("/api/album")
def album(dir: str):
    with db.session() as c:
        a = c.execute("SELECT * FROM albums WHERE dir=?", (dir,)).fetchone()
        if not a:
            raise HTTPException(404, "dossier inconnu (relancez un scan ?)")
        tracks = fixes.tracks_of(c, dir)
        ign = _ignores(c).get(dir, ())
        others = []
        if a["dup_group"]:
            others = [_album(r) for r in c.execute(
                "SELECT * FROM albums WHERE dup_group=? AND dir<>?", (a["dup_group"], dir))]
    alb = _album(a, ign)
    guesses = fixes.track_guesses(dir, tracks, alb["suggestion"].get("albumartist"))
    for t in tracks:
        t["guess"] = guesses.get(t["path"], {})
    extra = []
    try:
        extra = sorted(e.name for e in os.scandir(scanner.absolute(dir))
                       if e.is_file() and not e.name.lower().endswith(config.AUDIO_EXT))
    except OSError:
        pass
    return {"album": alb, "tracks": tracks, "duplicates": others, "other_files": extra}


class SaveReq(BaseModel):
    dir: str
    album: dict = {}
    tracks: dict = {}


@app.post("/api/album/save")
def album_save(req: SaveReq):
    with jobs.acquire_or_busy():
        return fixes.save_album(req.dir, req.album, req.tracks)


@app.get("/api/album/preview")
def album_preview(dir: str, fields: str = "albumartist,album,year,compilation,tracks"):
    return fixes.suggestion_changes(dir, tuple(fields.split(",")))


class BatchReq(BaseModel):
    dirs: list[str]
    fields: list[str] = ["albumartist", "album", "year", "compilation", "tracks"]


@app.post("/api/batch/apply")
def batch_apply(req: BatchReq):
    j = jobs.start("apply", f"Application des suggestions ({len(req.dirs)} dossiers)",
                   fixes.apply_suggestions, req.dirs, tuple(req.fields))
    return j.as_dict()


class CompilationReq(BaseModel):
    dirs: list[str]


@app.post("/api/batch/compilation")
def batch_compilation(req: CompilationReq):
    """Mark folders as genuine compilations: Various Artists + TCMP=1."""
    def run(job, dirs):
        job.total = len(dirs)
        batch = fixes.new_batch()
        n = 0
        for d in dirs:
            job.step(d)
            with db.session() as c:
                paths = [t["path"] for t in fixes.tracks_of(c, d)]
            _, k, _ = fixes.write_many(
                {p: {"albumartist": analysis.VARIOUS, "compilation": "1"} for p in paths},
                "Marqué comme compilation", batch, job=job)
            n += k
        analysis.analyze_dirs(dirs)
        return {"batch": batch, "files": n}
    return jobs.start("compilation", f"Compilations ({len(req.dirs)} dossiers)", run, req.dirs).as_dict()


class IgnoreReq(BaseModel):
    dirs: list[str]
    kind: str
    value: bool = True


@app.post("/api/ignore")
def ignore(req: IgnoreReq):
    with db.session() as c:
        for d in req.dirs:
            if req.value:
                c.execute("INSERT OR IGNORE INTO ignores(dir, kind) VALUES (?, ?)", (d, req.kind))
            else:
                c.execute("DELETE FROM ignores WHERE dir=? AND kind=?", (d, req.kind))
    return {"ok": True}


# --------------------------------------------------------------- duplicates

@app.get("/api/duplicates")
def duplicates(q: str = "", show_ignored: bool = False, offset: int = 0, limit: int = 50):
    with db.session() as c:
        ign = _ignores(c)
        rows = c.execute("SELECT * FROM albums WHERE dup_group IS NOT NULL ORDER BY dup_group, quality DESC").fetchall()
    groups = {}
    for r in rows:
        groups.setdefault(r["dup_group"], []).append(_album(r, ign.get(r["dir"], ())))
    out = []
    for gid, members in groups.items():
        if not show_ignored and all("duplicate" in m["ignored"] for m in members):
            continue
        if q and not any(q.lower() in (m["dir"] + " " + (m["main_artist"] or "")).lower() for m in members):
            continue
        best = max(members, key=lambda m: m["quality"] or 0)
        out.append({"id": gid, "best": best["dir"], "members": members})
    out.sort(key=lambda g: g["members"][0]["dir"].lower())
    return {"total": len(out), "items": out[offset:offset + limit]}


class Resolve(BaseModel):
    keep: str
    remove: list[str]


class ResolveReq(BaseModel):
    groups: list[Resolve]
    sync: bool = False       # review mode: one group, answer when done


@app.post("/api/duplicates/resolve")
def duplicates_resolve(req: ResolveReq):
    groups = [g.model_dump() for g in req.groups]
    n = sum(len(g["remove"]) for g in groups)
    if req.sync:
        with jobs.acquire_or_busy():
            job = jobs.Job("dedupe", "Doublons")
            res = fixes.resolve_duplicates(job, groups)
        if job.errors:
            raise HTTPException(500, "; ".join(f"{e['where']}: {e['error']}" for e in job.errors))
        return res
    return jobs.start("dedupe", f"Doublons : {n} dossiers vers la corbeille",
                      fixes.resolve_duplicates, groups).as_dict()


# -------------------------------------------------------------- musicbrainz

@app.get("/api/mb/search")
def mb_search(artist: str = "", album: str = "", n: Optional[int] = None):
    try:
        return musicbrainz.search(artist, album, n)
    except httpx.HTTPError as e:
        raise HTTPException(502, f"MusicBrainz : {e}")


@app.get("/api/mb/by-durations")
def mb_by_durations(dir: str):
    """Look the folder up on MusicBrainz from its track lengths and order only."""
    with db.session() as c:
        files = fixes.tracks_of(c, dir)
    durations = [f["duration"] for f in files]
    try:
        releases = musicbrainz.by_durations(durations)
    except httpx.HTTPError as e:
        raise HTTPException(502, f"MusicBrainz : {e}")
    return {"tracks": len(files), "complete": all(durations), "releases": releases}


@app.get("/api/mb/recordings")
def mb_recordings(artist: str = "", title: str = ""):
    try:
        return musicbrainz.search_recordings(artist, title)
    except httpx.HTTPError as e:
        raise HTTPException(502, f"MusicBrainz : {e}")


@app.get("/api/mb/match")
def mb_match(dir: str, release: str):
    try:
        rel = musicbrainz.release(release)
    except httpx.HTTPError as e:
        raise HTTPException(502, f"MusicBrainz : {e}")
    with db.session() as c:
        files = fixes.tracks_of(c, dir)
    return {"release": rel, "mapping": musicbrainz.match(files, rel)}


class MbApply(BaseModel):
    dir: str
    release: str
    mapping: list[dict]
    cover: bool = False


@app.post("/api/mb/apply")
def mb_apply(req: MbApply):
    rel = musicbrainz.release(req.release)
    changes = musicbrainz.changes_for(rel, req.mapping)
    cover = musicbrainz.cover(req.release) if req.cover else None
    with jobs.acquire_or_busy():
        batch, n, dirs = fixes.write_many(changes, f"MusicBrainz : {rel['albumartist']} – {rel['title']}", cover=cover)
        analysis.analyze_dirs(dirs | {req.dir})
    return {"batch": batch, "files": n, "cover": bool(cover)}


# ------------------------------------------------------------------- genres

@app.get("/api/genres")
def genres_list():
    return genres.summary()


@app.post("/api/genres/musicbrainz")
def genres_musicbrainz():
    return jobs.start("genres-mb", "Genres des artistes sur MusicBrainz", genres.mb_lookup).as_dict()


@app.get("/api/genres/detail")
def genres_detail(raw: str = ""):
    return genres.detail(raw)


class CanonReq(BaseModel):
    genres: list[str]


@app.get("/api/genres/canon")
def genres_canon_get():
    return {"canon": genres.canon_list()}


@app.put("/api/genres/canon")
def genres_canon(req: CanonReq):
    genres.set_canon_list(req.genres)
    return {"canon": genres.canon_list()}


class GenreItem(BaseModel):
    raw: str = ""
    target: str


class GenreApplyReq(BaseModel):
    items: list[GenreItem]


@app.post("/api/genres/apply")
def genres_apply(req: GenreApplyReq):
    items = [i.model_dump() for i in req.items]
    keep = [i for i in items if i["target"] == genres.KEEP]
    for i in keep:                       # 'leave alone' is just remembered
        genres.set_override(i["raw"], genres.KEEP)
    todo = [i for i in items if i["target"] != genres.KEEP]
    if not todo:
        return {"status": "done", "kept": len(keep)}
    return jobs.start("genres", f"Genres ({len(todo)} valeur(s))", genres.apply, todo).as_dict()


# ------------------------------------------------------------------ history

@app.get("/api/history")
def history(limit: int = 100):
    with db.session() as c:
        rows = c.execute("""
            SELECT batch, MIN(ts) ts, MAX(label) label, COUNT(*) n,
                   SUM(action='trash') n_trash, MIN(undone) undone, MAX(undone) purged
            FROM history GROUP BY batch ORDER BY MIN(id) DESC LIMIT ?""", (limit,)).fetchall()
    return [dict(r) for r in rows]


@app.get("/api/history/{batch}")
def history_detail(batch: str):
    with db.session() as c:
        rows = c.execute("SELECT * FROM history WHERE batch=? ORDER BY id", (batch,)).fetchall()
    return [{**dict(r), "before": json.loads(r["before"]), "after": json.loads(r["after"])} for r in rows]


@app.post("/api/history/{batch}/undo")
def history_undo(batch: str):
    with jobs.acquire_or_busy():
        return fixes.undo(batch)


# -------------------------------------------------------------------- trash

@app.get("/api/trash")
def trash():
    return fixes.trash_info()


@app.post("/api/trash/empty")
def trash_empty():
    with jobs.acquire_or_busy():
        fixes.empty_trash()
    return fixes.trash_info()


# -------------------------------------------------------------------- audio

@app.get("/api/audio")
def audio(path: str):
    p = scanner.absolute(path)
    if not p.suffix.lower() in config.AUDIO_EXT or not p.is_file():
        raise HTTPException(404)
    # FileResponse answers Range requests, so the player can seek.
    return FileResponse(str(p), media_type="audio/mpeg")


# ------------------------------------------------------------------- covers

@app.get("/api/cover")
def cover(path: str):
    from mutagen.id3 import ID3
    try:
        tags = ID3(str(scanner.absolute(path)))
        pics = tags.getall("APIC")
    except Exception:
        pics = []
    if not pics:
        raise HTTPException(404)
    return Response(pics[0].data, media_type=pics[0].mime or "image/jpeg",
                    headers={"Cache-Control": "max-age=3600"})


# ---------------------------------------------------------------- navidrome

@app.post("/api/navidrome/scan")
def navidrome_scan():
    if not config.NAVIDROME_URL:
        raise HTTPException(400, "NAVIDROME_URL non configuré")
    salt = secrets.token_hex(6)
    token = hashlib.md5((config.NAVIDROME_PASSWORD + salt).encode()).hexdigest()
    try:
        r = httpx.get(config.NAVIDROME_URL.rstrip("/") + "/rest/startScan", timeout=15, params={
            "u": config.NAVIDROME_USER, "t": token, "s": salt, "v": "1.16.1", "c": "freshapp-music-tagger", "f": "json"})
        body = r.json().get("subsonic-response", {})
    except (httpx.HTTPError, ValueError) as e:
        raise HTTPException(502, f"Navidrome : {e}")
    if body.get("status") != "ok":
        raise HTTPException(502, f"Navidrome : {body.get('error', {}).get('message', body)}")
    return {"ok": True}
