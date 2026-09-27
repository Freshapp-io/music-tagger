import os
import sys
import tempfile
from pathlib import Path

import pytest

_TMP = Path(tempfile.mkdtemp(prefix="mt-"))
os.environ["MUSIC_ROOT"] = str(_TMP / "music")
os.environ["DATA_DIR"] = str(_TMP / "data")
os.environ["APP_USER"] = "admin"
os.environ["APP_PASSWORD"] = "test-password"
(_TMP / "music").mkdir()
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

# One silent MPEG-1 Layer III frame, 128 kbps, 44.1 kHz (417 bytes).
FRAME = b"\xff\xfb\x90\x64" + b"\x00" * 413


def make_mp3(path, seconds=2, bitrate_frames=FRAME, **tags):
    from mutagen.id3 import ID3, TALB, TCMP, TDRC, TIT2, TPE1, TPE2, TRCK
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(bitrate_frames * int(seconds * 38.28))
    frames = {"artist": TPE1, "albumartist": TPE2, "album": TALB, "title": TIT2,
              "track": TRCK, "year": TDRC, "compilation": TCMP}
    if tags:
        t = ID3()
        for k, v in tags.items():
            t.add(frames[k](encoding=3, text=[v]))
        t.save(path)
    return path


@pytest.fixture()
def library():
    """Empty library + fresh DB for each test."""
    import shutil

    from app import config, db
    for p in config.MUSIC_ROOT.iterdir():
        shutil.rmtree(p) if p.is_dir() else p.unlink()
    if config.DB_PATH.exists():
        config.DB_PATH.unlink()
    db.init()
    return config.MUSIC_ROOT


class FakeJob:
    def __init__(self):
        self.total = self.done = 0
        self.message = ""
        self.errors = []

    def step(self, message=None, n=1):
        self.done += n

    def error(self, where, err):
        self.errors.append((where, str(err)))


@pytest.fixture()
def job():
    return FakeJob()


def login(client):
    r = client.post("/api/login", json={"user": "admin", "password": "test-password"})
    assert r.status_code == 200
    return client
