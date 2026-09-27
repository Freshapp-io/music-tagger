import pytest
from conftest import make_mp3

from app import db, genres, scanner, tagger


@pytest.mark.parametrize("raw, expected", [
    ("Hip-Hop/Rap", "Hip-Hop"), ("Rap & Hip-Hop", "Hip-Hop"), ("hiphop", "Hip-Hop"),
    ("Rap Fr", "Rap français"), ("Hip Hop Français", "Rap français"),
    ("Dance Hall", "Dancehall"), ("Ragga/Hip-Hop", "Dancehall"), ("Reggae - Dub", "Reggae"),
    ("Rockers", "Reggae"), ("Reggae & Dub", "Dub"), ("Ska/Rock Steady", "Ska"),
    ("R'n'B, Soul, Funk, Jazz", "Soul / R&B"), ("Soul Jazz", "Jazz"), ("Variété française", "Chanson française"),
    ("Other; Chanson française", "Chanson française"), ("Electronic,Trip Hop", "Électro"),
    ("Alternatif et Indé", "Rock"), ("reggae", "Reggae"), ("Classical", "Classique"),
    ("Other", genres.JUNK), ("www.mp3-ogg.ru", genres.JUNK), ("Unclassifiable", genres.JUNK),
    ("GetMetal.club", genres.JUNK), ("Siloé", None), ("J-Love : Return Of The Swarm Part 1", None),
])
def test_classify(raw, expected):
    assert genres.classify(raw)[0] == expected


def test_apply_maps_infers_and_remembers(library, job):
    for i in range(3):
        make_mp3(library / f"Nas/Illmatic/0{i}.mp3", artist="Nas", albumartist="Nas", title=f"t{i}")
    for i in range(3):
        make_mp3(library / f"Nas/Stillmatic/0{i}.mp3", artist="Nas", albumartist="Nas", title=f"s{i}")
    scanner.scan(job)
    # tag the first album with a messy but mappable genre, the other with a weird one
    for i in range(3):
        tagger.write(str(library / f"Nas/Illmatic/0{i}.mp3"), {"genre": "Hip Hop/Rap"})
        tagger.write(str(library / f"Nas/Stillmatic/0{i}.mp3"), {"genre": "Fucking Awesome"})
    scanner.scan(job)

    rows = {r["raw"]: r for r in genres.summary()["items"]}
    assert rows["Hip Hop/Rap"]["proposal"] == "Hip-Hop"
    assert rows["Fucking Awesome"]["proposal"] == genres.INFER
    assert rows["Fucking Awesome"]["status"] == "weird"

    genres.apply(job, [{"raw": "Hip Hop/Rap", "target": "Hip-Hop"}])
    genres.apply(job, [{"raw": "Fucking Awesome", "target": genres.INFER}])
    with db.session() as c:
        got = {r[0] for r in c.execute("SELECT genre FROM tracks")}
    assert got == {"Hip-Hop"}
    assert genres.overrides()["Fucking Awesome"] == genres.INFER


def test_french_artists_stay_french(library, job):
    for i in range(3):
        make_mp3(library / f"La Rumeur/A/0{i}.mp3", artist="La Rumeur", albumartist="La Rumeur", title=f"a{i}")
        make_mp3(library / f"La Rumeur/B/0{i}.mp3", artist="La Rumeur", albumartist="La Rumeur", title=f"b{i}")
    scanner.scan(job)
    for i in range(3):
        tagger.write(str(library / f"La Rumeur/A/0{i}.mp3"), {"genre": "Rap Français"})
        tagger.write(str(library / f"La Rumeur/B/0{i}.mp3"), {"genre": "Rap"})
    scanner.scan(job)
    genres.apply(job, [{"raw": "Rap", "target": "Hip-Hop"}])
    with db.session() as c:
        assert {r[0] for r in c.execute("SELECT genre FROM tracks WHERE dir='La Rumeur/B'")} == {"Rap français"}
