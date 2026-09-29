"""Operations that modify the library. All of them are recorded in `history`
and can be undone batch by batch."""
import json
import os
import shutil
import uuid
from datetime import datetime

from . import analysis, config, db, scanner, tagger
from .parsing import fix_mojibake, is_various, parse_dir, parse_filename

ALBUM_FIELDS = ("albumartist", "album", "year", "genre", "compilation")


def new_batch():
    return datetime.now().strftime("%Y%m%d-%H%M%S-") + uuid.uuid4().hex[:4]


def _log(c, batch, label, action, path, before, after):
    c.execute(
        "INSERT INTO history(batch, label, action, path, before, after) VALUES (?,?,?,?,?,?)",
        (batch, label, action, path, json.dumps(before, ensure_ascii=False),
         json.dumps(after, ensure_ascii=False)),
    )


def write_many(changes_by_path, label, batch=None, cover=None, job=None):
    """changes_by_path: {rel_path: {field: value}}. Returns number of files changed."""
    batch = batch or new_batch()
    changed, dirs = 0, set()
    with db.session() as c:
        for path, changes in changes_by_path.items():
            if not changes and not cover:
                continue
            try:
                before, after = tagger.write(str(scanner.absolute(path)), changes, cover=cover)
            except Exception as e:
                if job:
                    job.error(path, e)
                    continue
                raise
            if after or before:
                _log(c, batch, label, "tags", path, before, after)
                changed += 1
            dirs.add(os.path.dirname(path))
    if changes_by_path:
        scanner.rescan_paths(list(changes_by_path))
    return batch, changed, dirs


def tracks_of(c, rel_dir):
    return [dict(r) for r in c.execute(
        # no disc number = disc 1, so that mixed '1/1' / empty discs keep the track order
        "SELECT * FROM tracks WHERE dir=? ORDER BY COALESCE(NULLIF(CAST(disc AS INTEGER), 0), 1), "
        "CAST(track AS INTEGER), filename",
        (rel_dir,))]


def track_guesses(rel_dir, tracks, albumartist=None):
    """Per-file guesses from file names (used for untagged / incomplete files)."""
    folder = parse_dir(rel_dir)
    hint = albumartist or folder.get("artist")
    if is_various(hint):
        hint = None   # compilation: file names are usually 'Artist - Title'
    out = {}
    for t in tracks:
        g = parse_filename(t["filename"], artist_hint=hint)
        if "disc" not in g and folder.get("disc"):
            g["disc"] = folder["disc"]
        out[t["path"]] = g
    return out


def suggestion_changes(rel_dir, fields=("albumartist", "album", "year", "compilation", "tracks")):
    """Changes that applying the stored suggestion to a folder would make."""
    with db.session() as c:
        a = c.execute("SELECT suggestion FROM albums WHERE dir=?", (rel_dir,)).fetchone()
        tracks = tracks_of(c, rel_dir)
    if not a:
        return {}
    sug = json.loads(a["suggestion"])
    aa = sug.get("albumartist")
    guesses = track_guesses(rel_dir, tracks, aa)
    out = {}
    for t in tracks:
        ch = {}
        if "albumartist" in fields and aa and t["albumartist"] != aa:
            ch["albumartist"] = aa
        if "album" in fields and sug.get("album") and t["album"] != sug["album"]:
            ch["album"] = sug["album"]
        if "year" in fields and sug.get("year") and t["year"] != sug["year"]:
            ch["year"] = sug["year"]
        if "compilation" in fields and aa:
            want = 1 if sug.get("compilation") else 0
            if (t["compilation"] or 0) != want:
                ch["compilation"] = "1" if want else None
        if "tracks" in fields:
            g = guesses.get(t["path"], {})
            if not t["title"] and g.get("title"):
                ch["title"] = g["title"]
            if not t["track"] and g.get("track"):
                ch["track"] = g["track"]
            if not t["disc"] and g.get("disc"):
                ch["disc"] = g["disc"]
            if not t["artist"]:
                artist = g.get("artist") or (aa if aa and not is_various(aa) else None)
                if artist:
                    ch["artist"] = artist
            for f in ("artist", "title"):
                if t[f] and fix_mojibake(t[f]) != t[f]:
                    ch[f] = fix_mojibake(t[f])
        if ch:
            out[t["path"]] = ch
    return out


def apply_suggestions(job, dirs, fields):
    job.total = len(dirs)
    batch = new_batch()
    total, touched = 0, set()
    for d in dirs:
        job.step(f"Correction : {d}")
        try:
            changes = suggestion_changes(d, fields)
            _, n, ds = write_many(changes, f"Suggestion appliquée ({len(dirs)} dossiers)", batch, job=job)
            total += n
            touched |= ds | {d}
        except Exception as e:
            job.error(d, e)
    job.message = "Mise à jour de l'analyse…"
    analysis.analyze_dirs(touched)
    job.message = f"{total} fichiers modifiés dans {len(dirs)} dossiers"
    return {"batch": batch, "files": total}


def save_album(rel_dir, album_fields, track_edits, label="Édition manuelle"):
    """album_fields apply to every file of the folder; track_edits = {path: {...}}."""
    with db.session() as c:
        tracks = tracks_of(c, rel_dir)
    changes = {}
    for t in tracks:
        ch = {k: v for k, v in album_fields.items() if k in ALBUM_FIELDS}
        if "compilation" in ch:
            ch["compilation"] = "1" if ch["compilation"] in (1, "1", True, "true") else None
        ch.update({k: v for k, v in track_edits.get(t["path"], {}).items() if k in tagger.EDITABLE})
        if ch:
            changes[t["path"]] = ch
    batch, n, dirs = write_many(changes, label)
    analysis.analyze_dirs(dirs | {rel_dir})
    return {"batch": batch, "files": n}


# -------------------------------------------------------------- trash / dup

def _ensure_trash():
    config.TRASH_DIR.mkdir(parents=True, exist_ok=True)
    marker = config.TRASH_DIR / ".ndignore"   # Navidrome skips folders containing this
    if not marker.exists():
        marker.write_text("")


def _unique(p):
    if not p.exists():
        return p
    i = 2
    while True:
        q = p.with_name(f"{p.name} ({i})")
        if not q.exists():
            return q
        i += 1


def trash_dir(c, rel_dir, batch, label):
    """Move a folder's audio (whole folder if it has no sub-folders) to the trash."""
    _ensure_trash()
    src = scanner.absolute(rel_dir)
    if src == config.MUSIC_ROOT:
        raise ValueError("impossible de supprimer la racine")
    dst = _unique(config.TRASH_DIR / rel_dir)
    has_subdirs = any(e.is_dir() for e in os.scandir(src))
    dst.parent.mkdir(parents=True, exist_ok=True)
    if has_subdirs:
        dst.mkdir(parents=True)
        moved = []
        for e in os.scandir(src):
            if e.is_file():
                shutil.move(e.path, dst / e.name)
                moved.append(e.name)
        after = {"dst": str(dst), "mode": "files", "files": moved}
    else:
        shutil.move(str(src), str(dst))
        after = {"dst": str(dst), "mode": "dir"}
    _log(c, batch, label, "trash", rel_dir, {"src": rel_dir}, after)
    c.execute("DELETE FROM tracks WHERE dir=?", (rel_dir,))
    c.execute("DELETE FROM albums WHERE dir=?", (rel_dir,))
    c.execute("DELETE FROM autotag WHERE dir=?", (rel_dir,))
    c.execute("DELETE FROM scan_errors WHERE dir=?", (rel_dir,))


def resolve_duplicates(job, groups):
    """groups: [{keep: dir, remove: [dirs]}]"""
    batch = new_batch()
    job.total = sum(len(g["remove"]) for g in groups)
    n = 0
    with db.session() as c:
        for g in groups:
            for d in g["remove"]:
                job.step(f"Corbeille : {d}")
                if d == g.get("keep"):
                    continue
                try:
                    trash_dir(c, d, batch, "Doublons supprimés")
                    n += 1
                except Exception as e:
                    job.error(d, e)
            c.commit()
    analysis.post_process()
    job.message = f"{n} dossiers déplacés dans la corbeille"
    return {"batch": batch, "moved": n}


def delete_albums(dirs):
    """Remove albums from the library: their files go to the trash (undoable
    from the history until the trash is emptied) and they leave the index."""
    batch = new_batch()
    label = f"Album supprimé : {dirs[0]}" if len(dirs) == 1 else f"Albums supprimés ({len(dirs)})"
    done, errors = [], []
    with db.session() as c:
        for d in dirs:
            try:
                trash_dir(c, d, batch, label)
                c.commit()
                done.append(d)
            except Exception as e:
                errors.append(f"{d}: {e}")
    if done:
        analysis.post_process()
    return {"batch": batch, "deleted": len(done), "errors": errors}


def undo(batch):
    with db.session() as c:
        rows = [dict(r) for r in c.execute(
            "SELECT * FROM history WHERE batch=? AND undone=0 ORDER BY id DESC", (batch,))]
    dirs, errors = set(), []
    for r in rows:
        try:
            if r["action"] == "tags":
                tagger.write(str(scanner.absolute(r["path"])), json.loads(r["before"]))
                scanner.rescan_paths([r["path"]])
                dirs.add(os.path.dirname(r["path"]))
            elif r["action"] == "trash":
                after = json.loads(r["after"])
                src, dst = scanner.absolute(r["path"]), after["dst"]
                if after["mode"] == "dir" and not src.exists():
                    src.parent.mkdir(parents=True, exist_ok=True)
                    shutil.move(dst, str(src))
                else:
                    src.mkdir(parents=True, exist_ok=True)
                    for name in os.listdir(dst):
                        shutil.move(os.path.join(dst, name), str(_unique(src / name)))
                    os.rmdir(dst)
                scanner.rescan_dir(r["path"])
                dirs.add(r["path"])
            with db.session() as c:
                c.execute("UPDATE history SET undone=1 WHERE id=?", (r["id"],))
        except Exception as e:
            errors.append(f"{r['path']}: {e}")
    if dirs:
        analysis.analyze_dirs(dirs)
    return {"undone": len(rows) - len(errors), "errors": errors}


def trash_info():
    if not config.TRASH_DIR.exists():
        return {"path": str(config.TRASH_DIR), "size": 0, "files": 0}
    size = files = 0
    for dp, _, fn in os.walk(config.TRASH_DIR):
        for f in fn:
            try:
                size += os.path.getsize(os.path.join(dp, f))
                files += 1
            except OSError:
                pass
    return {"path": str(config.TRASH_DIR), "size": size, "files": files}


def empty_trash():
    if config.TRASH_DIR.exists():
        for e in os.scandir(config.TRASH_DIR):
            if e.name == ".ndignore":
                continue
            if e.is_dir(follow_symlinks=False):
                shutil.rmtree(e.path)
            else:
                os.remove(e.path)
    with db.session() as c:
        c.execute("UPDATE history SET undone=2 WHERE action='trash' AND undone=0")  # 2 = purged


# ------------------------------------------------------------------ history

def history_stats():
    with db.session() as c:
        r = c.execute("""SELECT COUNT(DISTINCT batch) batches, COUNT(*) entries,
                                COALESCE(SUM(LENGTH(before) + LENGTH(after) + LENGTH(path) + LENGTH(label) + 40), 0) bytes,
                                MIN(ts) oldest,
                                COUNT(DISTINCT CASE WHEN action='trash' AND undone=0 THEN batch END) restorable
                         FROM history""").fetchone()
    return {**dict(r), "db_size": db.size_on_disk()}


def delete_history(batches=None):
    """Forget some batches (None = all of them). Files are not touched, but
    those changes can no longer be undone."""
    with db.session() as c:
        if batches is None:
            n = c.execute("DELETE FROM history").rowcount
        else:
            n = 0
            for b in batches:
                n += c.execute("DELETE FROM history WHERE batch=?", (b,)).rowcount
    db.vacuum()
    return {"deleted": n, **history_stats()}
