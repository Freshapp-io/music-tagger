"""Several artists in one tag: 'Nas & DJ Premier', 'Mobb Deep, Nas',
'Mad Professor meets Lee Perry'. Navidrome only files an album under each
artist when they are separated with '; ', so these values are listed with a
proposed split, checked against the artists already in the library."""
import re
from collections import defaultdict

from . import analysis, db, fixes
from .analysis import _album_folder
from .parsing import FEAT, fold, is_various

KEEP = "__keep__"        # leave the value alone (a duo, a band name)
JOIN = "; "

# Words and signs used between two artist names. '&' glued to letters
# ('R&B', 'M&M') and ',' inside numbers ('10,000 Maniacs') are not separators;
# 'x' only in lower case ('Malcolm X').
SEP = re.compile(r"(\s+&\s*|\s*&\s+|\s*,\s+|\s+\+\s+|\s+/\s+|"
                 r"\s+(?:et|and|meets?|vs\.?|versus|with|avec)\s+|\s+(?-i:x)\s+)", re.I)

# sure: every part is an artist of the library; partial: some are;
# unknown: none is (often a duo: 'Eric B. & Rakim'); single: an artist folder
# carries the whole name, so it is most likely one artist.
STATUSES = ("sure", "partial", "unknown", "single")


def _key(s):
    k = fold(s)
    return k[4:] if k.startswith("the ") else k


def _names(value):
    """'Nas; DJ Premier feat. X' -> ['Nas', 'DJ Premier']: the names already split with ';'."""
    m = FEAT.search(value)
    main = value[:m.start()] if m else value
    return [p.strip() for p in main.split(";") if p.strip()]


def known_artists(tracks, dirs):
    """Keys of names used alone in the tags, plus artist folder names (which
    may hold a separator: a folder 'Eric B. & Rakim' makes it one artist)."""
    known = set()
    for t in tracks:
        for field in ("artist", "albumartist"):
            for name in _names(t[field] or ""):
                if not SEP.search(name) and not is_various(name):
                    known.add(_key(name))
    for d in dirs:
        for name in _album_folder(d)[:-1]:
            known.add(_key(name))
    known.discard("")
    return known


def _split_segment(seg, known):
    """One ';'-free segment -> (groups [(name, known)], status)."""
    tokens = SEP.split(seg)
    parts, seps = [p.strip() for p in tokens[::2]], tokens[1::2]
    if len(parts) < 2 or not all(parts):
        return [(seg.strip(), True)], None
    join = lambda i, j: "".join(parts[k] + (seps[k] if k < j else "") for k in range(i, j + 1)).strip()
    if _key(seg) in known:
        return [(seg.strip(), True)], "single"
    # Longest known run first, so 'Earth, Wind & Fire & Nas' keeps the band whole.
    groups, i = [], 0
    while i < len(parts):
        j = next((j for j in range(len(parts) - 1, i - 1, -1) if _key(join(i, j)) in known), None)
        if j is None:
            groups.append([i, i, False])
        else:
            groups.append([i, j, True])
            i = j
        i += 1
    # Unknown neighbours stay together: 'Nas & Eric B. & Rakim' -> 'Nas; Eric B. & Rakim'.
    merged = []
    for g in groups:
        if merged and not g[2] and not merged[-1][2]:
            merged[-1][1] = g[1]
        else:
            merged.append(g)
    n_known = sum(g[2] for g in merged)
    if not n_known:
        return [(p, False) for p in parts], "unknown"
    return [(join(i, j), k) for i, j, k in merged], "sure" if n_known == len(merged) else "partial"


def propose(raw, known):
    """(proposal, groups, status) for a tag value, None when there is nothing to split."""
    if not raw or is_various(raw):
        return None
    m = FEAT.search(raw)
    main, tail = (raw[:m.start()], raw[m.start():]) if m else (raw, "")
    groups, statuses = [], []
    for seg in [s for s in main.split(";") if s.strip()]:
        g, st = _split_segment(seg, known)
        groups += g
        if st:
            statuses.append(st)
    if not statuses:
        return None
    status = next(s for s in ("unknown", "partial", "single", "sure") if s in statuses) \
        if len(set(statuses)) > 1 else statuses[0]
    if status == "single" and len(groups) == 1:
        return raw, [{"name": groups[0][0], "known": True}], status
    proposal = JOIN.join(name for name, _ in groups) + tail
    return proposal, [{"name": n, "known": k} for n, k in groups], status


def overrides():
    with db.session() as c:
        return db.get_meta(c, "artist_split", {})


def _load():
    with db.session() as c:
        tracks = [dict(r) for r in c.execute("SELECT path, dir, artist, albumartist FROM tracks")]
        dirs = [r[0] for r in c.execute("SELECT dir FROM albums")]
    return tracks, dirs


def summary():
    """One row per artist / album artist value holding several artists."""
    tracks, dirs = _load()
    known = known_artists(tracks, dirs)
    over = overrides()
    rows = defaultdict(lambda: {"artist": 0, "albumartist": 0, "dirs": set()})
    for t in tracks:
        for field in ("artist", "albumartist"):
            if t[field]:
                r = rows[t[field]]
                r[field] += 1
                r["dirs"].add(t["dir"])
    out = []
    for raw, r in rows.items():
        p = propose(raw, known)
        if not p:
            continue
        proposal, groups, status = p
        chosen = over.get(raw)
        out.append({
            "raw": raw, "artist": r["artist"], "albumartist": r["albumartist"], "dirs": len(r["dirs"]),
            "examples": sorted(r["dirs"], key=str.lower)[:3],
            "groups": groups, "status": status,
            "proposal": chosen or proposal, "suggested": proposal, "user_choice": chosen is not None,
        })
    out.sort(key=lambda x: -(x["artist"] + x["albumartist"]))
    return {"items": out}


def detail(raw):
    """Folders using a value, with the number of tracks per field."""
    tracks, _ = _load()
    dirs = defaultdict(lambda: {"artist": 0, "albumartist": 0})
    for t in tracks:
        for field in ("artist", "albumartist"):
            if t[field] == raw:
                dirs[t["dir"]][field] += 1
    return sorted(({"dir": d, **v} for d, v in dirs.items()), key=lambda x: x["dir"].lower())


def remember(raw, target):
    with db.session() as c:
        m = db.get_meta(c, "artist_split", {})
        m[raw] = target
        db.set_meta(c, "artist_split", m)


def apply(job, items):
    """items: [{raw, target}]: every artist / album artist tag equal to raw becomes target."""
    wanted = {}
    for i in items:
        if i["target"] == KEEP:
            remember(i["raw"], KEEP)
            continue
        target = re.sub(r"\s*;\s*", JOIN, (i["target"] or "").strip()).strip("; ")
        if not target:
            raise ValueError(f"valeur vide pour « {i['raw']} »")
        remember(i["raw"], target)
        if target != i["raw"]:
            wanted[i["raw"]] = target
    tracks, _ = _load()
    todo = {}
    for t in tracks:
        ch = {f: wanted[t[f]] for f in ("artist", "albumartist") if t[f] in wanted}
        if ch:
            todo[t["path"]] = ch
    job.total = len(todo)
    batch = fixes.new_batch()
    paths, changed, dirs = list(todo), 0, set()
    for i in range(0, len(paths), 100):
        chunk = {p: todo[p] for p in paths[i:i + 100]}
        _, n, d = fixes.write_many(chunk, f"Artistes séparés ({len(wanted)} valeur(s))", batch, job=job)
        changed += n
        dirs |= d
        job.step(f"Artistes : {changed} fichiers modifiés", n=len(chunk))
    if dirs:
        analysis.analyze_dirs(dirs)
    job.message = f"{changed} fichiers modifiés"
    return {"batch": batch, "files": changed}
