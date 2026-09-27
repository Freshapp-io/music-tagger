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
