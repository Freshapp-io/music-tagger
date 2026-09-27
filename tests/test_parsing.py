import pytest

from app.parsing import is_various, norm_album, parse_dir, parse_filename, primary_artist


@pytest.mark.parametrize("path, expected", [
    ("Youssoupha/Youssoupha-A Chaque Frere 2007", {"artist": "Youssoupha", "album": "A Chaque Frere", "year": "2007"}),
    ("113-113 Degres 2005 CDRip 320kbps", {"artist": "113", "album": "113 Degres", "year": "2005"}),
    ("Black Moon/Black_Moon-Alter_The_Chemistry-2006-FTD", {"artist": "Black Moon", "album": "Alter The Chemistry", "year": "2006"}),
    ("La Coka Nostra - A Brand You Can Trust (2009)", {"artist": "La Coka Nostra", "album": "A Brand You Can Trust", "year": "2009"}),
    ("The Doors/The Doors - STUDIO DISCOGRAPHY/The Doors - 1967 - Strange Days", {"artist": "The Doors", "album": "Strange Days", "year": "1967"}),
    ("Aaliyah/One In A Million", {"artist": "Aaliyah", "album": "One In A Million"}),
    ("Pink Floyd/The Wall/CD2", {"artist": "Pink Floyd", "album": "The Wall", "disc": "2"}),
])
def test_parse_dir(path, expected):
    got = parse_dir(path)
    for k, v in expected.items():
        assert got.get(k) == v, (path, got)


@pytest.mark.parametrize("name, hint, expected", [
    ("01 - A chaque frere.mp3", None, {"track": "1", "title": "A chaque frere"}),
    ("06 - Les meilleurs ennemis (avec Diam's).mp3", None, {"track": "6", "title": "Les meilleurs ennemis (avec Diam's)"}),
    ("03. Intro.mp3", None, {"track": "3", "title": "Intro"}),
    ("01-black_moon-who_got_da_props.mp3", "Black Moon", {"track": "1", "artist": "Black Moon", "title": "Who Got Da Props"}),
    ("Black Moon - 04 - How Many MC's.mp3", None, {"track": "4", "artist": "Black Moon", "title": "How Many MC's"}),
    ("101-artist-song.mp3", "artist", {"disc": "1", "track": "1", "title": "Song"}),
    ("Nas - The World Is Yours.mp3", None, {"artist": "Nas", "title": "The World Is Yours"}),
])
def test_parse_filename(name, hint, expected):
    got = parse_filename(name, hint)
    for k, v in expected.items():
        assert got.get(k) == v, (name, got)


def test_primary_artist():
    assert primary_artist("Black Moon feat. Q‐Tip") == "Black Moon"
    assert primary_artist("Swift Guad Feat Nekfeu") == "Swift Guad"
    assert primary_artist("Gentleman & Ky-Mani Marley") == "Gentleman & Ky-Mani Marley"
    assert primary_artist("The Roots (feat. Common)") == "The Roots"


def test_various():
    assert is_various("Various Artists") and is_various("VA") and is_various("Artistes Divers")
    assert not is_various("Vanessa Paradis")


def test_norm_album():
    assert norm_album("Come Around (Bootleg)") == norm_album("Come Around (2007)") == "come around"
    assert norm_album("113 Degres 2005 CDRip 320kbps") == norm_album("113 Degrés")


def test_brackets_and_mojibake():
    from app.parsing import disc_of, fix_mojibake
    assert parse_dir("OFWGKTA/OFWGKTA-Live at the Sydney Opera House (02-06-2011)")["album"] == \
        "Live at the Sydney Opera House (02-06-2011)"
    assert fix_mojibake("Rockin\x92 Squat") == "Rockin’ Squat"
    assert fix_mojibake("Café") == "Café"
    assert disc_of("Otis Redding - Live On The Sunset Strip CD2 (2010)") == 2
    assert disc_of("Shalamar - Friends/Disc 1") == 1 and disc_of("Two") is None
