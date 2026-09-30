import pytest
from conftest import make_mp3

from app import artists, db, scanner

KNOWN = {artists._key(x) for x in ["Nas", "DJ Premier", "Mobb Deep", "Mad Professor", "Lee Perry", "IAM",
                                   "Earth, Wind & Fire"]}


@pytest.mark.parametrize("raw, proposal, status", [
    ("Nas & DJ Premier", "Nas; DJ Premier", "sure"),
    ("Mobb Deep, Nas & DJ Premier", "Mobb Deep; Nas; DJ Premier", "sure"),
    ("Mad Professor meets Lee Perry", "Mad Professor; Lee Perry", "sure"),
    ("IAM et Nas", "IAM; Nas", "sure"),
    ("Nas x DJ Premier", "Nas; DJ Premier", "sure"),
    ("Nas & DJ Premier feat. Q-Tip", "Nas; DJ Premier feat. Q-Tip", "sure"),
    ("Earth, Wind & Fire & Nas", "Earth, Wind & Fire; Nas", "sure"),
    # unknown neighbours stay together
    ("Nas & Eric B. & Rakim", "Nas; Eric B. & Rakim", "partial"),
    # a duo nobody knows: proposed, but not as safe
    ("Eric B. & Rakim", "Eric B.; Rakim", "unknown"),
])
def test_propose(raw, proposal, status):
    got = artists.propose(raw, KNOWN)
    assert got[0] == proposal and got[2] == status


@pytest.mark.parametrize("raw", ["Nas", "Nas; DJ Premier", "R&B Crew", "10,000 Maniacs", "Malcolm X", "Various Artists", ""])
def test_nothing_to_split(raw):
    assert artists.propose(raw, KNOWN) is None


def test_artist_folder_makes_one_artist(library, job):
    for i in range(2):
        make_mp3(library / f"Eric B. & Rakim/Paid in Full/0{i}.mp3", artist="Eric B. & Rakim", title=f"t{i}")
        make_mp3(library / f"Nas/Illmatic/0{i}.mp3", artist="Nas", albumartist="Nas", title=f"i{i}")
    scanner.scan(job)
    rows = {r["raw"]: r for r in artists.summary()["items"]}
    assert rows["Eric B. & Rakim"]["status"] == "single"
    assert rows["Eric B. & Rakim"]["proposal"] == "Eric B. & Rakim"


def test_apply_splits_and_remembers(library, job):
    for i in range(2):
        make_mp3(library / f"Nas/Illmatic/0{i}.mp3", artist="Nas", albumartist="Nas", title=f"i{i}")
        make_mp3(library / f"DJ Premier/Beats/0{i}.mp3", artist="DJ Premier", albumartist="DJ Premier", title=f"b{i}")
        make_mp3(library / f"Nas/Collab/0{i}.mp3", artist="Nas & DJ Premier", albumartist="Nas & DJ Premier", title=f"c{i}")
        make_mp3(library / f"Gang Starr/Step/0{i}.mp3", artist="Guru & Premier", title=f"s{i}")
    scanner.scan(job)

    rows = {r["raw"]: r for r in artists.summary()["items"]}
    assert rows["Nas & DJ Premier"]["proposal"] == "Nas; DJ Premier"
    assert rows["Nas & DJ Premier"]["artist"] == 2 and rows["Nas & DJ Premier"]["albumartist"] == 2
    assert rows["Guru & Premier"]["status"] == "unknown"

    artists.apply(job, [{"raw": "Nas & DJ Premier", "target": "Nas ;DJ Premier"},
                        {"raw": "Guru & Premier", "target": artists.KEEP}])
    with db.session() as c:
        got = {(r[0], r[1]) for r in c.execute("SELECT artist, albumartist FROM tracks WHERE dir='Nas/Collab'")}
        untouched = {r[0] for r in c.execute("SELECT artist FROM tracks WHERE dir='Gang Starr/Step'")}
        tag_artist = c.execute("SELECT tag_artist, issues FROM albums WHERE dir='Nas/Collab'").fetchone()
    assert got == {("Nas; DJ Premier", "Nas; DJ Premier")}
    assert untouched == {"Guru & Premier"}
    assert tag_artist["tag_artist"] == "Nas; DJ Premier"      # analysis refreshed
    assert "misplaced" not in tag_artist["issues"]            # 'Nas' folder still matches

    rows = {r["raw"]: r for r in artists.summary()["items"]}
    assert "Nas & DJ Premier" not in rows
    assert rows["Guru & Premier"]["proposal"] == artists.KEEP


def test_api(library, job):
    from fastapi.testclient import TestClient

    from app.main import app
    from conftest import login
    make_mp3(library / "Nas/Illmatic/01.mp3", artist="Nas", title="a")
    make_mp3(library / "IAM/Ombre/01.mp3", artist="IAM", title="b")
    make_mp3(library / "IAM/Collab/01.mp3", artist="IAM et Nas", title="c")
    scanner.scan(job)
    client = login(TestClient(app))
    items = client.get("/api/artists").json()["items"]
    assert [(r["raw"], r["proposal"]) for r in items] == [("IAM et Nas", "IAM; Nas")]
    assert client.get("/api/artists/detail", params={"raw": "IAM et Nas"}).json() == \
        [{"dir": "IAM/Collab", "artist": 1, "albumartist": 0}]
    r = client.post("/api/artists/apply", json={"items": [{"raw": "IAM et Nas", "target": artists.KEEP}]}).json()
    assert r["status"] == "done"
    assert artists.overrides() == {"IAM et Nas": artists.KEEP}
