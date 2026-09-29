import json
import sqlite3
from contextlib import contextmanager

from . import config

SCHEMA = """
CREATE TABLE IF NOT EXISTS tracks (
    path TEXT PRIMARY KEY,          -- relative to MUSIC_ROOT
    dir TEXT NOT NULL,
    filename TEXT NOT NULL,
    size INTEGER, mtime REAL,
    tagged INTEGER,                 -- 0 = no ID3 tag at all
    artist TEXT, albumartist TEXT, album TEXT, title TEXT,
    track TEXT, disc TEXT, year TEXT, genre TEXT, compilation INTEGER,
    mb_albumid TEXT,
    bitrate INTEGER, bitrate_mode TEXT, duration REAL, sample_rate INTEGER,
    has_cover INTEGER, id3_version TEXT, error TEXT
);
CREATE INDEX IF NOT EXISTS tracks_dir ON tracks(dir);

CREATE TABLE IF NOT EXISTS albums (
    dir TEXT PRIMARY KEY,
    n_tracks INTEGER, n_untagged INTEGER, n_incomplete INTEGER,
    artists TEXT, albumartists TEXT, albums TEXT, years TEXT,
    main_artist TEXT, main_album TEXT, main_year TEXT,
    avg_bitrate INTEGER, min_bitrate INTEGER, vbr INTEGER,
    total_size INTEGER, duration REAL, has_cover INTEGER, has_mbid INTEGER,
    issues TEXT,                    -- json list of issue codes
    suggestion TEXT,                -- json {albumartist, album, year, compilation, confidence, reason}
    quality REAL,
    dup_group INTEGER,
    norm_artist TEXT, norm_album TEXT, titles TEXT,
    tag_artist TEXT,                -- album artist the tags agree on (None when unsure)
    misplaced TEXT                  -- json {folder_artist, tag_artist, target} when stored in another artist's folder
);
CREATE INDEX IF NOT EXISTS albums_dup ON albums(dup_group);

CREATE TABLE IF NOT EXISTS ignores (
    dir TEXT NOT NULL, kind TEXT NOT NULL, PRIMARY KEY (dir, kind)
);

CREATE TABLE IF NOT EXISTS history (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    ts TEXT DEFAULT (datetime('now', 'localtime')),
    batch TEXT, label TEXT, action TEXT, path TEXT,
    before TEXT, after TEXT, undone INTEGER DEFAULT 0
);
CREATE INDEX IF NOT EXISTS history_batch ON history(batch);

-- Files or folders the last scan could not read (they are not in `tracks`).
CREATE TABLE IF NOT EXISTS scan_errors (
    path TEXT PRIMARY KEY, dir TEXT NOT NULL, is_dir INTEGER DEFAULT 0, error TEXT,
    ts TEXT DEFAULT (datetime('now', 'localtime'))
);

CREATE TABLE IF NOT EXISTS meta (key TEXT PRIMARY KEY, value TEXT);

CREATE TABLE IF NOT EXISTS autotag (
    dir TEXT PRIMARY KEY, analyzed TEXT, release_id TEXT, score REAL,
    status TEXT,                    -- ok / ambiguous / partial / none / error / applied
    details TEXT, applied TEXT
);
"""


def connect():
    config.DATA_DIR.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(config.DB_PATH, timeout=30)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA synchronous=NORMAL")
    return conn


@contextmanager
def session():
    conn = connect()
    try:
        yield conn
        conn.commit()
    finally:
        conn.close()


# Columns added after the first release: {table: [(column, type)]}.
MIGRATIONS = {"albums": [("tag_artist", "TEXT"), ("misplaced", "TEXT")]}


def init():
    with session() as c:
        c.executescript(SCHEMA)
        for table, cols in MIGRATIONS.items():
            have = {r["name"] for r in c.execute(f"PRAGMA table_info({table})")}
            for name, kind in cols:
                if name not in have:
                    c.execute(f"ALTER TABLE {table} ADD COLUMN {name} {kind}")


def size_on_disk():
    """Database file plus its write-ahead log, in bytes."""
    total = 0
    for suffix in ("", "-wal"):
        try:
            total += config.DB_PATH.with_name(config.DB_PATH.name + suffix).stat().st_size
        except OSError:
            pass
    return total


def vacuum():
    """Give the space of deleted rows back to the file system."""
    conn = connect()
    try:
        conn.isolation_level = None
        conn.execute("PRAGMA wal_checkpoint(TRUNCATE)")
        conn.execute("VACUUM")
    finally:
        conn.close()


def get_meta(c, key, default=None):
    r = c.execute("SELECT value FROM meta WHERE key=?", (key,)).fetchone()
    return json.loads(r[0]) if r else default


def set_meta(c, key, value):
    c.execute("INSERT OR REPLACE INTO meta(key, value) VALUES (?, ?)", (key, json.dumps(value)))
