"""Unreadable files, albums in the wrong folder, album deletion, history cleanup."""
import json

from conftest import login, make_mp3

from app import analysis, db, fixes, scanner


def albums():
    with db.session() as c:
        return {r["dir"]: dict(r, issues=json.loads(r["issues"]),
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
        "target": "Jay-Z/Reasonable Doubt"}
    assert a["Mobb Deep - Illmatic Remix"]["misplaced"] == {
        "folder_artist": None, "folder": None, "name_artist": "Mobb Deep", "tag_artist": "Nas",
        "target": "Nas/Mobb Deep - Illmatic Remix"}
    assert a["Nas/Jay-Z - The Blueprint 2"]["misplaced"] == {
        "folder_artist": "Nas", "folder": "Nas", "name_artist": "Jay-Z", "tag_artist": None,
        "target": "Jay-Z/Jay-Z - The Blueprint 2"}
    m = a["Kery James/Ideal J - O'riginal"]["misplaced"]
    assert (m["folder_artist"], m["name_artist"], m["target"]) == ("Kery James", "Ideal J", None)

    # fixing the tags clears the flag
    fixes.save_album("Nas/Reasonable Doubt", {"albumartist": "Nas"}, {
        t: {"artist": "Nas"} for t in (f"Nas/Reasonable Doubt/0{i}.mp3" for i in (1, 2, 3))})
    assert "misplaced" not in albums()["Nas/Reasonable Doubt"]["issues"]


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
