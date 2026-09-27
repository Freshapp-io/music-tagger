from app import musicbrainz


def rel(titles, lengths):
    n = len(titles)
    return {"id": "r", "title": "Alb", "albumartist": "A", "albumartist_id": "a", "date": "2001",
            "year": "2001", "release_group_id": "g", "compilation": False, "cover": False,
            "tracks": [{"disc": 1, "discs": 1, "position": i + 1, "count": n, "title": t, "artist": "A",
                        "artist_id": "a", "recording_id": f"rec{i}", "track_id": f"t{i}", "length": l}
                       for i, (t, l) in enumerate(zip(titles, lengths))]}


def f(path, filename, title=None, track=None, duration=0):
    return {"path": path, "filename": filename, "title": title, "track": track, "disc": None, "duration": duration}


def test_match_by_title_when_order_differs():
    r = rel(["Intro", "Nautilus", "Jamaica"], [60, 300, 200])
    files = [f("x/a.mp3", "a.mp3", "Jamaica", "1", 200), f("x/b.mp3", "b.mp3", "Nautilus", "5", 301)]
    m = {x["path"]: x["index"] for x in musicbrainz.match(files, r)}
    assert m == {"x/a.mp3": 2, "x/b.mp3": 1}


def test_match_from_filenames_and_changes():
    r = rel(["Intro", "Nautilus"], [60, 300])
    files = [f("x/01 - intro.mp3", "01 - intro.mp3"), f("x/02 - nautilus.mp3", "02 - nautilus.mp3")]
    mapping = musicbrainz.match(files, r)
    ch = musicbrainz.changes_for(r, mapping)
    assert ch["x/02 - nautilus.mp3"]["title"] == "Nautilus"
    assert ch["x/02 - nautilus.mp3"]["track"] == "2/2"
    assert ch["x/01 - intro.mp3"]["mb_trackid"] == "rec0"


def test_unrelated_file_left_unmapped():
    r = rel(["Intro", "Nautilus", "Jamaica"], [60, 300, 200])
    files = [f("x/z.mp3", "z.mp3", "Completely Different Song", None, 999)]
    assert musicbrainz.match(files, r)[0]["index"] is None


def test_retries_when_musicbrainz_is_busy(monkeypatch):
    import httpx
    calls = []

    def fake_get(url, params=None, headers=None, timeout=None):
        calls.append(url)
        code = 503 if len(calls) < 3 else 200
        return httpx.Response(code, json={"recordings": []}, request=httpx.Request("GET", url))
    monkeypatch.setattr(musicbrainz.httpx, "get", fake_get)
    monkeypatch.setattr(musicbrainz.time, "sleep", lambda s: None)
    musicbrainz._cache.clear()
    assert musicbrainz.search_recordings("Aretha Franklin", "Respect") == []
    assert len(calls) == 3


def test_toc_from_durations():
    # 150-sector lead-in, 75 sectors per second, lead-out after the last track
    assert musicbrainz.toc([2, 3]) == "1 2 525 150 300"


def test_by_durations_uses_fuzzy_toc(monkeypatch):
    seen = {}

    def fake_get(path, params):
        seen.update(path=path, **params)
        return {"releases": [
            {"id": "r1", "title": "Kind of Blue", "artist-credit": [{"name": "Miles Davis", "joinphrase": ""}],
             "date": "1959", "media": [{"format": "CD", "track-count": 5}]},
            {"id": "r1", "title": "dup"}]}
    monkeypatch.setattr(musicbrainz, "_get", fake_get)
    res = musicbrainz.by_durations([562, 586, 337, 693, 566])
    assert seen["path"] == "discid/-" and seen["toc"].startswith("1 5 ") and seen["cdstubs"] == "no"
    assert [(r["id"], r["tracks"], r["by_durations"]) for r in res] == [("r1", 5, True)]
    assert musicbrainz.by_durations([100, None]) == []     # unknown length: no lookup


def test_match_picks_the_disc_whose_lengths_fit():
    tracks = []
    for disc, lengths in ((1, [100, 200, 300]), (2, [410, 120, 250])):
        for i, l in enumerate(lengths, 1):
            tracks.append({"disc": disc, "discs": 2, "position": i, "count": 3, "title": f"T{disc}{i}",
                           "artist": "A", "artist_id": None, "recording_id": f"r{disc}{i}", "track_id": None, "length": l})
    rel = {"tracks": tracks}
    files = [f(f"x/0{i}.mp3", f"0{i}.mp3", None, str(i), d) for i, d in enumerate([409, 121, 251], 1)]
    got = [tracks[m["index"]]["disc"] for m in musicbrainz.match(files, rel)]
    assert got == [2, 2, 2]


def test_retries_after_a_timeout(monkeypatch):
    import httpx
    calls = []

    def fake_get(url, params=None, headers=None, timeout=None):
        calls.append(url)
        if len(calls) == 1:
            raise httpx.ReadTimeout("The read operation timed out")
        return httpx.Response(200, json={"recordings": []}, request=httpx.Request("GET", url))
    monkeypatch.setattr(musicbrainz.httpx, "get", fake_get)
    monkeypatch.setattr(musicbrainz.time, "sleep", lambda s: None)
    musicbrainz._cache.clear()
    assert musicbrainz.search_recordings("X", "Y") == [] and len(calls) == 2
