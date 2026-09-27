"""Heuristics on names: normalisation, 'feat.' stripping, and guessing
artist / album / year / track / title from folder and file names."""
import re
import unicodedata
from pathlib import PurePosixPath

VARIOUS_NAMES = {
    "various artists", "various artist", "various", "va", "v a", "v.a.", "divers",
    "artistes divers", "artistes varies", "compilation", "multi artistes",
    "verschiedene interpreten", "varios artistas", "artisti vari",
}

# Release-scene and ripping noise found in folder names.
NOISE = re.compile(
    r"\b(cdrip|cd rip|webrip|web|vinyl ?rip|vinyl|retail|promo|bootleg|advance|"
    r"\d{2,3} ?kbps|\d{3}k|v0|v2|vbr|cbr|mp3|flac|wav|eac|lame|fr|us|uk|"
    r"cdm|cds|cdep|ep|lp|mixtape|full album|album|remastered|remaster|reissue|"
    r"(?:dis[ck]|cd) ?\d+)\b",
    re.I,
)
YEAR = re.compile(r"(?<!\d)(19[4-9]\d|20[0-3]\d)(?!\d)")
DISC_DIR = re.compile(r"^(?:cd|dis[ck]|disque|vol(?:ume)?\.?)\s*[-_ ]?\s*(\d{1,2})\b", re.I)
FEAT = re.compile(r"\s*[(\[]?\s*\b(?:feat\.?|ft\.?|featuring)\s+.*$", re.I)


def fold(s):
    """Lowercase, strip accents, collapse punctuation — for comparisons only."""
    if not s:
        return ""
    s = unicodedata.normalize("NFKD", str(s))
    s = "".join(ch for ch in s if not unicodedata.combining(ch))
    s = s.lower().replace("&", " and ").replace("_", " ")
    s = re.sub(r"[‐‑‒–—―]", "-", s)
    s = re.sub(r"[^\w]+", " ", s)
    return re.sub(r"\s+", " ", s).strip()


GENERIC_TITLE = re.compile(
    r"^(?:(?:track|piste|titre|pista|song|audio ?track)\s*\d*|\d+|untitled|sans titre|intro|outro|"
    r"interlude|skit|bonus|bonus track|hidden track|instrumental)$")
DISC_ANY = re.compile(r"\b(?:cd|dis[ck]|disque)\s*[-_ ]?\s*(\d{1,2})\b", re.I)


def fix_mojibake(s):
    """'Rockin\x92 Squat' (cp1252 bytes read as latin-1) -> 'Rockin’ Squat'."""
    if s and any("\x80" <= ch <= "\x9f" for ch in s):
        try:
            return s.encode("latin-1").decode("cp1252")
        except (UnicodeEncodeError, UnicodeDecodeError):
            pass
    return s


def disc_of(rel_dir):
    """Disc number found in the last folder name ('... CD2 (2010)', 'Disc 1')."""
    m = DISC_ANY.search(PurePosixPath(rel_dir).name if rel_dir else "")
    return int(m.group(1)) if m else None


def is_various(s):
    return bool(s) and (fold(s) in VARIOUS_NAMES or fold(s).startswith("various artist"))


def primary_artist(s):
    """'Black Moon feat. Q-Tip' -> 'Black Moon'."""
    if not s:
        return s
    p = FEAT.sub("", s).strip(" -,")
    return p or s.strip()


def norm_album(s):
    """Key used to detect the same album under different spellings."""
    if not s:
        return ""
    s = re.sub(r"[\[(][^\])]*[\])]", " ", s)       # anything in brackets
    s = s.replace("_", " ")
    s = YEAR.sub(" ", s)
    s = NOISE.sub(" ", s)
    return fold(s)


def norm_title(s):
    if not s:
        return ""
    s = FEAT.sub("", s)
    s = re.sub(r"[\[(][^\])]*[\])]", " ", s)
    return fold(s)


def _smart_case(s):
    s = re.sub(r"\s+", " ", s).strip(" -._")
    if s and s == s.lower():
        s = " ".join(w[:1].upper() + w[1:] for w in s.split(" "))
    return s


def clean_album(s):
    """Folder-ish album name -> display album name."""
    s = s.replace("_", " ")
    s = re.sub(r"[\[(]\s*(?:19|20)\d\d\s*[\])]", " ", s)
    s = re.sub(r"[\[(][^\])]*(?:kbps|rip|web|flac|mp3|v0|320|eac|by )[^\])]*[\])]", " ", s, flags=re.I)
    s = re.sub(r"\s(?:cdrip|cd rip|webrip|web|\d{3} ?kbps|v0|320|mp3|eac)\b.*$", " ", s, flags=re.I)
    s = re.sub(r"\s(?:19|20)\d\d\s*$", " ", s)
    return _smart_case(s)


def parse_dir(rel_dir):
    """Guess {artist, album, year, disc} from a relative folder path."""
    parts = [p for p in PurePosixPath(rel_dir).parts if p not in ("", ".")]
    if not parts:
        return {}
    out = {}
    name = parts[-1]
    parents = parts[:-1]
    m = DISC_DIR.match(name)
    if m and parents:
        out["disc"] = str(int(m.group(1)))
        name = parents[-1]
        parents = parents[:-1]

    ym = YEAR.findall(name)
    if ym:
        out["year"] = ym[-1]

    scene = "_" in name and " " not in name.strip()
    work = name.replace("_", " ") if scene else name
    # dashes inside brackets ('(02-06-2011)') are not separators
    work = re.sub(r"[\[(][^\])]*[\])]", lambda m: m.group(0).replace("-", "\x00"), work)
    if scene:
        chunks = [c.strip() for c in work.split("-") if c.strip()]
    elif " - " in work:
        chunks = [c.strip() for c in work.split(" - ") if c.strip()]
    elif re.match(r"^[^-]+-[^-]+", work) and work.count("-") <= 3:
        chunks = [c.strip() for c in work.split("-") if c.strip()]
    else:
        chunks = [work.strip()]

    chunks = [c.replace("\x00", "-") for c in chunks if not YEAR.fullmatch(c)]
    if len(chunks) >= 2:
        out["artist"] = _smart_case(chunks[0])
        out["album"] = clean_album(chunks[1])
    elif chunks:
        out["album"] = clean_album(chunks[0])

    # Artist/Album layout: the parent folder is the artist.
    if parents and not DISC_DIR.match(parents[-1]):
        parent = parents[-1]
        if " - " not in parent and not YEAR.search(parent) and len(parent) < 60:
            parent_artist = _smart_case(parent.replace("_", " "))
            if "artist" not in out or fold(out["artist"]) == fold(parent_artist):
                out["artist"] = parent_artist
            elif fold(parent_artist) in fold(name) and "artist" in out:
                pass
            elif len(chunks) >= 2:
                # 'Artist/Other - Album': keep folder split but remember parent
                out["parent_artist"] = parent_artist
            else:
                out["artist"] = parent_artist
            if "album" in out and fold(out["album"]).startswith(fold(parent_artist) + " "):
                out["album"] = out["album"][len(parent_artist):].strip(" -_")
    return {k: v for k, v in out.items() if v}


FILE_DISC_TRACK = re.compile(r"^([1-9])(\d{2})[-_. ]+(.+)$")
FILE_TRACK = re.compile(r"^(\d{1,3})\s*(?:[-._)\]]\s*|\s+)(.+)$")
FILE_ARTIST_TRACK = re.compile(r"^(.+?)\s+-\s+(\d{1,3})\s+-\s+(.+)$")


def parse_filename(filename, artist_hint=None):
    """Guess {track, disc, artist, title} from a file name."""
    stem = re.sub(r"\.[^.]+$", "", filename)
    if "_" in stem and " " not in stem:
        stem = stem.replace("_", " ")
    stem = stem.strip()
    out = {}
    m = FILE_ARTIST_TRACK.match(stem)
    if m:
        out["artist"], out["track"], rest = m.group(1), m.group(2), m.group(3)
    else:
        m = FILE_DISC_TRACK.match(stem)
        if m and "-" in stem[:4]:
            out["disc"], out["track"], rest = m.group(1), m.group(2), m.group(3)
        else:
            m = FILE_TRACK.match(stem)
            if m:
                out["track"], rest = m.group(1), m.group(2)
            else:
                rest = stem
    rest = rest.strip(" -.")
    for sep in (" - ", "-"):
        if sep in rest and "artist" not in out:
            a, t = rest.split(sep, 1)
            # Only split when the left part looks like an artist name we know,
            # or when the file name is clearly 'Artist - Title'.
            if artist_hint and fold(a) in (fold(artist_hint), fold(primary_artist(artist_hint))):
                out["artist"], rest = a.strip(), t.strip()
            elif sep == " - " and (not artist_hint):
                out["artist"], rest = a.strip(), t.strip()
            break
    if not re.fullmatch(r"\d+", rest.strip()):   # '02.mp3' has no title
        out["title"] = _smart_case(rest)
    if "artist" in out:
        out["artist"] = _smart_case(out["artist"])
    if "track" in out:
        out["track"] = str(int(out["track"]))
    return {k: v for k, v in out.items() if v}
