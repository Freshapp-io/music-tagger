"""Release sources: MusicBrainz, and Discogs when a token is set. Discogs ids
carry the 'discogs:' prefix; everything else is a MusicBrainz id."""
from . import discogs, genres, musicbrainz


def is_discogs(release_id):
    return str(release_id or "").startswith(discogs.PREFIX)


def release(release_id):
    if is_discogs(release_id):
        return discogs.release(release_id)
    return {"source": "musicbrainz", **musicbrainz.release(release_id)}


def changes_for(rel, mapping):
    return (discogs if is_discogs(rel["id"]) else musicbrainz).changes_for(rel, mapping)


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
