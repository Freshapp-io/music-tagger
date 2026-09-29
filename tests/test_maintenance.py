"""Unreadable files, albums in the wrong folder, album deletion, history cleanup."""
import json

from conftest import login, make_mp3

from app import analysis, db, fixes, scanner


def albums():
    with db.session() as c:
        return {r["dir"]: dict(r, issues=json.loads(r["issues"]), suggestion=json.loads(r["suggestion"]),
                               misplaced=json.loads(r["misplaced"]) if r["misplaced"] else None)
                for r in c.execute("SELECT * FROM albums")}


def album(library, folder, artist, album_name, n=3, **extra):
    for i in range(1, n + 1):
        make_mp3(library / folder / f"{i:02d}.mp3", artist=artist, albumartist=artist, album=album_name,
                 title=f"{album_name} {i}", track=str(i), **extra)


def untagged(library, folder, n=3):
    for i in range(1, n + 1):
        make_mp3(library / folder / f"{i:02d} - Song {i}.mp3")


def test_misplaced_album(library, job):
    album(library, "Nas/Illmatic", "Nas", "Illmatic")
    album(library, "Nas/Stillmatic", "Nas", "Stillmatic")
    album(library, "Nas/Reasonable Doubt", "Jay-Z", "Reasonable Doubt")        # tags: another artist
    album(library, "Jay-Z/The Blueprint", "Jay-Z", "The Blueprint")
    album(library, "Mobb Deep - Illmatic Remix", "Nas", "Illmatic Remix")      # wrong 'Artist - Album' name
    album(library, "Mobb Deep/The Infamous/CD1", "Mobb Deep", "The Infamous")  # disc folder: fine
    album(library, "Nas/Nas & Damian Marley - Distant Relatives", "Nas & Damian Marley", "Distant Relatives")
    album(library, "Rap/Big L - Lifestylez", "Big L", "Lifestylez")            # genre folder: fine
    album(library, "Nas/Illmatic - Deluxe Edition", "Nas", "Illmatic")        # left part is the album
    for i, a in enumerate(["Nas", "Jay-Z", "Big L"], 1):                        # compilation: never misplaced
        make_mp3(library / f"Nas/Best Of Rap/{i:02d}.mp3", artist=a, albumartist="Various Artists",
                 album="Best Of Rap", title=f"t{i}", compilation="1")
    # folder vs folder, no tags at all
    untagged(library, "Nas/Jay-Z - The Blueprint 2")         # Jay-Z is a known artist
    untagged(library, "Nas/1999 - I Am")                     # a year, not an artist
    untagged(library, "Nas/Live - Paris")
    untagged(library, "50 Cent/50 Cent - Get Rich")
    untagged(library, "Kery James/Kery James - A.C.E.")
    untagged(library, "Kery James/Kery James - Reel")
    untagged(library, "Kery James/Ideal J - O'riginal")      # unknown artist, but the folder's naming says so
    scanner.scan(job)
    a = albums()
    flagged = {d for d, x in a.items() if "misplaced" in x["issues"]}
    assert flagged == {"Nas/Reasonable Doubt", "Mobb Deep - Illmatic Remix", "Nas/Jay-Z - The Blueprint 2",
                       "Kery James/Ideal J - O'riginal"}
    assert a["Nas/Reasonable Doubt"]["misplaced"] == {
        "folder_artist": "Nas", "folder": "Nas", "name_artist": None, "tag_artist": "Jay-Z",
        "target": "Jay-Z/Reasonable Doubt", "kinds": ["tags"]}
    assert a["Mobb Deep - Illmatic Remix"]["misplaced"] == {
        "folder_artist": None, "folder": None, "name_artist": "Mobb Deep", "tag_artist": "Nas",
        "target": "Nas/Mobb Deep - Illmatic Remix", "kinds": ["tags"]}
    assert a["Nas/Jay-Z - The Blueprint 2"]["misplaced"] == {
        "folder_artist": "Nas", "folder": "Nas", "name_artist": "Jay-Z", "tag_artist": None,
        "target": "Jay-Z/Jay-Z - The Blueprint 2", "kinds": ["folders"]}
    assert {"misplaced_folders"} <= set(a["Nas/Jay-Z - The Blueprint 2"]["issues"])
    assert "misplaced_folders" not in a["Nas/Reasonable Doubt"]["issues"]
    m = a["Kery James/Ideal J - O'riginal"]["misplaced"]
    assert (m["folder_artist"], m["name_artist"], m["target"]) == ("Kery James", "Ideal J", None)

    # fixing the tags clears the flag
    fixes.save_album("Nas/Reasonable Doubt", {"albumartist": "Nas"}, {
        t: {"artist": "Nas"} for t in (f"Nas/Reasonable Doubt/0{i}.mp3" for i in (1, 2, 3))})
    assert "misplaced" not in albums()["Nas/Reasonable Doubt"]["issues"]


def test_misplaced_real_cases(library, job):
    """Cases reported on a real library: none of them is a folder problem."""
    album(library, "Alton Ellis/Alton and Hortense Ellis - At Studio One", "Alton & Hortense Ellis", "At Studio One")
    album(library, "Alton Ellis/Alton Ellis - Sunday Coming", "Alton Ellis", "Sunday Coming")
    album(library, "Abcdr_Du_Son/Abcdr_du_Son_-_From_Scratch_-_Scenario_(2012)", "Abcdrduson.com", "From Scratch")
    album(library, "Al Campbell/Al Campbell - Roots & Culture", "New Artist", "New Title")
    album(library, "Adrian Younge/Adrian Younge - There Is Only Now", "Souls of Mischief", "There Is Only Now")
    album(library, "Adrian Younge/Adrian Younge - Something About April", "Adrian Younge", "Something About April")
    scanner.scan(job)
    a = albums()
    flagged = {d: x["misplaced"] for d, x in a.items() if "misplaced" in x["issues"]}
    # only the tags disagree, and only for Adrian Younge: shown as 'folder ≠ tags' with both folder names
    assert list(flagged) == ["Adrian Younge/Adrian Younge - There Is Only Now"]
    m = flagged["Adrian Younge/Adrian Younge - There Is Only Now"]
    assert (m["folder_artist"], m["name_artist"], m["tag_artist"], m["kinds"]) == \
        ("Adrian Younge", "Adrian Younge", "Souls of Mischief", ["tags"])

    from fastapi.testclient import TestClient

    from app.main import app
    with TestClient(app) as client:
        login(client)
        only_folders = client.get("/api/albums", params={"issue": "misplaced", "sub": "misplaced_folders"}).json()
        assert only_folders["total"] == 0
        assert client.get("/api/albums", params={"issue": "misplaced", "sub": "misplaced_tags"}).json()["total"] == 1


def test_scan_errors_are_kept_and_retried(library, job, monkeypatch):
    album(library, "Good", "A", "Good")
    make_mp3(library / "Bad/01.mp3", artist="B", title="ok")
    (library / "Bad/02.mp3").write_bytes(b"not an mp3 at all" * 10)     # read, but rejected by mutagen
    scanner.scan(job)
    real_read = scanner.tagger.read

    def flaky(path):
        if path.endswith("Bad/01.mp3"):
            raise OSError(5, "Input/output error")
        return real_read(path)
    monkeypatch.setattr(scanner.tagger, "read", flaky)
    monkeypatch.setattr(scanner.time, "sleep", lambda s: None)
    scanner.scan(job, full=True)

    from fastapi.testclient import TestClient

    from app.main import app
    with TestClient(app) as client:
        login(client)
        assert client.get("/api/status").json()["counts"]["errors"] == 1
        items = client.get("/api/errors").json()["items"]
        assert [d["dir"] for d in items] == ["Bad"]
        files = {f["filename"]: f["error"] for f in items[0]["files"]}
        assert set(files) == {"01.mp3", "02.mp3"} and "Input/output" in files["01.mp3"]

        # the share is back: retrying clears the I/O error, the corrupt file stays
        monkeypatch.setattr(scanner.tagger, "read", real_read)
        assert client.post("/api/errors/retry", json={"dirs": ["Bad"]}).json()["remaining"] == 0
        items = client.get("/api/errors").json()["items"]
        assert [f["filename"] for f in items[0]["files"]] == ["02.mp3"]


def test_delete_album_and_undo(library, job):
    album(library, "Artist/Album", "Artist", "Album")
    album(library, "Artist/Other", "Artist", "Other")
    (library / "Artist/Album/cover.jpg").write_bytes(b"jpg")
    scanner.scan(job)
    from fastapi.testclient import TestClient

    from app.main import app
    with TestClient(app) as client:
        login(client)
        r = client.post("/api/album/delete", json={"dirs": ["Artist/Album"]}).json()
        assert r["deleted"] == 1 and not r["errors"]
        assert not (library / "Artist/Album").exists() and (library / "Artist/Other").exists()
        assert "Artist/Album" not in albums()
        assert client.get("/api/album", params={"dir": "Artist/Album"}).status_code == 404
        h = client.get("/api/history").json()["items"][0]
        assert h["label"] == "Album supprimé : Artist/Album" and h["n_trash"] == 1

    fixes.undo(r["batch"])
    assert (library / "Artist/Album/cover.jpg").exists()
    assert albums()["Artist/Album"]["n_tracks"] == 3


def test_history_delete_and_clear(library, job):
    make_mp3(library / "X/01.mp3", artist="A", title="One")
    scanner.scan(job)
    b1 = fixes.save_album("X", {"album": "First"}, {})["batch"]
    b2 = fixes.save_album("X", {"album": "Second"}, {})["batch"]
    b3 = fixes.save_album("X", {"album": "Third"}, {})["batch"]
    from fastapi.testclient import TestClient

    from app.main import app
    with TestClient(app) as client:
        login(client)
        h = client.get("/api/history").json()
        assert [x["batch"] for x in h["items"]] == [b3, b2, b1]
        assert h["stats"]["batches"] == 3 and h["stats"]["bytes"] > 0 and h["stats"]["db_size"] > 0

        r = client.post("/api/history/delete", json={"batches": [b2]}).json()
        assert r["deleted"] == 1 and r["batches"] == 2
        assert [x["batch"] for x in client.get("/api/history").json()["items"]] == [b3, b1]
        # the files are not touched
        with db.session() as c:
            assert c.execute("SELECT album FROM tracks").fetchone()[0] == "Third"

        assert client.post("/api/history/delete", json={}).status_code == 400
        r = client.post("/api/history/delete", json={"all": True}).json()
        assert r["batches"] == 0 and r["bytes"] == 0
        assert client.get("/api/history").json()["items"] == []


def test_migration_adds_new_columns(library):
    with db.session() as c:
        c.execute("DROP TABLE albums")
        old = [col for col in analysis.ALBUM_COLUMNS if col not in ("tag_artist", "misplaced")]
        c.execute(f"CREATE TABLE albums ({', '.join(old)})")
    db.init()
    with db.session() as c:
        cols = {r["name"] for r in c.execute("PRAGMA table_info(albums)")}
    assert {"tag_artist", "misplaced"} <= cols
    analysis.compute_misplaced()        # works on the migrated table


def test_deliberate_various_artists_is_kept(library, job):
    """Setting 'Various Artists' on a folder of various artists must not be
    suggested back to its most frequent artist (the editor pre-fills the suggestion)."""
    for i, a in enumerate(["DJ Premier", "Funkmaster Flex", "DJ Clue", "DJ Premier"], 1):
        make_mp3(library / f"Mixtapes/{i:02d}.mp3", artist=a, albumartist=a, album=f"Mix {i}", title=f"Mix {i}")
    scanner.scan(job)
    assert albums()["Mixtapes"]["suggestion"]["albumartist"] == "DJ Premier"
    fixes.save_album("Mixtapes", {"albumartist": "Various Artists", "compilation": "1"}, {})
    a = albums()["Mixtapes"]
    assert (a["suggestion"]["albumartist"], a["suggestion"]["compilation"]) == ("Various Artists", 1)
    assert "various" not in a["issues"]


def test_loose_folder(library, job):
    from fastapi.testclient import TestClient

    from app.main import app
    make_mp3(library / "Mixtapes/DJ Premier - Crooklyn Cuts.mp3", artist="DJ Premier feat. Guru", title="Crooklyn Cuts")
    make_mp3(library / "Mixtapes/Funkmaster Flex - 60 Minutes.mp3", artist="Funkmaster Flex", albumartist="Various Artists",
             album="60 Minutes Of Funk", title="60 Minutes Of Funk", compilation="1")
    make_mp3(library / "Mixtapes/DJ Clue - Desert Storm.mp3")                      # no tags at all
    scanner.scan(job)
    assert "inconsistent" in albums()["Mixtapes"]["issues"]
    with TestClient(app) as client:
        login(client)
        assert client.post("/api/folder/mode", json={"dir": "Mixtapes", "loose": True}).status_code == 200
        a = client.get("/api/album", params={"dir": "Mixtapes"}).json()["album"]
        assert a["mode"] == "loose" and a["suggestion"]["loose"]
        assert {"inconsistent", "loose_albumartist", "untagged"} <= set(a["issues"]) and "various" not in a["issues"]
        ch = client.get("/api/album/preview", params={"dir": "Mixtapes", "fields": "loose"}).json()
        assert ch["Mixtapes/DJ Premier - Crooklyn Cuts.mp3"] == {
            "albumartist": "DJ Premier", "album": "Crooklyn Cuts", "track": "1/1"}
        assert ch["Mixtapes/Funkmaster Flex - 60 Minutes.mp3"] == {
            "albumartist": "Funkmaster Flex", "track": "1/1", "compilation": None}
        assert ch["Mixtapes/DJ Clue - Desert Storm.mp3"] == {
            "artist": "DJ Clue", "title": "Desert Storm", "albumartist": "DJ Clue", "album": "Desert Storm", "track": "1/1"}
    # bulk 'apply the suggestion' does the same for loose folders
    fixes.apply_suggestions(job, ["Mixtapes"], ("albumartist", "album", "year", "compilation", "tracks"))
    a = albums()["Mixtapes"]
    assert a["issues"] == [] and a["dup_group"] is None and a["misplaced"] is None
    with TestClient(app) as client:
        login(client)
        client.post("/api/folder/mode", json={"dir": "Mixtapes", "loose": False})
    assert "inconsistent" in albums()["Mixtapes"]["issues"]      # one folder, three albums again


def test_looks_loose():
    from app.analysis import looks_loose

    def t(artist, album, minutes):
        return {"artist": artist, "album": album, "duration": minutes * 60}
    assert looks_loose([t("A", "x", 60), t("B", "y", 55), t("C", "z", 62)])
    assert not looks_loose([t("A", "x", 4), t("B", "y", 5), t("C", "z", 3)])           # ordinary compilation
    assert not looks_loose([t("A", "x", 60), t("A", "x", 58), t("A", "x", 61)])        # one long album
