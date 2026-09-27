import json

from conftest import login, make_mp3

from app import analysis, db, fixes, scanner, tagger


def albums():
    with db.session() as c:
        return {r["dir"]: dict(r, issues=json.loads(r["issues"]), suggestion=json.loads(r["suggestion"]))
                for r in c.execute("SELECT * FROM albums")}


def test_tag_roundtrip_and_undo(library):
    p = make_mp3(library / "a.mp3", artist="X", title="T")
    before, after = tagger.write(str(p), {"artist": "Y", "albumartist": "Y", "title": "T"})
    assert before == {"artist": "X", "albumartist": None} and after == {"artist": "Y", "albumartist": "Y"}
    r = tagger.read(str(p))
    assert (r["artist"], r["albumartist"], r["bitrate"]) == ("Y", "Y", 128)
    tagger.write(str(p), before)
    assert tagger.read_fields(str(p), ["artist", "albumartist"]) == {"artist": "X", "albumartist": None}


def test_untagged_file_gets_tags(library):
    p = make_mp3(library / "u.mp3")
    assert tagger.read(str(p))["tagged"] == 0
    tagger.write(str(p), {"title": "Hello"})
    assert tagger.read(str(p))["title"] == "Hello"


def test_scan_detects_issues(library, job):
    # untagged album: info only in folder / file names
    for i, t in enumerate(["A chaque frere", "Ma destinee", "Macadam"], 1):
        make_mp3(library / f"Youssoupha/Youssoupha-A Chaque Frere 2007/0{i} - {t}.mp3")
    # wrongly tagged Various Artists
    for i in range(1, 6):
        artist = "The Roots" if i != 3 else "The Roots feat. Common"
        make_mp3(library / f"The Roots/Game Theory/{i:02d}.mp3", artist=artist, albumartist="Various Artists",
                 album="Game Theory", title=f"Song {i}", track=str(i), year="2006")
    # a real compilation, already clean
    for i, a in enumerate(["Nas", "Jay-Z", "Mobb Deep", "Big L"], 1):
        make_mp3(library / f"VA - Rap Classics/{i:02d}.mp3", artist=a, albumartist="Various Artists",
                 album="Rap Classics", title=f"Classic {i}", compilation="1")
    # album that Navidrome would split: no album artist, mixed years
    for i in range(1, 5):
        make_mp3(library / f"Black Moon/Enta Da Stage/{i:02d}.mp3",
                 artist="Black Moon" + (" feat. Havoc" if i == 2 else ""), album="Enta Da Stage",
                 title=f"Track {i}", year="1993" if i < 4 else "1994")

    scanner.scan(job)
    assert not job.errors
    a = albums()

    y = a["Youssoupha/Youssoupha-A Chaque Frere 2007"]
    assert "untagged" in y["issues"]
    assert y["suggestion"]["albumartist"] == "Youssoupha" and y["suggestion"]["album"] == "A Chaque Frere"

    r = a["The Roots/Game Theory"]
    assert "various" in r["issues"] and "inconsistent" not in r["issues"]
    assert r["suggestion"]["albumartist"] == "The Roots" and r["suggestion"]["confidence"] == "high"

    assert "various" not in a["VA - Rap Classics"]["issues"]

    b = a["Black Moon/Enta Da Stage"]
    assert {"inconsistent", "albumartist_missing", "year_mixed"} <= set(b["issues"])
    assert b["suggestion"]["albumartist"] == "Black Moon"

    # apply suggestions to everything, then check the issues are gone
    fixes.apply_suggestions(job, list(a), ("albumartist", "album", "year", "compilation", "tracks"))
    assert not job.errors
    a2 = albums()
    for d in ("Youssoupha/Youssoupha-A Chaque Frere 2007", "The Roots/Game Theory", "Black Moon/Enta Da Stage"):
        assert a2[d]["issues"] == [], (d, a2[d]["issues"])
    with db.session() as c:
        t = c.execute("SELECT * FROM tracks WHERE filename='02 - Ma destinee.mp3'").fetchone()
        assert (t["artist"], t["title"], t["track"], t["album"], t["year"]) == \
            ("Youssoupha", "Ma destinee", "2", "A Chaque Frere", "2007")
        batch = c.execute("SELECT batch FROM history LIMIT 1").fetchone()[0]

    # undo restores the original state
    res = fixes.undo(batch)
    assert not res["errors"]
    a3 = albums()
    assert "untagged" in a3["Youssoupha/Youssoupha-A Chaque Frere 2007"]["issues"]
    assert "various" in a3["The Roots/Game Theory"]["issues"]


def test_duplicates_keep_best_and_undo(library, job):
    titles = ["Intro", "Nautilus", "Take Me To The Mardi Gras", "Jamaica", "Westchester Lady"]
    hq = b"\xff\xfb\xe0\x64" + b"\x00" * (1044 - 4)   # 320 kbps frame
    for i, t in enumerate(titles, 1):
        make_mp3(library / f"Bob James/Bob James - Two (1975)/{i:02d} {t}.mp3", bitrate_frames=hq,
                 artist="Bob James", albumartist="Bob James", album="Two", title=t, track=str(i))
        make_mp3(library / f"Bob_James-Two-1975-GRP/{i:02d}-bob_james-{t.lower().replace(' ', '_')}.mp3",
                 artist="Bob James", album="Two (Remastered)", title=t)
    # A 2-disc album must not be seen as a duplicate of itself
    for disc in (1, 2):
        for i in range(1, 6):
            make_mp3(library / f"Pink Floyd/The Wall/CD{disc}/{i:02d}.mp3", artist="Pink Floyd",
                     albumartist="Pink Floyd", album="The Wall", title=f"Part {disc}-{i}")
    scanner.scan(job)
    a = albums()
    g1 = a["Bob James/Bob James - Two (1975)"]["dup_group"]
    assert g1 and g1 == a["Bob_James-Two-1975-GRP"]["dup_group"]
    assert a["Bob James/Bob James - Two (1975)"]["quality"] > a["Bob_James-Two-1975-GRP"]["quality"]
    assert a["Pink Floyd/The Wall/CD1"]["dup_group"] is None

    res = fixes.resolve_duplicates(job, [{"keep": "Bob James/Bob James - Two (1975)", "remove": ["Bob_James-Two-1975-GRP"]}])
    assert res["moved"] == 1 and not job.errors
    assert not (library / "Bob_James-Two-1975-GRP").exists()
    assert (library / ".music-tagger-trash/.ndignore").exists()
    assert "Bob_James-Two-1975-GRP" not in albums()

    # the trash is not scanned
    scanner.scan(job)
    assert "Bob_James-Two-1975-GRP" not in albums()

    fixes.undo(res["batch"])
    assert (library / "Bob_James-Two-1975-GRP/01-bob_james-intro.mp3").exists()
    assert albums()["Bob_James-Two-1975-GRP"]["dup_group"]


def test_save_album_does_not_clear_untouched_fields(library, job):
    make_mp3(library / "X/01.mp3", artist="A", album="Alb", title="One", year="2001")
    scanner.scan(job)
    fixes.save_album("X", {"albumartist": "A"}, {"X/01.mp3": {"title": "Uno"}})
    with db.session() as c:
        t = c.execute("SELECT * FROM tracks").fetchone()
    assert (t["albumartist"], t["title"], t["album"], t["year"]) == ("A", "Uno", "Alb", "2001")


def test_api_smoke(library, job):
    from fastapi.testclient import TestClient

    from app.main import app
    make_mp3(library / "Artist - Album (2010)/01 - Song.mp3")
    scanner.scan(job)
    with TestClient(app) as client:
        login(client)
        s = client.get("/api/status").json()
        assert s["tracks"] == 1 and s["counts"]["untagged"] == 1
        items = client.get("/api/albums", params={"issue": "untagged"}).json()["items"]
        assert items[0]["suggestion"]["albumartist"] == "Artist"
        d = client.get("/api/album", params={"dir": "Artist - Album (2010)"}).json()
        assert d["tracks"][0]["guess"] == {"track": "1", "title": "Song"}
        prev = client.get("/api/album/preview", params={"dir": "Artist - Album (2010)"}).json()
        assert prev["Artist - Album (2010)/01 - Song.mp3"]["artist"] == "Artist"
        assert client.post("/api/ignore", json={"dirs": ["Artist - Album (2010)"], "kind": "untagged"}).status_code == 200
        assert client.get("/api/status").json()["counts"]["untagged"] == 0
        assert client.get("/").status_code == 200
        assert client.get("/api/album", params={"dir": "../etc"}).status_code == 404


def test_path_traversal_rejected():
    import pytest
    with pytest.raises(ValueError):
        scanner.absolute("../../etc/passwd")


def test_unreadable_folder_does_not_drop_its_tracks(library, job, monkeypatch):
    import os as _os
    make_mp3(library / "A/01.mp3", artist="A", title="x")
    make_mp3(library / "B/01.mp3", artist="B", title="y")
    scanner.scan(job)
    real = _os.scandir

    def flaky(d):
        if str(d).endswith("/B"):
            raise OSError(53, "network path not found")
        return real(d)
    monkeypatch.setattr(scanner.os, "scandir", flaky)
    monkeypatch.setattr(scanner.time, "sleep", lambda s: None)
    scanner.scan(job)
    with db.session() as c:
        assert c.execute("SELECT COUNT(*) FROM tracks").fetchone()[0] == 2


def test_empty_root_aborts_scan(library, job, monkeypatch):
    import pytest
    make_mp3(library / "A/01.mp3", artist="A", title="x")
    scanner.scan(job)
    import shutil
    shutil.rmtree(library / "A")
    with pytest.raises(scanner.RootUnavailable):
        scanner.scan(job)
    with db.session() as c:
        assert c.execute("SELECT COUNT(*) FROM tracks").fetchone()[0] == 1


def test_duplicate_false_positives():
    from app.analysis import find_duplicate_groups

    def alb(d, artist, album, titles):
        return {"dir": d, "norm_artist": artist, "norm_album": album, "titles": json.dumps(titles)}
    songs = ["give up the funk", "flash light", "up for the down stroke", "mothership connection", "p funk"]
    groups = find_duplicate_groups([
        # 2 CDs of a live album sharing songs
        alb("Otis/Live CD1 (2010)", "otis redding", "live on the sunset strip", songs),
        alb("Otis/Live CD2 (2010)", "otis redding", "live on the sunset strip", songs),
        # two best-ofs sharing songs
        alb("P/Greatest Hits", "parliament", "greatest hits", songs),
        alb("P/The Best Of Parliament", "parliament", "the best of parliament", songs),
        # generic titles only
        alb("A/x", "a", "x", ["track 01", "track 02", "track 03"]),
        alb("B/y", "b", "y", ["track 01", "track 02", "track 03"]),
    ])
    assert groups == []


def test_review_mode_endpoints(library, job):
    from fastapi.testclient import TestClient

    from app.main import app
    # compilation with 'Artist - Title' file names
    for i, (a, t) in enumerate([("Nas", "The World Is Yours"), ("Jay-Z", "Dead Presidents"), ("Big L", "Ebonics")], 1):
        make_mp3(library / f"VA - Rap Classics/0{i} - {a} - {t}.mp3", albumartist="Various Artists", album="Rap Classics")
    titles = ["Intro", "Nautilus", "Take Me To The Mardi Gras", "Jamaica"]
    for i, t in enumerate(titles, 1):
        make_mp3(library / f"Bob James - Two/{i:02d} {t}.mp3", artist="Bob James", album="Two", title=t)
        make_mp3(library / f"Bob_James-Two-1975/{i:02d}-{t}.mp3", artist="Bob James", album="Two", title=t)
    scanner.scan(job)
    with TestClient(app) as client:
        login(client)
        d = client.get("/api/album", params={"dir": "VA - Rap Classics"}).json()
        g = {t["filename"]: t["guess"] for t in d["tracks"]}
        assert g["02 - Jay-Z - Dead Presidents.mp3"] == {"track": "2", "artist": "Jay-Z", "title": "Dead Presidents"}

        # audio streaming with seeking
        path = d["tracks"][0]["path"]
        r = client.get("/api/audio", params={"path": path}, headers={"Range": "bytes=0-99"})
        assert r.status_code == 206 and len(r.content) == 100 and r.headers["content-type"] == "audio/mpeg"
        assert client.get("/api/audio", params={"path": "../x.mp3"}).status_code == 400

        # one track saved on its own
        r = client.post("/api/album/save", json={"dir": "VA - Rap Classics", "album": {},
                                                  "tracks": {path: {"artist": "Nas", "title": "The World Is Yours"}}})
        assert r.json()["files"] == 1

        # one duplicate group resolved synchronously
        groups = client.get("/api/duplicates").json()["items"]
        assert len(groups) == 1
        r = client.post("/api/duplicates/resolve", json={"sync": True, "groups": [
            {"keep": "Bob James - Two", "remove": ["Bob_James-Two-1975"]}]})
        assert r.status_code == 200 and r.json()["moved"] == 1
        assert client.get("/api/duplicates").json()["total"] == 0
