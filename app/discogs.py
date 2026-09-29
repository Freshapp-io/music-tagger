"""Minimal Discogs client, returning the same structures as musicbrainz.py so
that matching, scoring and writing are shared. Release ids are prefixed with
'discogs:' everywhere in the app. Needs a personal access token (DISCOGS_TOKEN,
discogs.com > Settings > Developers); Discogs allows 60 requests/minute."""
import difflib
import re
import threading
import time

import httpx

from . import config
from .parsing import fold, is_various, norm_album

API = "https://api.discogs.com"
PREFIX = "discogs:"
_lock = threading.Lock()
_last = [0.0]
_cache = {}


def enabled():
    return bool(config.DISCOGS_TOKEN)


def _headers():
    return {"User-Agent": config.MB_USER_AGENT, "Authorization": f"Discogs token={config.DISCOGS_TOKEN}"}


def _get(path, params=None):
    if not enabled():
        raise ValueError("DISCOGS_TOKEN non configuré")
    params = params or {}
    key = (path, tuple(sorted(params.items())))
    if key in _cache:
        return _cache[key]
    for attempt in range(4):
        try:
            with _lock:
                wait = 1.05 - (time.time() - _last[0])
                if wait > 0:
                    time.sleep(wait)
                try:
                    r = httpx.get(f"{API}/{path}", params=params, headers=_headers(), timeout=20)
                finally:
                    _last[0] = time.time()
        except (httpx.TimeoutException, httpx.TransportError):
            if attempt == 3:
                raise
            time.sleep(2 * (attempt + 1))
            continue
        if r.status_code not in (429, 502, 503) or attempt == 3:
            break
        time.sleep(5 * (attempt + 1))          # rate limit: the window is one minute
    r.raise_for_status()
    data = r.json()
    if len(_cache) > 500:
        _cache.clear()
    _cache[key] = data
    return data


def _name(s):
    """'Nas (2)' -> 'Nas': Discogs numbers artists sharing a name."""
    return re.sub(r"\s*\(\d+\)$", "", (s or "").strip())


def _credit(artists):
    out = ""
    for a in artists or []:
        out += _name(a.get("anv") or a.get("name"))
        join = (a.get("join") or "").strip()
        out += ", " if join == "," else f" {join} " if join else ""
    out = out.strip(" ,")
    return "Various Artists" if is_various(out) else out


def _seconds(s):
    """'4:23' or '1:02:03' -> seconds, 0 when unknown."""
    try:
        n = 0
        for part in (s or "").strip().split(":"):
            n = n * 60 + int(part)
        return n
    except ValueError:
        return 0


def _ratio(a, b):
    return difflib.SequenceMatcher(None, a, b).ratio() if a and b else 0.0


def search(artist, album, n_tracks=None):
    """Releases matching the names, in the format of musicbrainz.search().
    Discogs gives no track count here (None) and no score: it is computed from
    the names."""
    params = {"type": "release", "per_page": 25}
    if album:
        params["release_title"] = album
    if artist and not is_various(artist):
        params["artist"] = artist
    data = _get("database/search", params)
    if not data.get("results"):
        data = _get("database/search", {"type": "release", "per_page": 25, "q": f"{artist or ''} {album or ''}".strip()})
    out = []
    for r in data.get("results", []):
        a, _, t = (r.get("title") or "").partition(" - ")
        if not t:
            a, t = "", a
        names = [_ratio(norm_album(album), norm_album(t))] if album else []
        if artist and not is_various(artist):
            names.append(_ratio(fold(artist), fold(_name(a))))
        out.append({
            "id": f"{PREFIX}{r['id']}", "title": t, "artist": _name(a),
            "date": str(r.get("year") or ""), "country": r.get("country", ""),
            "format": ", ".join(dict.fromkeys(r.get("format") or [])),
            "label": (r.get("label") or [""])[0], "tracks": None,
            "score": round(100 * sum(names) / len(names)) if names else 50,
            "status": "", "disambiguation": "", "source": "discogs",
            "genres": (r.get("genre") or []) + (r.get("style") or []),
        })
    out.sort(key=lambda x: -x["score"])
    return out


POSITION = re.compile(r"^(?:cd|dvd|dis[ck])?\s*(\d+)\s*[-.:]\s*(\d+)$", re.I)


def _tracklist(tracklist, album_artist):
    """Discogs tracklist -> musicbrainz.release() tracks. Headings are skipped,
    'index' tracks (a suite split in movements) count as one track. '2-05' or
    'CD2-5' give the disc; vinyl sides ('A1', 'B2') are one disc. Tracks are
    numbered 1..n on each disc."""
    items = []
    for t in tracklist or []:
        kind = t.get("type_") or "track"
        if kind not in ("track", "index"):
            continue
        m = POSITION.match(t.get("position") or "")
        items.append((int(m.group(1)) if m else 1, t))
    discs = sorted({d for d, _ in items}) or [1]
    renumber = {d: i for i, d in enumerate(discs, 1)}   # 'CD1'.. or odd first numbers
    counts = {}
    for d, _ in items:
        counts[renumber[d]] = counts.get(renumber[d], 0) + 1
    tracks, pos = [], {}
    for d, t in items:
        disc = renumber[d]
        pos[disc] = pos.get(disc, 0) + 1
        tracks.append({
            "disc": disc, "discs": len(discs), "position": pos[disc], "count": counts[disc],
            "title": t.get("title"), "artist": _credit(t.get("artists")) or album_artist, "artist_id": None,
            "recording_id": None, "track_id": None, "length": _seconds(t.get("duration")),
        })
    return tracks


def release(release_id):
    """Release in the format of musicbrainz.release(), plus 'genres'."""
    rid = str(release_id).removeprefix(PREFIX)
    r = _get(f"releases/{rid}")
    albumartist = _credit(r.get("artists"))
    date = (r.get("released") or str(r.get("year") or "")).replace("-00", "")
    images = r.get("images") or []
    front = next((i for i in images if i.get("type") == "primary"), images[0] if images else None)
    return {
        "id": f"{PREFIX}{r['id']}", "source": "discogs", "title": r.get("title"), "albumartist": albumartist,
        "albumartist_id": None, "date": date if date != "0" else "", "year": date[:4] if date != "0" else "",
        "release_group_id": f"discogs-master:{r['master_id']}" if r.get("master_id") else f"{PREFIX}{r['id']}",
        "compilation": is_various(albumartist),
        "cover": (front or {}).get("uri") or None,
        "genres": (r.get("genres") or []) + (r.get("styles") or []),
        "tracks": _tracklist(r.get("tracklist"), albumartist),
    }


def changes_for(rel, mapping):
    out = {}
    for m in mapping:
        if m.get("index") is None:
            continue
        t = rel["tracks"][m["index"]]
        out[m["path"]] = {
            "album": rel["title"], "albumartist": rel["albumartist"], "artist": t["artist"],
            "title": t["title"], "track": f"{t['position']}/{t['count']}",
            "disc": f"{t['disc']}/{t['discs']}", "year": rel["date"] or None,
            "compilation": "1" if rel["compilation"] else None,
            "discogs_releaseid": rel["id"].removeprefix(PREFIX),
        }
    return out


def cover(rel):
    """(mime, bytes) of the main image, or None."""
    if not rel.get("cover"):
        return None
    try:
        r = httpx.get(rel["cover"], headers=_headers(), timeout=30, follow_redirects=True)
        if r.status_code == 200 and r.headers.get("content-type", "").startswith("image/"):
            return r.headers["content-type"].split(";")[0], r.content
    except httpx.HTTPError:
        pass
    return None


def artist_genres(name):
    """Genres and styles of an artist's releases, most frequent first."""
    data = _get("database/search", {"type": "release", "artist": name, "per_page": 50})
    counts = {}
    for r in data.get("results", []):
        if fold(_name((r.get("title") or "").partition(" - ")[0])) != fold(name):
            continue
        for g in (r.get("style") or []) + (r.get("genre") or []):
            counts[g] = counts.get(g, 0) + 1
    return sorted(counts, key=lambda g: -counts[g])
