"""Release sources: MusicBrainz, and Discogs when a token is set. Discogs ids
carry the 'discogs:' prefix; everything else is a MusicBrainz id."""
from . import db, discogs, genres, musicbrainz


def is_discogs(release_id):
    return str(release_id or "").startswith(discogs.PREFIX)


def release(release_id):
    if is_discogs(release_id):
        return discogs.release(release_id)
    return {"source": "musicbrainz", **musicbrainz.release(release_id)}


def split_artists():
    """Setting: write several artists of a credit as 'A; B' (default) rather
    than as the source spells them ('A & B')."""
    with db.session() as c:
        return bool(db.get_meta(c, "split_artists", True))


def set_split_artists(value):
    with db.session() as c:
        db.set_meta(c, "split_artists", bool(value))


def changes_for(rel, mapping, split=None):
    split = split_artists() if split is None else split
    return (discogs if is_discogs(rel["id"]) else musicbrainz).changes_for(rel, mapping, split)


def cover(rel):
    if is_discogs(rel["id"]):
        return discogs.cover(rel)
    return musicbrainz.cover(rel["id"]) if rel.get("cover") else None


def name(rel):
    return "Discogs" if is_discogs(rel["id"]) else "MusicBrainz"


def genre(rel, canon=None):
    """First genre / style of the release that maps onto a target genre."""
    canon = canon or genres.canon_list()
    for g in rel.get("genres") or []:
        target = genres.classify(g, canon)[0]
        if target and target != genres.JUNK:
            return target
    return None
