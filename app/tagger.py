"""Read and write ID3 tags with mutagen. Every write returns the previous
values of the touched fields so it can be undone from the history."""
import os

from mutagen.id3 import (
    APIC, ID3, TALB, TCMP, TCON, TDRC, TIT2, TPE1, TPE2, TPOS, TRCK, TXXX, UFID,
)
from mutagen.mp3 import MP3, BitrateMode

TEXT_FRAMES = {
    "artist": TPE1, "albumartist": TPE2, "album": TALB, "title": TIT2,
    "track": TRCK, "disc": TPOS, "year": TDRC, "genre": TCON, "compilation": TCMP,
}
TXXX_FIELDS = {
    "mb_albumid": "MusicBrainz Album Id",
    "mb_artistid": "MusicBrainz Artist Id",
    "mb_albumartistid": "MusicBrainz Album Artist Id",
    "mb_releasegroupid": "MusicBrainz Release Group Id",
    "mb_releasetrackid": "MusicBrainz Release Track Id",
}
MB_UFID = "http://musicbrainz.org"
EDITABLE = list(TEXT_FRAMES) + list(TXXX_FIELDS) + ["mb_trackid"]


def _text(tags, frame_id):
    f = tags.get(frame_id) if tags is not None else None
    if f is None or not getattr(f, "text", None):
        return None
    v = "; ".join(str(x) for x in f.text if str(x).strip())
    return v.strip() or None


def _get(tags, field):
    if tags is None:
        return None
    if field in TEXT_FRAMES:
        return _text(tags, TEXT_FRAMES[field].__name__)
    if field in TXXX_FIELDS:
        return _text(tags, "TXXX:" + TXXX_FIELDS[field])
    if field == "mb_trackid":
        f = tags.get("UFID:" + MB_UFID)
        return f.data.decode("ascii", "ignore") if f else None
    return None


def read(abs_path):
    """Return a dict ready for the `tracks` table (without path/dir)."""
    st = os.stat(abs_path)
    row = {"size": st.st_size, "mtime": st.st_mtime, "error": None}
    try:
        audio = MP3(abs_path)
    except Exception as e:  # corrupt / not really an mp3
        row.update(tagged=0, error=str(e)[:200])
        return row
    tags = audio.tags
    info = audio.info
    row.update(
        bitrate=int(info.bitrate / 1000) if info.bitrate else None,
        bitrate_mode="VBR" if info.bitrate_mode in (BitrateMode.VBR, BitrateMode.ABR) else "CBR",
        duration=round(info.length or 0, 2),
        sample_rate=info.sample_rate,
        tagged=int(tags is not None and len(tags) > 0),
        id3_version=".".join(map(str, tags.version[:2])) if tags is not None else None,
        has_cover=int(tags is not None and any(k.startswith("APIC") for k in tags.keys())),
    )
    for field in ("artist", "albumartist", "album", "title", "track", "disc", "year", "genre", "mb_albumid"):
        row[field] = _get(tags, field)
    comp = _get(tags, "compilation")
    row["compilation"] = 1 if comp and comp.strip() not in ("0", "") else 0
    if row["year"]:
        row["year"] = row["year"][:4]
    return row


def read_fields(abs_path, fields):
    try:
        tags = ID3(abs_path)
    except Exception:
        tags = None
    return {f: _get(tags, f) for f in fields}


def write(abs_path, changes, cover=None):
    """Apply {field: value}; a None/'' value removes the frame.
    Returns (before, after) dicts limited to fields that actually changed."""
    audio = MP3(abs_path)
    if audio.tags is None:
        audio.add_tags()
    tags = audio.tags
    version = tags.version[1] if tags.version[0] == 2 and tags.version[1] in (3, 4) else 4
    before, after = {}, {}
    for field, value in changes.items():
        if field == "cover" and value is None:  # undo of an embedded cover
            if any(k.startswith("APIC") for k in tags.keys()):
                tags.delall("APIC")
                before["cover"], after["cover"] = "embedded", None
            continue
        if field not in EDITABLE:
            continue
        value = None if value is None or str(value).strip() == "" else str(value).strip()
        old = _get(tags, field)
        if old == value:
            continue
        before[field], after[field] = old, value
        if field in TEXT_FRAMES:
            cls = TEXT_FRAMES[field]
            tags.delall(cls.__name__)
            if value is not None:
                tags.add(cls(encoding=3, text=[value]))
        elif field in TXXX_FIELDS:
            tags.delall("TXXX:" + TXXX_FIELDS[field])
            if value is not None:
                tags.add(TXXX(encoding=3, desc=TXXX_FIELDS[field], text=[value]))
        elif field == "mb_trackid":
            tags.delall("UFID:" + MB_UFID)
            if value is not None:
                tags.add(UFID(owner=MB_UFID, data=value.encode("ascii")))
    if cover and not any(k.startswith("APIC") for k in tags.keys()):
        mime, data = cover
        tags.add(APIC(encoding=3, mime=mime, type=3, desc="Cover", data=data))
        after["cover"] = "embedded"
        before["cover"] = None
    if before or after:
        audio.save(v2_version=version, v1=1)
    return before, after
