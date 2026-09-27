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
    # MusicBrainz answers 503 when it is busy or asked too fast: back off and retry.
    # Timeouts and dropped connections are retried as well.
    for attempt in range(4):
        try:
            with _lock:
                wait = 1.1 - (time.time() - _last[0])
                if wait > 0:
                    time.sleep(wait)
                try:
                    r = httpx.get(f"{API}/{path}", params={**params, "fmt": "json"},
                                  headers={"User-Agent": config.MB_USER_AGENT}, timeout=20)
                finally:
                    _last[0] = time.time()
        except (httpx.TimeoutException, httpx.TransportError):
            if attempt == 3:
                raise
            time.sleep(2 * (attempt + 1))
            continue
        if r.status_code not in (429, 503) or attempt == 3:
            break
        time.sleep(2 * (attempt + 1))
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


def _summary(r, **extra):
    media = r.get("media", [])
    return {
        "id": r["id"], "title": r.get("title"), "artist": _credit(r.get("artist-credit")),
        "date": r.get("date", ""), "country": r.get("country", ""),
        "format": " + ".join(sorted({m.get("format") or "?" for m in media})),
        "tracks": sum(m.get("track-count") or len(m.get("tracks", [])) for m in media),
        "score": r.get("score", 100), "status": r.get("status", ""),
        "disambiguation": r.get("disambiguation", ""),
        "label": ", ".join(li.get("label", {}).get("name", "") for li in r.get("label-info", []) if li.get("label")),
        **extra,
    }


def toc(durations):
    """CD table of contents built from track lengths in seconds: 75 sectors per
    second, first track after the standard 2 s (150 sectors) lead-in."""
    offsets, pos = [], 150
    for d in durations:
        offsets.append(pos)
        pos += max(1, int(round(d * 75)))
    return f"1 {len(durations)} {pos} " + " ".join(map(str, offsets))


def by_durations(durations):
    """Releases whose CD track lengths match these durations, in this order
    (MusicBrainz fuzzy TOC lookup: no title or artist needed; a few seconds
    of difference per track are tolerated, the track count must be equal)."""
    if not durations or len(durations) > 99 or any(not d for d in durations):
        return []
    data = _get("discid/-", {"toc": toc(durations), "cdstubs": "no", "inc": "artist-credits labels"})
    seen, out = set(), []
    for r in data.get("releases", []):
        if r["id"] not in seen:
            seen.add(r["id"])
            out.append(_summary(r, by_durations=True))
    return out


def search(artist, album, n_tracks=None):
    q = f'release:"{_esc(album)}"'
    if artist and not is_various(artist):
        q += f' AND artist:"{_esc(artist)}"'
    data = _get("release", {"query": q, "limit": 25})
    if not data.get("releases"):
        data = _get("release", {"query": f"{album} {artist or ''}".strip(), "limit": 25})
    out = [_summary(r) for r in data.get("releases", [])]
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
    default_disc = 1
    if discs > 1:
        # A folder usually holds one disc of a box set: pick the medium with the
        # same number of tracks and the closest lengths.
        lengths = [f.get("duration") or 0 for f in files]
        best = None
        for d in range(1, discs + 1):
            medium = [t["length"] for t in mb if t["disc"] == d]
            if len(medium) == len(files):
                diff = sum(abs(a - b) for a, b in zip(lengths, medium))
                if best is None or diff < best[0]:
                    best = (diff, d)
        if best:
            default_disc = best[1]

    def file_pos(f):
        g = parse_filename(f["filename"])
        tr = f["track"] or g.get("track")
        dc = f["disc"] or g.get("disc") or str(default_disc)
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
        # Trust the track number when the folder is the whole medium, when the
        # title looks alike, or when the length matches to within 3 s.
        same_count = len(files) in (len(mb), len([t for t in mb if t["disc"] == default_disc]))
        close = i is not None and f.get("duration") and mb[i]["length"] and abs(f["duration"] - mb[i]["length"]) <= 3
        if i is not None and i not in used and (same_count or close or sim(f, mb[i]) >= 0.45):
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
