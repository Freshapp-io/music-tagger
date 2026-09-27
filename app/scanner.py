"""Walk MUSIC_ROOT, read tags of new/changed mp3 files into SQLite."""
import os
import time
from concurrent.futures import ThreadPoolExecutor

from . import analysis, config, db, tagger

COLUMNS = [
    "path", "dir", "filename", "size", "mtime", "tagged", "artist", "albumartist", "album",
    "title", "track", "disc", "year", "genre", "compilation", "mb_albumid", "bitrate",
    "bitrate_mode", "duration", "sample_rate", "has_cover", "id3_version", "error",
]
UPSERT = "INSERT OR REPLACE INTO tracks ({}) VALUES ({})".format(
    ", ".join(COLUMNS), ", ".join("?" * len(COLUMNS))
)


def rel(abs_path):
    r = os.path.relpath(abs_path, config.MUSIC_ROOT).replace(os.sep, "/")
    return "" if r == "." else r


def absolute(rel_path):
    # Lexical check only: resolve() costs a syscall per file and is unreliable
    # on network shares (it may return a different spelling of the root).
    parts = rel_path.replace("\\", "/").split("/")
    if ".." in parts or os.path.isabs(rel_path):
        raise ValueError("chemin hors de la bibliothèque")
    return config.MUSIC_ROOT.joinpath(*[p for p in parts if p not in ("", ".")])


def _skip_dir(path, name):
    if name.startswith((".", "@")) or name in ("#recycle", "__MACOSX", "$RECYCLE.BIN", "System Volume Information"):
        return True
    if os.path.abspath(path) == str(config.TRASH_DIR):
        return True
    return os.path.exists(os.path.join(path, ".ndignore"))


class RootUnavailable(Exception):
    pass


def _scandir_retry(d, attempts=3):
    for i in range(attempts):
        try:
            return list(os.scandir(d))
        except OSError:
            if i == attempts - 1:
                raise
            time.sleep(2 * (i + 1))


def walk(job=None, failed=None):
    """Yield (rel_path, size, mtime) for every audio file. Folders that could
    not be listed are added to `failed` so their files are not seen as deleted."""
    stack = [str(config.MUSIC_ROOT)]
    while stack:
        d = stack.pop()
        try:
            entries = _scandir_retry(d)
        except OSError as e:
            if d == str(config.MUSIC_ROOT):
                raise RootUnavailable(f"Bibliothèque inaccessible ({e}) : scan annulé") from e
            if job:
                job.error(rel(d), e)
            if failed is not None:
                failed.add(rel(d))
            continue
        for e in entries:
            try:
                if e.is_dir(follow_symlinks=False):
                    if not _skip_dir(e.path, e.name):
                        stack.append(e.path)
                elif e.name.lower().endswith(config.AUDIO_EXT) and not e.name.startswith("._"):
                    st = e.stat()
                    yield rel(e.path), st.st_size, st.st_mtime
            except OSError as err:
                if job:
                    job.error(rel(e.path), err)
                if failed is not None:
                    failed.add(rel(e.path))
        if job:
            job.message = f"Parcours : {rel(d) or '/'}"


def _read_retry(abs_path, attempts=4):
    """Network shares drop connections under load: retry transient I/O errors."""
    for i in range(attempts):
        try:
            return tagger.read(abs_path)
        except OSError:
            if i == attempts - 1:
                raise
            time.sleep(1.5 * (i + 1))


def _row(rel_path):
    data = _read_retry(str(absolute(rel_path)))
    data["path"] = rel_path
    d = os.path.dirname(rel_path)
    data["dir"] = d
    data["filename"] = os.path.basename(rel_path)
    return tuple(data.get(c) for c in COLUMNS)


def scan(job, full=False):
    with db.session() as c:
        known = {r["path"]: (r["size"], r["mtime"]) for r in c.execute("SELECT path, size, mtime FROM tracks")}

    if not any(os.scandir(config.MUSIC_ROOT)) and known:
        raise RootUnavailable(f"{config.MUSIC_ROOT} est vide : volume non monté ? Scan annulé")
    seen, todo, failed = set(), [], set()
    for path, size, mtime in walk(job, failed):
        seen.add(path)
        k = known.get(path)
        if full or k is None or k[0] != size or abs((k[1] or 0) - mtime) > 0.01:
            todo.append(path)
        job.total = len(seen)
    job.message = f"{len(seen)} fichiers trouvés, {len(todo)} à lire"
    job.done = len(seen) - len(todo)

    def under_failed(p):
        return any(p == f or p.startswith(f + "/") for f in failed)

    removed = {p for p in set(known) - seen if not under_failed(p)}
    with db.session() as c:
        c.executemany("DELETE FROM tracks WHERE path=?", [(p,) for p in removed])

    batch = []

    def flush():
        with db.session() as c:
            c.executemany(UPSERT, batch)
        batch.clear()

    def safe_row(p):
        try:
            return _row(p)
        except Exception as e:
            job.error(p, e)
            return None

    with ThreadPoolExecutor(max_workers=config.SCAN_THREADS) as pool:
        for path, row in zip(todo, pool.map(safe_row, todo)):
            job.step(f"Lecture : {path}")
            if row:
                batch.append(row)
            if len(batch) >= 500:
                flush()
    if batch:
        flush()

    job.message = "Analyse de la bibliothèque…"
    analysis.analyze_all()
    with db.session() as c:
        from datetime import datetime
        db.set_meta(c, "last_scan", datetime.now().isoformat(timespec="seconds"))
    job.message = f"Terminé : {len(seen)} fichiers, {len(todo)} lus, {len(removed)} disparus"
    return {"files": len(seen), "read": len(todo), "removed": len(removed)}


def rescan_paths(paths):
    """Re-read specific files (after a write) and drop the missing ones."""
    rows, gone = [], []
    for p in paths:
        if absolute(p).exists():
            rows.append(_row(p))
        else:
            gone.append((p,))
    with db.session() as c:
        c.executemany(UPSERT, rows)
        c.executemany("DELETE FROM tracks WHERE path=?", gone)


def rescan_dir(rel_dir):
    """Re-read every audio file directly inside rel_dir."""
    base = absolute(rel_dir)
    paths = []
    if base.is_dir():
        paths = [rel(os.path.join(base, n)) for n in os.listdir(base)
                 if n.lower().endswith(config.AUDIO_EXT) and os.path.isfile(os.path.join(base, n))]
    with db.session() as c:
        known = [r[0] for r in c.execute("SELECT path FROM tracks WHERE dir=?", (rel_dir,))]
    rescan_paths(sorted(set(paths) | set(known)))
