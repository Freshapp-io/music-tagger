from fastapi.testclient import TestClient

from app import auth
from app.main import app


def test_everything_private_until_login():
    with TestClient(app) as c:
        assert c.get("/api/status").status_code == 401
        assert c.get("/api/audio", params={"path": "x.mp3"}).status_code == 401
        r = c.get("/", follow_redirects=False)
        assert r.status_code == 303 and r.headers["location"] == "/login"
        assert c.get("/docs", follow_redirects=False).status_code == 303
        # the login page and its assets stay reachable
        for url in ("/login", "/healthz", "/static/style.css", "/static/login.js", "/static/brand/colibri.svg"):
            assert c.get(url).status_code == 200, url
        assert c.get("/static/app.js", follow_redirects=False).status_code == 303


def test_login_logout_and_cookie_flags():
    with TestClient(app) as c:
        assert c.post("/api/login", json={"user": "admin", "password": "nope"}).status_code == 401
        r = c.post("/api/login", json={"user": "admin", "password": "test-password"})
        assert r.status_code == 200
        cookie = r.headers["set-cookie"].lower()
        assert "httponly" in cookie and "samesite=strict" in cookie and "secure" not in cookie
        assert c.get("/api/status").status_code == 200
        c.post("/api/logout")
        assert c.get("/api/status").status_code == 401


def test_secure_cookie_behind_https_proxy():
    with TestClient(app) as c:
        r = c.post("/api/login", json={"user": "admin", "password": "test-password"},
                   headers={"X-Forwarded-Proto": "https"})
        assert "secure" in r.headers["set-cookie"].lower()


def test_forged_or_expired_tokens_rejected(monkeypatch):
    good = auth.make_token("admin")
    assert auth.read_token(good) == "admin"
    payload, sig = good.split(".")
    assert auth.read_token(payload + "." + "0" * len(sig)) is None
    assert auth.read_token("garbage") is None
    monkeypatch.setattr(auth.time, "time", lambda: 10 ** 12)
    assert auth.read_token(good) is None


def test_password_change_invalidates_sessions(monkeypatch):
    token = auth.make_token("admin")
    monkeypatch.setattr(auth.config, "APP_PASSWORD", "another-one")
    assert auth.read_token(token) is None


def test_hashed_password(monkeypatch):
    h = auth.hash_password("s3cret")
    assert "$" not in h
    monkeypatch.setattr(auth.config, "APP_PASSWORD", None)
    monkeypatch.setattr(auth.config, "APP_PASSWORD_HASH", h)
    assert auth.check_credentials("admin", "s3cret")
    assert not auth.check_credentials("admin", "s3cret!")
    assert not auth.check_credentials("root", "s3cret")


def test_bruteforce_lock(monkeypatch):
    monkeypatch.setattr("app.main.time.sleep", lambda s: None)
    with TestClient(app) as c:
        for _ in range(auth.MAX_FAILURES):
            assert c.post("/api/login", json={"user": "admin", "password": "x"}).status_code == 401
        r = c.post("/api/login", json={"user": "admin", "password": "test-password"})
        assert r.status_code == 429
    auth._failures.clear()


def test_generated_password_when_none_configured(monkeypatch, tmp_path):
    monkeypatch.setattr(auth.config, "APP_PASSWORD", None)
    monkeypatch.setattr(auth.config, "APP_PASSWORD_HASH", None)
    monkeypatch.setattr(auth.config, "DATA_DIR", tmp_path)
    auth._generated.clear()
    kind, pw = auth.password_setting()
    assert kind == "plain" and len(pw) >= 12
    assert (tmp_path / "generated-password.txt").read_text() == pw
    assert auth.check_credentials("admin", pw)
    auth._generated.clear()
