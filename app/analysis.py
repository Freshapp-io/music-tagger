"""Turn the `tracks` table into one row per folder ('album') with detected
issues, a suggested fix, a quality score and duplicate groups."""
import difflib
import json
import re
from collections import Counter, defaultdict

from . import db
from .parsing import (
    DISC_DIR, GENERIC_TITLE, disc_of, fix_mojibake, fold, is_various, norm_album, norm_title, parse_dir,
    parse_filename, primary_artist,
)

VARIOUS = "Various Artists"

ALBUM_COLUMNS = [
    "dir", "n_tracks", "n_untagged", "n_incomplete", "artists", "albumartists", "albums", "years",
    "main_artist", "main_album", "main_year", "avg_bitrate", "min_bitrate", "vbr", "total_size",
    "duration", "has_cover", "has_mbid", "issues", "suggestion", "quality", "dup_group",
    "norm_artist", "norm_album", "titles", "tag_artist", "misplaced",
]


def majority(values):
    """Most common non-empty value (case/space-insensitive), its share over len(values)."""
    vals = [v for v in values if v]
    if not vals or not values:
        return None, 0.0
    groups = defaultdict(list)
    for v in vals:
        groups[fold(v)].append(v)
    key, members = max(groups.items(), key=lambda kv: len(kv[1]))
    best = Counter(members).most_common(1)[0][0]
    return best, len(members) / len(values)


def distinct(values, keep_none=False):
    seen = {}
    for v in values:
        if v is None and not keep_none:
            continue
        seen.setdefault(fold(v) if v else "", v)
    return list(seen.values())


def suggest(rel_dir, tracks):
    """Best guess for the album-level tags of a folder."""
    n = len(tracks)
    folder = parse_dir(rel_dir)
    tagged = [t for t in tracks if t["tagged"]]
    reasons = []

    aa_values = [t["albumartist"] for t in tracks if t["albumartist"] and not is_various(t["albumartist"])]
    prim_values = [primary_artist(t["artist"]) for t in tracks if t["artist"] and not is_various(t["artist"])]
    existing_aa, aa_share = majority(aa_values + [None] * (n - len(aa_values)))
    prim, prim_share = majority(prim_values + [None] * (n - len(prim_values)))
    n_prim = len(distinct(prim_values))
    folder_artist = folder.get("artist")

    albumartist, compilation, confidence = None, 0, "low"
    if existing_aa and aa_share >= 0.5:
        albumartist = existing_aa
        reasons.append(f"album artist présent sur {round(aa_share * n)}/{n} titres")
        confidence = "high" if aa_share >= 0.8 else "medium"
    elif prim and prim_share >= 0.5:
        albumartist = prim
        reasons.append(f"artiste principal sur {round(prim_share * n)}/{n} titres")
        confidence = "high" if prim_share >= 0.8 else "medium"
    elif folder_artist and (not prim_values or any(fold(folder_artist) in fold(p) for p in prim_values)):
        albumartist = folder_artist
        reasons.append("déduit du nom du dossier")
        confidence = "medium" if prim_values else "low"
    elif n_prim >= 3:
        albumartist, compilation = VARIOUS, 1
        reasons.append(f"compilation ({n_prim} artistes différents)")
        confidence = "medium"
    if albumartist and folder_artist and fold(albumartist) == fold(folder_artist) and confidence == "medium":
        confidence = "high"

    album, album_share = majority([t["album"] for t in tagged] + [None] * (n - len(tagged)))
    if not album or album_share < 0.5:
        album = folder.get("album") or album
        if album:
            reasons.append("album déduit du dossier")
            if confidence == "high":
                confidence = "medium"
    year, _ = majority([t["year"] for t in tracks])
    year = year or folder.get("year")
    albumartist, album = fix_mojibake(albumartist), fix_mojibake(album)
    return {
        "albumartist": albumartist, "album": album, "year": year,
        "compilation": compilation, "confidence": confidence, "reason": ", ".join(reasons),
        "folder": folder,
    }


# Default values written by rippers and tag editors: no artist at all.
PLACEHOLDER_ARTISTS = {"new artist", "unknown artist", "unknown", "artist", "artiste", "artiste inconnu",
                       "inconnu", "no artist", "untitled artist", "track"}


def tag_artist(tracks):
    """Album artist the tags themselves agree on (80 % of the tracks), from the
    album artist or else the main artist. None for compilations or when unsure:
    the folder name is never used here."""
    if any(t["compilation"] for t in tracks):
        return None
    for field in ("albumartist", "artist"):
        values = [primary_artist(t[field]) for t in tracks]
        if any(is_various(v) for v in values):
            return None
        best, share = majority(values)
        if best and share >= 0.8:
            return None if fold(best) in PLACEHOLDER_ARTISTS else best
    return None


def album_row(rel_dir, tracks):
    n = len(tracks)
    tagged = [t for t in tracks if t["tagged"]]
    n_untagged = n - len(tagged)
    n_incomplete = sum(1 for t in tagged if not (t["artist"] and t["title"] and t["album"]))
    issues = []

    if n_untagged or n_incomplete:
        issues.append("untagged")

    sug = suggest(rel_dir, tracks)
    prim_values = [primary_artist(t["artist"]) for t in tracks if t["artist"] and not is_various(t["artist"])]
    _, prim_share = majority(prim_values + [None] * (n - len(prim_values)))
    has_va = any(is_various(t["albumartist"]) or is_various(t["artist"]) for t in tracks)
    clean_compilation = (
        all(t["compilation"] for t in tracks)
        and all(is_various(t["albumartist"]) for t in tracks)
        and prim_share < 0.6
    )
    if has_va and not clean_compilation:
        issues.append("various")

    if len(tagged) > 1:
        sub = []
        if len(distinct([t["albumartist"] for t in tagged], keep_none=True)) > 1:
            sub.append("albumartist_mixed")
        elif not any(t["albumartist"] for t in tagged) and len(distinct([t["artist"] for t in tagged])) > 1:
            sub.append("albumartist_missing")
        if len(distinct([t["album"] for t in tagged], keep_none=True)) > 1:
            sub.append("album_mixed")
        if len(distinct([t["year"] for t in tagged], keep_none=True)) > 1:
            sub.append("year_mixed")
        if len(distinct([t["mb_albumid"] for t in tagged], keep_none=True)) > 1:
            sub.append("mbid_mixed")
        if sub:
            issues.append("inconsistent")
            issues.extend(sub)

    total_dur = sum(t["duration"] or 0 for t in tracks) or 1
    brs = [t["bitrate"] for t in tracks if t["bitrate"]]
    avg_br = round(sum((t["bitrate"] or 0) * (t["duration"] or 0) for t in tracks) / total_dur) if brs else 0
    vbr = int(any(t["bitrate_mode"] == "VBR" for t in tracks))
    has_cover = int(bool(tracks) and all(t["has_cover"] for t in tracks))
    has_mbid = int(bool(tracks) and all(t["mb_albumid"] for t in tracks))

    main_artist = sug["albumartist"] or majority([t["artist"] for t in tracks])[0]
    main_album = sug["album"]
    titles = sorted(
        x for x in {norm_title(t["title"] or parse_filename(t["filename"]).get("title")) for t in tracks}
        if x and not GENERIC_TITLE.match(x)
    )
    row = {
        "dir": rel_dir, "n_tracks": n, "n_untagged": n_untagged, "n_incomplete": n_incomplete,
        "artists": json.dumps(distinct([t["artist"] for t in tracks])[:30], ensure_ascii=False),
        "albumartists": json.dumps(distinct([t["albumartist"] for t in tracks], keep_none=True)[:30], ensure_ascii=False),
        "albums": json.dumps(distinct([t["album"] for t in tracks], keep_none=True)[:30], ensure_ascii=False),
        "years": json.dumps(distinct([t["year"] for t in tracks], keep_none=True)[:30], ensure_ascii=False),
        "main_artist": main_artist, "main_album": main_album, "main_year": sug["year"],
        "avg_bitrate": avg_br, "min_bitrate": min(brs) if brs else 0, "vbr": vbr,
        "total_size": sum(t["size"] or 0 for t in tracks),
        "duration": round(sum(t["duration"] or 0 for t in tracks)),
        "has_cover": has_cover, "has_mbid": has_mbid,
        "issues": json.dumps(issues), "suggestion": json.dumps(sug, ensure_ascii=False),
        "quality": None, "dup_group": None,
        "norm_artist": "various" if main_artist and is_various(main_artist) else fold(primary_artist(main_artist)),
        "norm_album": norm_album(main_album),
        "titles": json.dumps(titles, ensure_ascii=False),
        "tag_artist": tag_artist(tracks), "misplaced": None,
    }
    row["quality"] = quality(row, 1.0)
    return row


def quality(a, completeness):
    """Higher is better. Bitrate dominates, then completeness, then tags/cover."""
    br = min(a["avg_bitrate"] or 0, 320)
    if a["vbr"] and br >= 180:
        br = min(br + 40, 320)          # V0/V2 sound as good as higher CBR
    score = br * completeness
    score += 10 * (a["has_cover"] or 0)
    score += 10 * (1 if not a["n_untagged"] and not a["n_incomplete"] else 0)
    score += 5 * (a["has_mbid"] or 0)
    return round(score, 1)


def _load_tracks(c, dirs=None):
    by_dir = defaultdict(list)
    if dirs is None:
        cur = c.execute("SELECT * FROM tracks ORDER BY dir, disc, filename")
    else:
        q = "SELECT * FROM tracks WHERE dir IN ({}) ORDER BY dir, disc, filename"
        cur = []
        dirs = list(dirs)
        for i in range(0, len(dirs), 500):
            chunk = dirs[i:i + 500]
            cur.extend(c.execute(q.format(",".join("?" * len(chunk))), chunk).fetchall())
    for r in cur:
        by_dir[r["dir"]].append(dict(r))
    return by_dir


def _insert(c, rows):
    c.executemany(
        "INSERT OR REPLACE INTO albums ({}) VALUES ({})".format(
            ", ".join(ALBUM_COLUMNS), ", ".join("?" * len(ALBUM_COLUMNS))),
        [tuple(r[k] for k in ALBUM_COLUMNS) for r in rows],
    )


def analyze_all():
    with db.session() as c:
        by_dir = _load_tracks(c)
        rows = [album_row(d, ts) for d, ts in by_dir.items()]
        c.execute("DELETE FROM albums")
        _insert(c, rows)
    post_process()


def analyze_dirs(dirs):
    dirs = set(dirs)
    with db.session() as c:
        by_dir = _load_tracks(c, dirs)
        for d in dirs:
            c.execute("DELETE FROM albums WHERE dir=?", (d,))
        _insert(c, [album_row(d, ts) for d, ts in by_dir.items()])
    post_process()


def post_process():
    """Checks that compare folders with each other (run after any change)."""
    compute_duplicates()
    compute_misplaced()


# ---------------------------------------------------------------- duplicates

def _overlap(a, b):
    if not a or not b:
        return 0.0
    return len(a & b) / min(len(a), len(b))


def _is_dup(x, y):
    if x["dir"] == y["dir"]:
        return False
    # CD1 / CD2 of the same album are not duplicates
    dx, dy = disc_of(x["dir"]), disc_of(y["dir"])
    if dx and dy and dx != dy:
        return False
    ta, tb = x["_titles"], y["_titles"]
    common = len(ta & tb)
    if common < 3 and common < min(len(ta), len(tb)):
        return False
    ov = _overlap(ta, tb)
    same_album = x["norm_album"] and x["norm_album"] == y["norm_album"]
    same_artist = x["norm_artist"] == y["norm_artist"] or not x["norm_artist"] or not y["norm_artist"]
    if same_album and same_artist and ov >= 0.5:
        return True
    # Same songs under another album name: only when the names still look alike
    # (otherwise it is a best-of / live album sharing songs with the original).
    small, big = sorted((len(ta), len(tb)))
    if small < 4 or ov < 0.8 or small / big < 0.6:
        return False
    if not x["norm_album"] or not y["norm_album"]:
        return ov >= 0.9 and small == big
    return difflib.SequenceMatcher(None, x["norm_album"], y["norm_album"]).ratio() >= 0.6


def find_duplicate_groups(albums):
    """albums: list of dicts with dir, norm_artist, norm_album, titles(json)."""
    for a in albums:
        a["_titles"] = set(json.loads(a["titles"] or "[]"))
    cand = [a for a in albums if len(a["_titles"]) >= 2]
    blocks = defaultdict(list)
    for a in cand:
        if a["norm_artist"] and a["norm_artist"] != "various":
            blocks["ar:" + a["norm_artist"]].append(a)
        if len(a["norm_album"] or "") >= 3:
            blocks["al:" + a["norm_album"]].append(a)

    parent = {a["dir"]: a["dir"] for a in cand}

    def find(x):
        while parent[x] != x:
            parent[x] = parent[parent[x]]
            x = parent[x]
        return x

    for members in blocks.values():
        if len(members) < 2 or len(members) > 400:
            continue
        for i, x in enumerate(members):
            for y in members[i + 1:]:
                if find(x["dir"]) != find(y["dir"]) and _is_dup(x, y):
                    parent[find(x["dir"])] = find(y["dir"])

    groups = defaultdict(list)
    for a in cand:
        groups[find(a["dir"])].append(a["dir"])
    return [sorted(g) for g in groups.values() if len(g) > 1]


def compute_duplicates():
    with db.session() as c:
        albums = [dict(r) for r in c.execute(
            "SELECT dir, norm_artist, norm_album, titles, n_tracks, n_untagged, n_incomplete, "
            "avg_bitrate, vbr, has_cover, has_mbid, issues FROM albums")]
        by_dir = {a["dir"]: a for a in albums}
        groups = find_duplicate_groups(albums)
        c.execute("UPDATE albums SET dup_group=NULL")
        for gid, g in enumerate(groups, 1):
            max_n = max(by_dir[d]["n_tracks"] for d in g)
            for d in g:
                a = by_dir[d]
                issues = [i for i in json.loads(a["issues"]) if i != "duplicate"] + ["duplicate"]
                c.execute("UPDATE albums SET dup_group=?, quality=?, issues=? WHERE dir=?",
                          (gid, quality(a, a["n_tracks"] / max_n), json.dumps(issues), d))
        # Clear stale 'duplicate' flags
        in_groups = {d for g in groups for d in g}
        for a in albums:
            if a["dir"] not in in_groups and "duplicate" in a["issues"]:
                issues = [i for i in json.loads(a["issues"]) if i != "duplicate"]
                c.execute("UPDATE albums SET issues=?, quality=? WHERE dir=?",
                          (json.dumps(issues), quality(a, 1.0), a["dir"]))
    return groups


# ----------------------------------------------------------------- misplaced

def _artist_key(s):
    k = fold(primary_artist(s))
    return k[4:] if k.startswith("the ") else k


def _same_artist(a, b):
    ka, kb = _artist_key(a), _artist_key(b)
    if not ka or not kb or ka == kb:
        return True
    short, long_ = sorted((ka, kb), key=len)
    # 'Jay-Z' / 'Jay-Z & Kanye West', or a small spelling difference
    if re.search(r"\b" + re.escape(short) + r"\b", long_) or difflib.SequenceMatcher(None, ka, kb).ratio() >= 0.85:
        return True
    # 'Alton Ellis' / 'Alton and Hortense Ellis': every word of one is in the other
    ws, wl = set(short.split()) - {"and", "et"}, set(long_.split())
    if ws and ws <= wl:
        return True
    # 'Abcdr du Son' / 'Abcdrduson.com': same letters once spaces are gone
    cs, cl = short.replace(" ", ""), long_.replace(" ", "")
    return len(cs) >= 5 and cs in cl


def _album_folder(rel_dir):
    """'Nas/Illmatic/CD1' -> ['Nas', 'Illmatic']: disc sub-folders belong to the album."""
    parts = rel_dir.split("/")
    if len(parts) > 1 and DISC_DIR.match(parts[-1]):
        parts = parts[:-1]
    return parts


# Left part of 'X - Album' folder names that is no artist: years, numbers, discs, editions.
NOT_ARTIST = re.compile(
    r"^(?:[\d ]+$|(?:cd|dis[ck]|disque|vol(?:ume)?|part|tome|chapitre) ?\d*\b|"
    r"(?:live|best of|the best of|greatest hits|deluxe|remaster(?:ed)?|bonus|ep|lp|singles?|mixtape|ost|soundtrack)\b)")


def _name_split(name):
    """'Jay-Z - Reasonable Doubt' -> ('Jay-Z', 'Reasonable Doubt'), None when the
    folder name has no 'Artist - Album' form."""
    if " " not in name:
        name = name.replace("_", " ")          # scene names: 'Jay-Z_-_Reasonable_Doubt'
    if " - " not in name:
        return None
    left, right = (x.strip() for x in name.split(" - ", 1))
    if not left or not right or NOT_ARTIST.match(fold(left)):
        return None
    return left, right


def _same_album(a, b):
    na, nb = norm_album(a), norm_album(b)
    if not na or not nb:
        return False
    return na == nb or (min(len(na), len(nb)) >= 4 and (na in nb or nb in na))


def find_misplaced(albums):
    """albums: dicts with dir, tag_artist, artists and albums (json).
    Returns {dir: info} for the albums whose location disagrees:
    - folder vs folder: 'Nas/Jay-Z - Reasonable Doubt' (artist folder vs the
      artist in the album folder name), tags or not;
    - folder vs tags: the folder names an artist of the library (parent folder
      or 'Artist - Album' name) while the tags agree on another artist.
    info = {folder_artist, folder, name_artist, tag_artist, target, kinds}
    with kinds among 'folders' and 'tags'."""
    # Artists of the library: album artists, and track artists (a folder named
    # after an artist who only appears as a track artist is still theirs).
    known = {_artist_key(a["tag_artist"]) for a in albums if a["tag_artist"]}
    for a in albums:
        known.update(_artist_key(x) for x in json.loads(a.get("artists") or "[]") if not is_various(x))
    known.discard("")
    # Existing folders named after an artist: where a misplaced album should go.
    homes = defaultdict(Counter)
    # Artist folders whose albums are named 'Artist - Album': there the left part is an artist.
    convention = Counter()
    for a in albums:
        parts = _album_folder(a["dir"])
        for i in range(len(parts) - 1):
            k = _artist_key(parts[i])
            if k in known:
                homes[k]["/".join(parts[:i + 1])] += 1
        split = _name_split(parts[-1])
        if len(parts) >= 2 and split and _artist_key(split[0]) and _same_artist(split[0], parts[-2]):
            convention["/".join(parts[:-1])] += 1
            homes[_artist_key(parts[-2])]["/".join(parts[:-1])] += 1

    out = {}
    for a in albums:
        artist = a["tag_artist"]
        parts = _album_folder(a["dir"])
        split = _name_split(parts[-1])
        tag_albums = [x for x in json.loads(a.get("albums") or "[]") if x]
        info = {"folder_artist": None, "folder": None, "name_artist": None, "tag_artist": artist, "target": None}
        kinds = []

        # 1. folder vs folder
        if len(parts) >= 2 and split:
            parent, parent_path = parts[-2], "/".join(parts[:-1])
            left, right = split
            if not any(_same_album(left, x) for x in tag_albums):      # 'Illmatic - Deluxe Edition'
                left_is_artist = (_artist_key(left) in known or convention[parent_path] >= 2
                                  or any(_same_album(right, x) for x in tag_albums))
                parent_is_artist = _artist_key(parent) in known or convention[parent_path] >= 2
                if left_is_artist and parent_is_artist and not _same_artist(left, parent):
                    info.update(folder_artist=parent, folder=parent_path, name_artist=left)
                    kinds.append("folders")

        # 2. folder vs tags
        if artist:
            candidates = []
            if split:
                candidates.append((split[0], None))
            candidates += [(parts[i], "/".join(parts[:i + 1])) for i in range(len(parts) - 2, -1, -1)]
            found = next(((name, folder) for name, folder in candidates if _artist_key(name) in known), None)
            # The right artist appearing somewhere in the path is enough ('Jay-Z/Jay-Z & Nas - …').
            if found and not _same_artist(found[0], artist) and \
                    not re.search(r"\b" + re.escape(_artist_key(artist)) + r"\b", fold(a["dir"])):
                if found[1] is None:
                    info["name_artist"] = found[0]
                elif not info["folder_artist"]:
                    info.update(folder_artist=found[0], folder=found[1])
                kinds.append("tags")
        if not kinds:
            continue
        info["kinds"] = kinds
        # Show the other folder name too when it names the artist ('Adrian Younge/Adrian Younge - …').
        if len(parts) >= 2 and not info["folder_artist"]:
            parent = parts[-2]
            if _artist_key(parent) in known or convention["/".join(parts[:-1])] >= 2 or \
                    (info["name_artist"] and _same_artist(parent, info["name_artist"])):
                info.update(folder_artist=parent, folder="/".join(parts[:-1]))
        if split and not info["name_artist"] and (
                _artist_key(split[0]) in known or (info["folder_artist"] and _same_artist(split[0], info["folder_artist"]))):
            info["name_artist"] = split[0]

        # Where it should go: the tags' artist, else (no tags) the one in the folder name.
        right_artist = artist or info["name_artist"]
        if right_artist and not (info["folder_artist"] and _same_artist(right_artist, info["folder_artist"])):
            home = homes.get(_artist_key(right_artist))
            if home:
                rest = a["dir"].split("/")[len(parts) - 1:]
                info["target"] = home.most_common(1)[0][0] + "/" + "/".join(rest)
        out[a["dir"]] = info
    return out


def compute_misplaced():
    with db.session() as c:
        albums = [dict(r) for r in c.execute("SELECT dir, tag_artist, artists, albums, issues, misplaced FROM albums")]
        found = find_misplaced(albums)
        for a in albums:
            info = found.get(a["dir"])
            issues = [i for i in json.loads(a["issues"]) if not i.startswith("misplaced")]
            if info:
                issues += ["misplaced"] + [f"misplaced_{k}" for k in info["kinds"]]
            value = json.dumps(info, ensure_ascii=False) if info else None
            if value != a["misplaced"] or issues != json.loads(a["issues"]):
                c.execute("UPDATE albums SET misplaced=?, issues=? WHERE dir=?", (value, json.dumps(issues), a["dir"]))
    return found
