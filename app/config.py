import os
from pathlib import Path


def _env(name, default=None):
    v = os.environ.get(name)
    return v if v not in (None, "") else default


APP_NAME = "Freshapp.io Music Tagger"
VERSION = "1.11.1"

MUSIC_ROOT = Path(_env("MUSIC_ROOT", "/music")).resolve()
DATA_DIR = Path(_env("DATA_DIR", "/data")).resolve()
# Duplicates are moved here instead of being deleted. Keeping it inside the
# music volume makes the move an instant rename (no copy over the network).
TRASH_DIR = Path(_env("TRASH_DIR", str(MUSIC_ROOT / ".music-tagger-trash"))).resolve()
DB_PATH = DATA_DIR / "music-tagger.db"

SCAN_THREADS = int(_env("SCAN_THREADS", "8"))

MB_USER_AGENT = _env("MB_USER_AGENT", f"freshapp-music-tagger/{VERSION} ( https://github.com/Freshapp-io/music-tagger )")

# Optional second tag source: personal access token from discogs.com > Settings > Developers.
DISCOGS_TOKEN = _env("DISCOGS_TOKEN")

NAVIDROME_URL = _env("NAVIDROME_URL")
NAVIDROME_USER = _env("NAVIDROME_USER")
NAVIDROME_PASSWORD = _env("NAVIDROME_PASSWORD")

AUDIO_EXT = (".mp3",)

# Login. Without APP_PASSWORD / APP_PASSWORD_HASH a password is generated at
# first start, stored in DATA_DIR/generated-password.txt and printed in the logs.
APP_USER = _env("APP_USER", "admin")
APP_PASSWORD = _env("APP_PASSWORD")
APP_PASSWORD_HASH = _env("APP_PASSWORD_HASH")     # python -m app.auth hash
SECRET_KEY = _env("SECRET_KEY")                   # default: generated in DATA_DIR
COOKIE_SECURE = _env("COOKIE_SECURE", "auto")     # auto = when served over HTTPS
