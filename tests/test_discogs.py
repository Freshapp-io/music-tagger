"""Discogs as a second source: parsing, manual matching, auto-tag, genres (API mocked)."""
from conftest import login, make_mp3

from app import autotag, config, db, discogs, genres, musicbrainz, scanner, tagger

SEARCH = {"results": [
    {"id": 111, "title": "Nas (2) - Illmatic", "year": 1994, "country": "US", "format": ["CD", "Album"],
     "label": ["Columbia"], "genre": ["Hip Hop"], "style": ["Conscious"]},
    {"id": 222, "title": "Nas - Illmatic XX", "year": 2014, "format": ["CD"], "label": ["Legacy"]},
]}
RELEASE = {
    "id": 111, "master_id": 9, "title": "Illmatic", "released": "1994-04-19", "year": 1994,
    "artists": [{"name": "Nas (2)", "anv": "", "join": ""}],
    "genres": ["Hip Hop"], "styles": ["Conscious", "Boom Bap"],
    "images": [{"type": "secondary", "uri": "https://i.discogs.com/b.jpg"},
               {"type": "primary", "uri": "https://i.discogs.com/a.jpg"}],
    "tracklist": [
        {"position": "", "type_": "heading", "title": "Side 40th St."},
        {"position": "A1", "type_": "track", "title": "The Genesis", "duration": "1:45"},
        {"position": "A2", "type_": "track", "title": "N.Y. State Of Mind", "duration": "4:54"},
        {"position": "B1", "type_": "track", "title": "One Love", "duration": "5:25",
         "artists": [{"name": "Nas (2)", "join": "Feat."}, {"name": "Q-Tip", "join": ""}]},
    ],
}


def fake_api(monkeypatch, responses):
    monkeypatch.setattr(config, "DISCOGS_TOKEN", "tok")
    calls = []

    def get(path, params=None):
        calls.append((path, params))
        for prefix, data in responses.items():
            if path.startswith(prefix):
                return data
        return {}
    monkeypatch.setattr(discogs, "_get", get)
    return calls


def test_parsing(monkeypatch):
    fake_api(monkeypatch, {"database/search": SEARCH, "releases/111": RELEASE})
    res = discogs.search("Nas", "Illmatic")
    assert res[0]["id"] == "discogs:111" and res[0]["artist"] == "Nas" and res[0]["title"] == "Illmatic"
    assert res[0]["score"] == 100 and res[0]["score"] > res[1]["score"]
    rel = discogs.release("discogs:111")
    assert (rel["albumartist"], rel["date"], rel["cover"]) == ("Nas", "1994-04-19", "https://i.discogs.com/a.jpg")
    assert [(t["disc"], t["position"], t["count"], t["length"]) for t in rel["tracks"]] == \
        [(1, 1, 3, 105), (1, 2, 3, 294), (1, 3, 3, 325)]
    assert rel["tracks"][2]["artist"] == "Nas Feat. Q-Tip" and rel["tracks"][0]["artist"] == "Nas"


def test_multi_disc_and_various():
    tracks = discogs._tracklist([
        {"position": "CD1-1", "title": "a"}, {"position": "CD1-2", "title": "b"},
        {"position": "2-1", "title": "c", "artists": [{"name": "X", "join": ","}, {"name": "Y"}]},
    ], "Various Artists")
    assert [(t["disc"], t["discs"], t["position"], t["count"]) for t in tracks] == \
        [(1, 2, 1, 2), (1, 2, 2, 2), (2, 2, 1, 1)]
    assert tracks[2]["artist"] == "X, Y"
    assert tracks[2]["artists"] == "X; Y" and tracks[0]["artists"] == "Various Artists"
    assert discogs._credit([{"name": "Various"}]) == "Various Artists"
    assert discogs._seconds("1:02:03") == 3723 and discogs._seconds("") == 0


def test_disabled_without_token(library, monkeypatch):
    from fastapi.testclient import TestClient

    from app.main import app
    monkeypatch.setattr(config, "DISCOGS_TOKEN", None)
    with TestClient(app) as client:
        login(client)
        assert client.get("/api/status").json()["discogs"] is False
        assert client.get("/api/discogs/search", params={"album": "x"}).status_code == 400


def _folder(library):
    for i, (t, secs) in enumerate([("The Genesis", 105), ("NY State of Mind", 294), ("One Love", 325)], 1):
        make_mp3(library / f"Nas - Illmatic/{i:02d} - {t}.mp3", seconds=secs)


def test_manual_match_and_apply(library, job, monkeypatch):
    from fastapi.testclient import TestClient

    from app.main import app
    fake_api(monkeypatch, {"database/search": SEARCH, "releases/111": RELEASE})
    monkeypatch.setattr(discogs, "cover", lambda rel: None)
    _folder(library)
    scanner.scan(job)
    with TestClient(app) as client:
        login(client)
        assert client.get("/api/status").json()["discogs"] is True
        found = client.get("/api/discogs/search", params={"artist": "Nas", "album": "Illmatic"}).json()
        assert found[0]["source"] == "discogs"
        m = client.get("/api/mb/match", params={"dir": "Nas - Illmatic", "release": "discogs:111"}).json()
        assert [x["index"] for x in m["mapping"]] == [0, 1, 2] and m["genre"] == "Hip-Hop"
        r = client.post("/api/mb/apply", json={"dir": "Nas - Illmatic", "release": "discogs:111",
                                                "mapping": m["mapping"], "genre": True}).json()
        assert r["files"] == 3
    p = str(library / "Nas - Illmatic/03 - One Love.mp3")
    got = tagger.read_fields(p, ["albumartist", "album", "title", "track", "year", "genre", "discogs_releaseid", "artist"])
    assert got == {"albumartist": "Nas", "album": "Illmatic", "title": "One Love", "track": "3/3",
                   "year": "1994-04-19", "genre": "Hip-Hop", "discogs_releaseid": "111", "artist": "Nas Feat. Q-Tip"}
    with db.session() as c:
        assert c.execute("SELECT label FROM history LIMIT 1").fetchone()[0] == "Discogs : Nas – Illmatic"


def test_autotag_uses_discogs_when_musicbrainz_has_nothing(library, job, monkeypatch):
    fake_api(monkeypatch, {"database/search": SEARCH, "releases/111": RELEASE, "releases/222": {**RELEASE, "id": 222, "title": "Illmatic XX", "master_id": 9}})
    monkeypatch.setattr(discogs, "cover", lambda rel: None)
    monkeypatch.setattr(musicbrainz, "by_durations", lambda d: [])
    monkeypatch.setattr(musicbrainz, "search", lambda a, b, n=None: [])
    _folder(library)
    scanner.scan(job)
    res = autotag.analyse_dir("Nas - Illmatic")
    assert res["release_id"] == "discogs:111" and res["status"] == "ok" and res["score"] >= 95
    autotag.analyse(job, ["Nas - Illmatic"])
    out = autotag.apply(job, ["Nas - Illmatic"], {"cover": False})
    assert out["files"] == 3 and not job.errors
    with db.session() as c:
        t = c.execute("SELECT album, genre FROM tracks WHERE filename LIKE '01%'").fetchone()
        assert tuple(t) == ("Illmatic", "Hip-Hop")


def test_genre_lookup_falls_back_on_discogs(library, job, monkeypatch):
    fake_api(monkeypatch, {"database/search": SEARCH})
    asked = []
    monkeypatch.setattr(musicbrainz, "artist_tags", lambda name: asked.append(name) or ([], None))
    make_mp3(library / "Nas/x/01.mp3", artist="Nas", title="t")
    scanner.scan(job)
    genres.mb_lookup(job)
    assert asked == ["Nas"] and genres.mb_artist_genres() == {"nas": "Hip-Hop"}

    # an artist MusicBrainz already failed on is only asked to Discogs
    with db.session() as c:
        db.set_meta(c, "mb_artist_genres", {"nas": None})
    genres.mb_lookup(job)
    assert asked == ["Nas"] and genres.mb_artist_genres() == {"nas": "Hip-Hop"}


def test_release_score_endpoint(library, job, monkeypatch):
    """The manual search grades a release like auto-tagging does."""
    from fastapi.testclient import TestClient

    from app.main import app
    other = {**RELEASE, "id": 333, "title": "Stillmatic", "tracklist": [
        {"position": "1", "title": "Stillmatic Intro", "duration": "2:10"},
        {"position": "2", "title": "Ether", "duration": "4:37"},
        {"position": "3", "title": "Got Ur Self A...", "duration": "4:26"},
        {"position": "4", "title": "Smokin'", "duration": "4:21"}]}
    fake_api(monkeypatch, {"releases/111": RELEASE, "releases/333": other})
    _folder(library)
    scanner.scan(job)
    with TestClient(app) as client:
        login(client)
        good = client.get("/api/release/score", params={"dir": "Nas - Illmatic", "release": "discogs:111",
                                                         "artist": "Nas", "album": "Illmatic"}).json()
        bad = client.get("/api/release/score", params={"dir": "Nas - Illmatic", "release": "discogs:333",
                                                        "artist": "Nas", "album": "Illmatic"}).json()
    assert good["score"] >= 95 and good["medium_tracks"] == 3 and good["durations"] == 100
    assert bad["score"] < 60 and bad["medium_tracks"] == 4


def test_split_artists_setting(library, job, monkeypatch):
    from fastapi.testclient import TestClient

    from app.main import app
    duo = {**RELEASE, "artists": [{"name": "Nas (2)", "join": "&"}, {"name": "DJ Premier", "join": ""}]}
    fake_api(monkeypatch, {"releases/111": duo})
    monkeypatch.setattr(discogs, "cover", lambda rel: None)
    _folder(library)
    scanner.scan(job)
    p = str(library / "Nas - Illmatic/01 - The Genesis.mp3")
    with TestClient(app) as client:
        login(client)
        assert client.get("/api/status").json()["split_artists"] is True        # default
        m = client.get("/api/mb/match", params={"dir": "Nas - Illmatic", "release": "discogs:111"}).json()
        client.post("/api/mb/apply", json={"dir": "Nas - Illmatic", "release": "discogs:111", "mapping": m["mapping"]})
        assert tagger.read_fields(p, ["albumartist", "artist"]) == {"albumartist": "Nas; DJ Premier", "artist": "Nas; DJ Premier"}

        assert client.put("/api/settings", json={"split_artists": False}).json() == {"split_artists": False}
        client.post("/api/mb/apply", json={"dir": "Nas - Illmatic", "release": "discogs:111", "mapping": m["mapping"]})
        assert tagger.read_fields(p, ["albumartist"]) == {"albumartist": "Nas & DJ Premier"}
