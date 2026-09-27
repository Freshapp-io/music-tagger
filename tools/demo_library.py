"""Build a small demo library (silent, tiny MP3 files with realistic tags and
durations) that shows every kind of problem the app detects. Used for the
README screenshots.

    python tools/demo_library.py /path/to/empty/folder

Needs mutagen; Pillow is optional (generated cover art).
"""
import colorsys
import io
import struct
import sys
from pathlib import Path

from mutagen.id3 import APIC, ID3, TALB, TCMP, TCON, TDRC, TIT2, TPE1, TPE2, TRCK

HEADER = {128: b"\xff\xfb\x90\x64", 192: b"\xff\xfb\xb0\x64", 320: b"\xff\xfb\xe0\x64"}
FRAME_LEN = {128: 417, 192: 626, 320: 1044}


def silent_mp3(seconds, kbps=128, vbr=False):
    """A few silent frames plus a Xing/Info header announcing the real length,
    so players and tag readers show a realistic duration for a tiny file."""
    frames = int(seconds * 44100 / 1152)
    first = HEADER[kbps] + b"\x00" * 32 + (b"Xing" if vbr else b"Info")
    first += struct.pack(">III", 3, frames, frames * FRAME_LEN[kbps])
    first = first.ljust(FRAME_LEN[kbps], b"\x00")
    return first + (HEADER[kbps] + b"\x00" * (FRAME_LEN[kbps] - 4)) * 8


def cover(seed, text):
    try:
        from PIL import Image, ImageDraw
    except ImportError:
        return None
    h = (hash(seed) % 360) / 360
    img = Image.new("RGB", (300, 300))
    d = ImageDraw.Draw(img)
    for y in range(300):
        r, g, b = colorsys.hsv_to_rgb((h + y / 1200) % 1, 0.55, 0.55 + y / 900)
        d.line([(0, y), (300, y)], fill=(int(r * 255), int(g * 255), int(b * 255)))
    d.ellipse((70, 70, 230, 230), outline=(255, 255, 255), width=6)
    d.text((20, 262), text[:34], fill=(255, 255, 255))
    buf = io.BytesIO()
    img.save(buf, "JPEG", quality=85)
    return buf.getvalue()


def album(root, folder, tracks, *, artist=None, albumartist=None, album=None, year=None, genre=None,
          kbps=192, vbr=False, compilation=False, with_cover=True, tagged=True, filename="{n:02d} - {title}.mp3"):
    art = cover(folder, album or folder) if with_cover else None
    for n, t in enumerate(tracks, 1):
        title, secs = t[0], t[1]
        track_artist = t[2] if len(t) > 2 else artist
        path = root / folder / filename.format(n=n, title=title, artist=track_artist or "")
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(silent_mp3(secs, kbps, vbr))
        if not tagged:
            continue
        tags = ID3()
        for cls, value in ((TPE1, track_artist), (TPE2, albumartist), (TALB, album), (TIT2, title),
                           (TRCK, str(n)), (TDRC, year if not callable(year) else year(n)),
                           (TCON, genre if not callable(genre) else genre(n))):
            if value:
                tags.add(cls(encoding=3, text=[value]))
        if compilation:
            tags.add(TCMP(encoding=3, text=["1"]))
        if art:
            tags.add(APIC(encoding=3, mime="image/jpeg", type=3, desc="Cover", data=art))
        tags.save(path)


def build(root):
    root = Path(root)
    m = lambda s: int(s.split(":")[0]) * 60 + int(s.split(":")[1])

    # Clean album
    album(root, "Nina Simone/Nina Simone - Pastel Blues (1965)", [
        ("Be My Husband", m("2:58")), ("Nobody Knows You When You're Down and Out", m("3:59")),
        ("End of the Line", m("2:41")), ("Trouble in Mind", m("2:43")), ("Tell Me More and More", m("2:30")),
        ("Chilly Winds Don't Blow", m("3:18")), ("Ain't No Use", m("3:23")), ("Sinnerman", m("10:19")),
    ], artist="Nina Simone", albumartist="Nina Simone", album="Pastel Blues", year="1965", genre="Jazz", kbps=320)

    # Album wrongly tagged "Various Artists"
    album(root, "Bob Marley & The Wailers/Catch A Fire (1973)", [
        ("Concrete Jungle", m("4:13")), ("Slave Driver", m("2:54")), ("400 Years", m("2:45")),
        ("Stop That Train", m("3:55")), ("Baby We've Got a Date", m("3:57")), ("Stir It Up", m("5:32")),
        ("Kinky Reggae", m("3:37")), ("No More Trouble", m("3:57")), ("Midnight Ravers", m("5:08")),
    ], artist="Bob Marley & The Wailers", albumartist="Various Artists", album="Catch a Fire", year="1973",
        genre="General Reggae", kbps=320)

    # No tags at all: everything comes from folder and file names
    album(root, "Youssoupha/Youssoupha - Noir Desir (2012)", [
        ("Intro", m("1:32")), ("Espérance de vie", m("3:51")), ("Menace de mort", m("4:02")),
        ("Les disques de mon père", m("3:40")), ("Dreamin", m("4:14")), ("Noir D", m("3:59")),
    ], tagged=False, with_cover=False)

    # Untagged rip of a real CD: found on MusicBrainz from its track lengths alone
    album(root, "Portishead/Portishead - Dummy (1994)", [
        (t, secs + (1 if i % 2 else -1)) for i, (t, secs) in enumerate([
            ("Mysterons", 306), ("Sour Times", 254), ("Strangers", 238), ("It Could Be Sweet", 260),
            ("Wandering Star", 294), ("It's a Fire", 229), ("Numb", 238), ("Roads", 305), ("Pedestal", 221),
            ("Biscuit", 304), ("Glory Box", 306)])
    ], tagged=False, with_cover=False)

    # Navidrome would split it: no album artist, 'feat.' artists, two years
    album(root, "Massive Attack/Massive Attack - Mezzanine", [
        ("Angel", m("6:18"), "Massive Attack feat. Horace Andy"), ("Risingson", m("4:58"), "Massive Attack"),
        ("Teardrop", m("5:29"), "Massive Attack feat. Elizabeth Fraser"), ("Inertia Creeps", m("5:56"), "Massive Attack"),
        ("Exchange", m("4:11"), "Massive Attack"), ("Dissolved Girl", m("6:06"), "Massive Attack feat. Sarah Jay"),
        ("Man Next Door", m("5:55"), "Massive Attack feat. Horace Andy"), ("Black Milk", m("6:20"), "Massive Attack feat. Elizabeth Fraser"),
    ], album="Mezzanine", year=lambda n: "1998" if n < 6 else "2006", genre="Electronic,Trip Hop", kbps=192, vbr=True)

    # A genuine compilation that still needs the compilation flag
    album(root, "VA - Soul Classics Vol. 1", [
        ("Respect", m("2:28"), "Aretha Franklin"), ("Stand by Me", m("2:57"), "Ben E. King"),
        ("I Got You (I Feel Good)", m("2:47"), "James Brown"), ("A Change Is Gonna Come", m("3:11"), "Sam Cooke"),
        ("My Girl", m("2:55"), "The Temptations"), ("Let's Stay Together", m("3:18"), "Al Green"),
    ], albumartist="Various Artists", album="Soul Classics Vol. 1", year="2019", genre="Soul and R&B", kbps=192,
        filename="{n:02d} - {artist} - {title}.mp3")

    # Same album twice: 320 kbps copy vs 128 kbps incomplete copy
    kob = [("So What", m("9:22")), ("Freddie Freeloader", m("9:46")), ("Blue in Green", m("5:37")),
           ("All Blues", m("11:33")), ("Flamenco Sketches", m("9:26"))]
    album(root, "Miles Davis/Miles Davis - Kind of Blue (1959)", kob, artist="Miles Davis", albumartist="Miles Davis",
          album="Kind of Blue", year="1959", genre="Jazz", kbps=320)
    album(root, "Miles_Davis-Kind_Of_Blue-1959", kob[:4], artist="Miles Davis", album="Kind Of Blue",
          year="1959", genre="jazz", kbps=128, with_cover=False, filename="{n:02d}-miles_davis-{title}.mp3")

    album(root, "Daft Punk/Daft Punk - Discovery (2001)", [
        ("One More Time", m("5:20")), ("Aerodynamic", m("3:27")), ("Digital Love", m("4:58")),
        ("Harder, Better, Faster, Stronger", m("3:44")), ("Crescendolls", m("3:31")), ("Nightvision", m("1:44")),
    ], artist="Daft Punk", albumartist="Daft Punk", album="Discovery", year="2001", genre="Electro", kbps=320)
    album(root, "Daft Punk/Discovery", [
        ("One More Time", m("5:20")), ("Aerodynamic", m("3:27")), ("Digital Love", m("4:58")),
        ("Harder, Better, Faster, Stronger", m("3:44")), ("Crescendolls", m("3:31")), ("Nightvision", m("1:44")),
    ], artist="Daft Punk", album="Discovery", year="2001", genre="Other", kbps=192, vbr=True)

    # Messy genres
    album(root, "IAM/IAM - L'École du micro d'argent (1997)", [
        ("L'École du micro d'argent", m("5:22")), ("Dangereux", m("4:22")), ("Nés sous la même étoile", m("4:33")),
        ("La Saga", m("5:49")), ("Petit frère", m("4:57")), ("Demain c'est loin", m("8:59")),
    ], artist="IAM", albumartist="IAM", album="L'École du micro d'argent", year="1997", genre="Rap Fr", kbps=320)
    album(root, "A Tribe Called Quest/The Low End Theory", [
        ("Excursions", m("3:54")), ("Buggin' Out", m("3:38")), ("Check the Rhime", m("3:36")),
        ("Jazz (We've Got)", m("4:10")), ("Scenario", m("4:10")),
    ], artist="A Tribe Called Quest", albumartist="A Tribe Called Quest", album="The Low End Theory",
        year="1991", genre="Hip-Hop/Rap", kbps=320)
    album(root, "Burning Spear/Marcus Garvey (1975)", [
        ("Marcus Garvey", m("3:24")), ("Slavery Days", m("3:31")), ("The Invasion", m("3:14")),
        ("Live Good", m("3:08")), ("Old Marcus Garvey", m("3:11")),
    ], artist="Burning Spear", albumartist="Burning Spear", album="Marcus Garvey", year="1975",
        genre="Unknown", kbps=192)
    album(root, "Lee Scratch Perry/Super Ape", [
        ("Zion's Blood", m("3:43")), ("Croaking Lizard", m("4:30")), ("Black Vest", m("3:44")),
        ("Underground", m("4:22")), ("Curly Dub", m("4:28")),
    ], artist="The Upsetters", albumartist="The Upsetters", album="Super Ape", year="1976", genre="Reggae - Dub", kbps=192)


if __name__ == "__main__":
    if len(sys.argv) != 2:
        sys.exit(__doc__)
    build(sys.argv[1])
    print("demo library written to", sys.argv[1])
