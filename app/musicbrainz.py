"""Minimal MusicBrainz client: search releases, fetch a tracklist, map it
onto the files of a folder. MusicBrainz asks for max 1 request/second."""
import difflib
import threading
import time

import httpx

from . import config
from .parsing import is_various, norm_title, parse_filename

API = "https://musicbrainz.org/ws/2"
_lock = threading.Lock()
_last = [0.0]
_cache = {}


def _get(path, params):
    key = (path, tuple(sorted(params.items())))
    if key in _cache:
        return _cache[key]
    with _lock:
        wait = 1.1 - (time.time() - _last[0])
        if wait > 0:
            time.sleep(wait)
        r = httpx.get(f"{API}/{path}", params={**params, "fmt": "json"},
                      headers={"User-Agent": config.MB_USER_AGENT}, timeout=20)
        _last[0] = time.time()
    r.raise_for_status()
    data = r.json()
    if len(_cache) > 500:
        _cache.clear()
    _cache[key] = data
    return data


def _credit(ac):
    return "".join(a.get("name", a.get("artist", {}).get("name", "")) + a.get("joinphrase", "") for a in ac or []).strip()


def _esc(s):
    return s.replace("\\", "\\\\").replace('"', '\\"')


def search(artist, album, n_tracks=None):
    q = f'release:"{_esc(album)}"'
    if artist and not is_various(artist):
        q += f' AND artist:"{_esc(artist)}"'
    data = _get("release", {"query": q, "limit": 25})
    if not data.get("releases"):
        data = _get("release", {"query": f"{album} {artist or ''}".strip(), "limit": 25})
    out = []
    for r in data.get("releases", []):
        tc = sum(m.get("track-count", 0) for m in r.get("media", []))
        out.append({
            "id": r["id"], "title": r.get("title"), "artist": _credit(r.get("artist-credit")),
            "date": r.get("date", ""), "country": r.get("country", ""),
            "format": " + ".join(sorted({m.get("format", "?") for m in r.get("media", [])})),
            "tracks": tc, "score": r.get("score", 0), "status": r.get("status", ""),
            "disambiguation": r.get("disambiguation", ""),
            "label": ", ".join(li.get("label", {}).get("name", "") for li in r.get("label-info", []) if li.get("label")),
        })
    if n_tracks:
        # Same score: prefer the release whose track count matches the folder.
        out.sort(key=lambda x: (-x["score"], abs(x["tracks"] - n_tracks)))
    return out


def search_recordings(artist, title):
    """Candidates for a single track (used to tag compilations track by track)."""
    q = f'recording:"{_esc(title)}"'
    if artist and not is_various(artist):
        q += f' AND artist:"{_esc(artist)}"'
    data = _get("recording", {"query": q, "limit": 15})
    out = []
    for r in data.get("recordings", []):
        ac = r.get("artist-credit") or []
        rels = r.get("releases") or []
        out.append({
            "id": r["id"], "title": r.get("title"), "artist": _credit(ac),
            "artist_id": ac[0]["artist"]["id"] if ac and ac[0].get("artist") else None,
            "length": round((r.get("length") or 0) / 1000),
            "year": (r.get("first-release-date") or "")[:4],
            "disambiguation": r.get("disambiguation", ""),
            "releases": [x.get("title") for x in rels[:3]],
            "score": r.get("score", 0),
        })
    return out


def artist_tags(name):
    """(tags most voted first, country code) of the artist best matching `name`."""
    from .parsing import fold
    data = _get("artist", {"query": f'artist:"{_esc(name)}"', "limit": 5})
    for a in data.get("artists", []):
        names = {fold(a.get("name")), fold(a.get("sort-name"))} | {fold(x.get("name")) for x in a.get("aliases", [])}
        if a.get("score", 0) >= 90 and fold(name) in names:
            tags = sorted(a.get("tags", []), key=lambda t: -t.get("count", 0))
            return [t["name"] for t in tags], a.get("country")
    return [], None


def release(release_id):
    r = _get(f"release/{release_id}", {"inc": "recordings artist-credits release-groups labels media"})
    aa = r.get("artist-credit") or []
    tracks = []
    media = r.get("media", [])
    for m in media:
        count = m.get("track-count") or len(m.get("tracks", []))
        for t in m.get("tracks", []):
            rec = t.get("recording", {})
            ac = t.get("artist-credit") or rec.get("artist-credit") or aa
            tracks.append({
                "disc": m.get("position", 1), "discs": len(media),
                "position": t.get("position"), "count": count,
                "title": t.get("title") or rec.get("title"),
                "artist": _credit(ac),
                "artist_id": ac[0]["artist"]["id"] if ac and ac[0].get("artist") else None,
                "recording_id": rec.get("id"), "track_id": t.get("id"),
                "length": round((t.get("length") or rec.get("length") or 0) / 1000),
            })
    albumartist = _credit(aa)
    return {
        "id": r["id"], "title": r.get("title"), "albumartist": albumartist,
        "albumartist_id": aa[0]["artist"]["id"] if aa and aa[0].get("artist") else None,
        "date": r.get("date", ""), "year": (r.get("date") or "")[:4],
        "release_group_id": (r.get("release-group") or {}).get("id"),
        "compilation": is_various(albumartist),
        "cover": bool((r.get("cover-art-archive") or {}).get("front")),
        "tracks": tracks,
    }


def match(files, rel):
    """Map each file to a release track. Returns [{path, index|None, score}]."""
    mb = rel["tracks"]
    by_pos = {}
    for i, t in enumerate(mb):
        by_pos[(t["disc"], t["position"])] = i
    discs = rel["tracks"][0]["discs"] if mb else 1

    def file_pos(f):
        g = parse_filename(f["filename"])
        tr = f["track"] or g.get("track")
        dc = f["disc"] or g.get("disc") or "1"
        try:
            return int(str(dc).split("/")[0]) if discs > 1 else 1, int(str(tr).split("/")[0])
        except (TypeError, ValueError):
            return None

    def sim(f, t):
        a = norm_title(f["title"] or parse_filename(f["filename"]).get("title"))
        s = difflib.SequenceMatcher(None, a, norm_title(t["title"])).ratio()
        if f.get("duration") and t["length"]:
            if abs(f["duration"] - t["length"]) <= 3:
                s += 0.3
            elif abs(f["duration"] - t["length"]) > 20:
                s -= 0.3
        return s

    result, used = [], set()
    # 1) by track number, confirmed by title or duration
    pending = []
    for f in files:
        p = file_pos(f)
        i = by_pos.get(p) if p else None
        same_count = len(files) == len(mb)
        if i is not None and i not in used and (same_count or sim(f, mb[i]) >= 0.45):
            used.add(i)
            result.append({"path": f["path"], "index": i, "score": round(sim(f, mb[i]), 2)})
        else:
            pending.append(f)
    # 2) best remaining title match
    for f in pending:
        best, bs = None, 0.5
        for i, t in enumerate(mb):
            if i in used:
                continue
            s = sim(f, t)
            if s > bs:
                best, bs = i, s
        if best is not None:
            used.add(best)
        result.append({"path": f["path"], "index": best, "score": round(bs, 2) if best is not None else 0})
    return result


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
            "mb_albumid": rel["id"], "mb_albumartistid": rel["albumartist_id"],
            "mb_artistid": t["artist_id"], "mb_releasegroupid": rel["release_group_id"],
            "mb_trackid": t["recording_id"], "mb_releasetrackid": t["track_id"],
        }
    return out


def cover(release_id):
    """(mime, bytes) of the front cover, or None."""
    try:
        r = httpx.get(f"https://coverartarchive.org/release/{release_id}/front-500",
                      headers={"User-Agent": config.MB_USER_AGENT}, timeout=30, follow_redirects=True)
        if r.status_code == 200:
            return r.headers.get("content-type", "image/jpeg").split(";")[0], r.content
    except httpx.HTTPError:
        pass
    return None
