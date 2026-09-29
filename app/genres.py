"""Genre harmonisation: map the hundreds of genre spellings found in the
library onto a short list of target genres."""
import re
from collections import Counter, defaultdict

from . import db, fixes
from .parsing import fold, is_various, primary_artist

DEFAULT_CANON = [
    "Hip-Hop", "Rap français", "Reggae", "Dancehall", "Dub", "Ska", "Soul / R&B", "Funk", "Jazz",
    "Blues", "Rock", "Punk", "Metal", "Pop", "Chanson française", "Électro", "Trip-Hop", "Folk",
    "Latin", "Afro", "Musiques du monde", "Classique", "Bande originale",
]

FRENCH_COUNTRIES = {"FR", "BE", "CH", "LU", "MC"}
INFER = "__infer__"      # use the artist's usual genre
REMOVE = "__remove__"    # delete the genre tag
KEEP = "__keep__"        # leave these files alone
JUNK = "__junk__"

# Checked in order on each part of the (folded) genre; first match wins.
RULES = [
    (r"\b(rap|hip ?hop)\b.*\b(fr|francais|france|fr\w*)\b|\b(francais|fr)\b.*\b(rap|hip ?hop)\b|^rap fr", "Rap français"),
    (r"trip ?hop|abstract", "Trip-Hop"),
    (r"dance ?hall|ragga", "Dancehall"),
    (r"dubstep", "Électro"),
    (r"\bdub\b", "Dub"),
    (r"\bska\b|rock ?steady", "Ska"),
    (r"reggae|\broots\b|lovers|rasta|rockers", "Reggae"),
    (r"hip ?hop|\brap\b|gangsta|grime|boom ?bap|mixtape|witch ?hop|turntabl|beat ?tape|хип|рэп", "Hip-Hop"),
    (r"acid jazz|soul jazz|jazz funk|jazz fusion", "Jazz"),
    (r"drum ?(and|n) ?bass|jungle|frenchcore|electro|electronic|electronique|techno|house|\bdance\b|edm|ambient|\bidm\b|breakbeat|downtempo|chill", "Électro"),
    (r"chanson|variete|french pop|pop francaise", "Chanson française"),
    (r"\bsoul\b|r and b|\brnb\b|\br n b\b|motown|gospel", "Soul / R&B"),
    (r"funk|disco", "Funk"),
    (r"jazz|\bbop\b|post bop|hard bop|swing|big band|fusion", "Jazz"),
    (r"blues", "Blues"),
    (r"metal", "Metal"),
    (r"punk|hardcore", "Punk"),
    (r"rock|alternati|\bindie?\b|grunge", "Rock"),
    (r"\bpop\b", "Pop"),
    (r"folk|country|bluegrass", "Folk"),
    (r"latin|salsa|cumbia|bossa|samba|zouk|kompa|merengue|reggaeton|bachata|brasil|\bmpb\b|colombia|tropical", "Latin"),
    (r"\bafro|africa|highlife|makossa|coupe decale|mbalax", "Afro"),
    (r"world|ethni|international|musiques? du monde|traditional|traditionnel|etranger|klezmer|fado|celtic", "Musiques du monde"),
    (r"^(?!.*(unclassi|inclass|hors class)).*\bclassi(cal|que)\b", "Classique"),
    (r"soundtrack|\bost\b|bande originale|\bfilm\b|\bscore\b|саундтрек", "Bande originale"),
    (r"^(other|others|unknown|inconnu|unbekannt|misc|miscellaneous|genre|unclassifiable|none|blank|default|"
     r"vocal|instrumental|spoken word|divers|autre|autres|general|various|unclassifiable|inclassable|genre inconnu|christmas|live|tribute|retro|styles|library|multi|vf|\d+)$|hors classification", JUNK),
]
_RULES = [(re.compile(p), g) for p, g in RULES]
URL = re.compile(r"www\.|https?:|\.(com|net|ru|org|club|fr|info|es)\b", re.I)
SPLIT = re.compile(r"\s*(?:[;,/|+]|\s-\s)\s*")


def canon_list():
    with db.session() as c:
        return db.get_meta(c, "genre_canon", DEFAULT_CANON)


def set_canon_list(values):
    values = [v.strip() for v in values if v and v.strip()]
    with db.session() as c:
        db.set_meta(c, "genre_canon", values or DEFAULT_CANON)


def overrides():
    with db.session() as c:
        return db.get_meta(c, "genre_map", {})


def set_override(raw, target):
    with db.session() as c:
        m = db.get_meta(c, "genre_map", {})
        if target is None:
            m.pop(raw or "", None)
        else:
            m[raw or ""] = target
        db.set_meta(c, "genre_map", m)


def classify(raw, canon=None):
    """(target, reason) for a raw genre value. target is a canonical genre,
    JUNK, or None when nothing matches (a 'weird' genre)."""
    canon = canon or DEFAULT_CANON
    by_fold = {fold(g): g for g in canon}
    if not raw:
        return None, "pas de genre"
    if fold(raw) in by_fold:
        return by_fold[fold(raw)], "même genre, autre écriture" if raw not in canon else "déjà propre"
    if URL.search(raw) or not re.search(r"\w", raw):
        return JUNK, "genre générique ou parasite"
    junk = False
    for part in [p for p in SPLIT.split(raw) if p.strip()] or [raw]:
        f = fold(part)
        if f in by_fold:
            return by_fold[f], f"d'après « {part.strip()} »"
        for rx, target in _RULES:
            if rx.search(f):
                if target == JUNK:
                    junk = True
                    break
                if target in canon:
                    return target, f"d'après « {part.strip()} »"
                break
    return (JUNK, "genre générique ou parasite") if junk else (None, "genre non reconnu")


def _artist_key(t):
    a = t["albumartist"] if t["albumartist"] and not is_various(t["albumartist"]) else t["artist"]
    return fold(primary_artist(a)) if a else ""


def _load():
    with db.session() as c:
        return [dict(r) for r in c.execute("SELECT path, dir, genre, artist, albumartist FROM tracks")]


def artist_genres(tracks, canon):
    """Artist -> most common canonical genre over the whole library."""
    cache, per_artist = {}, defaultdict(Counter)
    for t in tracks:
        if not t["genre"]:
            continue
        if t["genre"] not in cache:
            cache[t["genre"]] = classify(t["genre"], canon)[0]
        g = cache[t["genre"]]
        if g and g != JUNK:
            per_artist[_artist_key(t)][g] += 1
    return {a: c.most_common(1)[0][0] for a, c in per_artist.items() if a}


def _parent(d):
    return d.rsplit("/", 1)[0] if "/" in d else ""


def folder_genres(tracks, canon):
    """Folder -> most common canonical genre, for the folder and its parent."""
    cache, per_dir, per_parent = {}, defaultdict(Counter), defaultdict(Counter)
    for t in tracks:
        if not t["genre"]:
            continue
        if t["genre"] not in cache:
            cache[t["genre"]] = classify(t["genre"], canon)[0]
        g = cache[t["genre"]]
        if g and g != JUNK:
            per_dir[t["dir"]][g] += 1
            if _parent(t["dir"]):
                per_parent[_parent(t["dir"])][g] += 1
    top = lambda m: {k: c.most_common(1)[0][0] for k, c in m.items()}
    return top(per_dir), top(per_parent)


class Inferer:
    """Genre for a track whose own genre is useless: its artist's usual genre,
    else the genre of the rest of its folder, else of the parent folder."""

    def __init__(self, tracks, canon):
        self.by_artist = artist_genres(tracks, canon)
        self.by_dir, self.by_parent = folder_genres(tracks, canon)
        self.by_mb = {k: v for k, v in mb_artist_genres().items() if v in canon}

    def __call__(self, t):
        key = _artist_key(t)
        return (self.by_artist.get(key) or self.by_dir.get(t["dir"]) or self.by_mb.get(key)
                or self.by_parent.get(_parent(t["dir"])) or self.by_parent.get(t["dir"]))


def mb_artist_genres():
    with db.session() as c:
        return db.get_meta(c, "mb_artist_genres", {})


def missing_artists():
    """Artists with tracks for which no genre can be inferred: {key: display name}."""
    canon = canon_list()
    tracks = _load()
    infer = Inferer(tracks, canon)
    from . import discogs
    # None: MusicBrainz knew nothing, worth asking Discogs once it is set up.
    known = {k: v for k, v in mb_artist_genres().items() if v is not None or not discogs.enabled()}
    out = {}
    for t in tracks:
        if (t["genre"] and classify(t["genre"], canon)[0] not in (None, JUNK)) or infer(t):
            continue
        key = _artist_key(t)
        if key and key not in known:
            name = t["albumartist"] if t["albumartist"] and not is_various(t["albumartist"]) else t["artist"]
            out.setdefault(key, primary_artist(name))
    return out


def _first_canonical(tags, canon):
    for tag in tags:
        g = classify(tag, canon)[0]
        if g and g != JUNK:
            return g
    return None


def mb_lookup(job):
    """Ask MusicBrainz, then Discogs when set up, for the genre of artists we
    know nothing about. Stored per artist: the genre, None (MusicBrainz found
    nothing) or '' (neither source found anything)."""
    from . import discogs, musicbrainz
    canon = canon_list()
    todo = missing_artists()
    asked_mb = mb_artist_genres()
    job.total = len(todo)
    found = 0
    for key, name in todo.items():
        genre = None
        try:
            if key not in asked_mb:
                job.step(f"MusicBrainz : {name}")
                tags, country = musicbrainz.artist_tags(name)
                genre = _first_canonical(tags, canon)
                if genre == "Hip-Hop" and country in FRENCH_COUNTRIES and "Rap français" in canon:
                    genre = "Rap français"
            else:
                job.step()
            if not genre and discogs.enabled():
                job.message = f"Discogs : {name}"
                genre = _first_canonical(discogs.artist_genres(name), canon) or ""
        except Exception as e:  # network hiccup: keep going, retry next time
            job.error(name, e)
            continue
        with db.session() as c:
            m = db.get_meta(c, "mb_artist_genres", {})
            m[key] = genre
            db.set_meta(c, "mb_artist_genres", m)
        found += bool(genre)
    job.message = f"Genre trouvé pour {found} artiste(s) sur {len(todo)}"
    return {"artists": len(todo), "found": found}


def summary():
    """One row per raw genre value (None = no genre) with a proposed target."""
    canon = canon_list()
    over = overrides()
    tracks = _load()
    infer = Inferer(tracks, canon)
    rows = defaultdict(lambda: {"tracks": 0, "dirs": set(), "artists": Counter(), "inferred": Counter()})
    for t in tracks:
        r = rows[t["genre"] or ""]
        r["tracks"] += 1
        r["dirs"].add(t["dir"])
        name = t["albumartist"] if t["albumartist"] and not is_various(t["albumartist"]) else t["artist"]
        if name:
            r["artists"][primary_artist(name)] += 1
        r["inferred"][infer(t) or "?"] += 1
    out = []
    for raw, r in rows.items():
        target, reason = classify(raw, canon)
        if target is None or target == JUNK:
            proposal = INFER
        else:
            proposal = target
        status = "clean" if raw in canon else "junk" if target == JUNK else "weird" if target is None and raw else \
            "empty" if not raw else "map"
        chosen = over.get(raw)
        out.append({
            "raw": raw, "tracks": r["tracks"], "dirs": len(r["dirs"]),
            "artists": [a for a, _ in r["artists"].most_common(4)],
            "inferred": [[g, n] for g, n in r["inferred"].most_common(4)],
            "status": status, "reason": reason,
            "proposal": chosen or proposal, "user_choice": chosen is not None,
        })
    out.sort(key=lambda x: -x["tracks"])
    return {"canon": canon, "items": out}


def detail(raw):
    """Folders using a raw genre, with the genre inferred from their artist."""
    canon = canon_list()
    tracks = _load()
    infer = Inferer(tracks, canon)
    dirs = defaultdict(lambda: {"n": 0, "artist": None, "inferred": Counter()})
    for t in tracks:
        if (t["genre"] or "") != (raw or ""):
            continue
        d = dirs[t["dir"]]
        d["n"] += 1
        d["artist"] = d["artist"] or t["albumartist"] or t["artist"]
        d["inferred"][infer(t) or "?"] += 1
    return sorted(({"dir": k, "tracks": v["n"], "artist": v["artist"],
                    "inferred": v["inferred"].most_common(1)[0][0]} for k, v in dirs.items()),
                  key=lambda x: x["dir"].lower())


def apply(job, items):
    """items: [{raw, target}] where target is a canonical genre, INFER, REMOVE or KEEP."""
    canon = canon_list()
    tracks = _load()
    infer = Inferer(tracks, canon)
    wanted = {(i["raw"] or ""): i["target"] for i in items}
    for raw, target in wanted.items():
        set_override(raw, target)
    todo = {}
    skipped = 0
    for t in tracks:
        target = wanted.get(t["genre"] or "")
        if target is None or target == KEEP:
            continue
        if target == INFER:
            target = infer(t)
            if not target:
                skipped += 1
                continue
        # A French artist stays in 'Rap français' when a raw 'Rap' is mapped to Hip-Hop.
        if target == "Hip-Hop" and "Rap français" in canon and (
                infer.by_artist.get(_artist_key(t)) or infer.by_mb.get(_artist_key(t))) == "Rap français":
            target = "Rap français"
        value = None if target == REMOVE else target
        if (t["genre"] or None) != value:
            todo[t["path"]] = {"genre": value}
    job.total = len(todo)
    batch = fixes.new_batch()
    paths = list(todo)
    changed = 0
    for i in range(0, len(paths), 100):
        chunk = {p: todo[p] for p in paths[i:i + 100]}
        _, n, _ = fixes.write_many(chunk, f"Genres harmonisés ({len(wanted)} genre(s))", batch, job=job)
        changed += n
        job.step(f"Genres : {changed} fichiers modifiés", n=len(chunk))
    job.message = f"{changed} fichiers modifiés" + (f", {skipped} sans genre déductible (inchangés)" if skipped else "")
    return {"batch": batch, "files": changed, "skipped": skipped}
