import json

from conftest import make_mp3

from app import autotag, db, musicbrainz, scanner, tagger


def fake_release(rid, title, artist, rg, tracks):
    return {"id": rid, "title": title, "albumartist": artist, "albumartist_id": None, "date": "1959",
            "year": "1959", "release_group_id": rg, "compilation": False, "cover": False,
            "tracks": [{"disc": 1, "discs": 1, "position": i, "count": len(tracks), "title": t, "artist": artist,
                        "artist_id": None, "recording_id": f"{rid}-{i}", "track_id": None, "length": l}
                       for i, (t, l) in enumerate(tracks, 1)]}


KOB = [("So What", 20), ("Freddie Freeloader", 30), ("Blue in Green", 12), ("All Blues", 40), ("Flamenco Sketches", 25)]


def setup(library, job, monkeypatch, releases, toc_ids, search_ids=()):
    for i, (t, secs) in enumerate(KOB, 1):
        make_mp3(library / f"Miles Davis - Kind of Blue/0{i} - {t}.mp3", seconds=secs)
    scanner.scan(job)
    monkeypatch.setattr(musicbrainz, "by_durations", lambda d: [{"id": i} for i in toc_ids])
    monkeypatch.setattr(musicbrainz, "search", lambda a, b, n=None: [{"id": i, "tracks": 5, "score": 100} for i in search_ids])
    monkeypatch.setattr(musicbrainz, "release", lambda rid: releases[rid])
    monkeypatch.setattr(musicbrainz, "cover", lambda rid: None)


def test_confident_match_is_ready_and_applies(library, job, monkeypatch):
    rel = fake_release("kob", "Kind of Blue", "Miles Davis", "rg1", KOB)
    setup(library, job, monkeypatch, {"kob": rel}, ["kob"])
    autotag.analyse(job, ["Miles Davis - Kind of Blue"])
    with db.session() as c:
        row = c.execute("SELECT * FROM autotag").fetchone()
    assert row["status"] == "ok" and row["score"] >= 95, (row["score"], row["details"])

    autotag.apply(job, ["Miles Davis - Kind of Blue"], {"albumartist_mode": "fixed", "albumartist_value": "Miles Davis Quintet"})
    assert not job.errors
    t = tagger.read(str(library / "Miles Davis - Kind of Blue/03 - Blue in Green.mp3"))
    assert (t["title"], t["artist"], t["albumartist"], t["album"], t["track"]) == \
        ("Blue in Green", "Miles Davis", "Miles Davis Quintet", "Kind of Blue", "3/5")
    with db.session() as c:
        assert c.execute("SELECT status FROM autotag").fetchone()[0] == "applied"


def test_other_album_with_same_lengths_is_ambiguous(library, job, monkeypatch):
    kob = fake_release("kob", "Kind of Blue", "Miles Davis", "rg1", KOB)
    copy = fake_release("blue", "Blue", "Mostly Other People Do the Killing", "rg2", KOB)
    setup(library, job, monkeypatch, {"kob": kob, "blue": copy}, ["blue", "kob"])
    res = autotag.analyse_dir("Miles Davis - Kind of Blue")
    # names from the folder make Kind of Blue win clearly -> not ambiguous
    assert res["release_id"] == "kob" and res["status"] == "ok"
    # without any name hint, both are equally good -> ambiguous
    with db.session() as c:
        c.execute("UPDATE albums SET suggestion=? ", (json.dumps({}),))
    res = autotag.analyse_dir("Miles Davis - Kind of Blue")
    assert res["status"] == "ambiguous"


def test_lengths_only_on_a_short_release_is_capped(library, job, monkeypatch):
    rel = fake_release("ep", "EP", "X", "rg", KOB[:2])
    for i, (t, secs) in enumerate(KOB[:2], 1):
        make_mp3(library / f"xx/0{i}.mp3", seconds=secs)
    scanner.scan(job)
    score, details = autotag.evaluate(
        [dict(r) for r in db.connect().execute("SELECT * FROM tracks WHERE dir='xx' ORDER BY filename")], rel, {})
    assert details["titles"] is None and details["names"] is None
    assert score <= 76
