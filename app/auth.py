"""Login for the whole app: one account defined in the environment, a signed
session cookie, and a per-IP brake on failed attempts.

Generate a password hash with:  python -m app.auth hash
"""
import base64
import getpass
import hashlib
import hmac
import secrets
import sys
import threading
import time

from . import config

COOKIE = "fmt_session"
SESSION_DAYS = 30
MAX_FAILURES = 5
LOCK_SECONDS = 15 * 60

# Paths reachable without being logged in (login page and what it needs).
PUBLIC_PREFIXES = ("/static/brand/", "/static/fonts/")
PUBLIC_PATHS = {"/login", "/api/login", "/healthz", "/static/style.css", "/static/login.js", "/static/theme.js"}

_failures = {}          # ip -> [timestamps]
_lock = threading.Lock()
_generated = {}


# ------------------------------------------------------------------ secrets

def _read_or_create(path, factory):
    config.DATA_DIR.mkdir(parents=True, exist_ok=True)
    if path.exists():
        return path.read_text().strip()
    value = factory()
    path.write_text(value)
    path.chmod(0o600)
    return value


def secret_key():
    if config.SECRET_KEY:
        return config.SECRET_KEY
    return _read_or_create(config.DATA_DIR / "secret.key", lambda: secrets.token_urlsafe(48))


def password_setting():
    """(kind, value): 'hash' or 'plain' from the environment, else a password
    generated once and kept in DATA_DIR (printed in the logs)."""
    if config.APP_PASSWORD_HASH:
        return "hash", config.APP_PASSWORD_HASH
    if config.APP_PASSWORD:
        return "plain", config.APP_PASSWORD
    if "pw" not in _generated:
        path = config.DATA_DIR / "generated-password.txt"
        existed = path.exists()
        _generated["pw"] = _read_or_create(path, lambda: secrets.token_urlsafe(12))
        if not existed:
            print(f"\n*** No APP_PASSWORD set: generated login '{config.APP_USER}' / "
                  f"'{_generated['pw']}' (stored in {path}) ***\n", flush=True)
    return "plain", _generated["pw"]


# ---------------------------------------------------------------- passwords

def hash_password(password, n=2 ** 14, r=8, p=1):
    salt = secrets.token_bytes(16)
    dk = hashlib.scrypt(password.encode(), salt=salt, n=n, r=r, p=p, dklen=32)
    b64 = lambda b: base64.urlsafe_b64encode(b).decode().rstrip("=")
    # ':' separators: '$' would be expanded by docker compose when read from .env
    return f"scrypt:{n}:{r}:{p}:{b64(salt)}:{b64(dk)}"


def _b64d(s):
    return base64.urlsafe_b64decode(s + "=" * (-len(s) % 4))


def verify_password(password):
    kind, value = password_setting()
    if kind == "plain":
        return hmac.compare_digest(password.encode(), value.encode())
    try:
        _, n, r, p, salt, dk = value.split(":")
        got = hashlib.scrypt(password.encode(), salt=_b64d(salt), n=int(n), r=int(r), p=int(p), dklen=32)
        return hmac.compare_digest(got, _b64d(dk))
    except (ValueError, TypeError):
        return False


def check_credentials(user, password):
    user_ok = hmac.compare_digest((user or "").encode(), config.APP_USER.encode())
    pw_ok = verify_password(password or "")
    return user_ok and pw_ok


# ------------------------------------------------------------------ session

def _signing_key():
    # Changing the password (or the secret) invalidates every open session.
    kind, value = password_setting()
    return hashlib.sha256((secret_key() + "|" + kind + "|" + value).encode()).digest()


def make_token(user):
    exp = int(time.time()) + SESSION_DAYS * 86400
    payload = base64.urlsafe_b64encode(f"{user}|{exp}".encode()).decode().rstrip("=")
    sig = hmac.new(_signing_key(), payload.encode(), hashlib.sha256).hexdigest()
    return f"{payload}.{sig}"


def read_token(token):
    """User name if the token is valid and not expired, else None."""
    try:
        payload, sig = token.split(".", 1)
        expected = hmac.new(_signing_key(), payload.encode(), hashlib.sha256).hexdigest()
        if not hmac.compare_digest(sig, expected):
            return None
        user, exp = _b64d(payload).decode().rsplit("|", 1)
        if int(exp) < time.time() or user != config.APP_USER:
            return None
        return user
    except (ValueError, AttributeError):
        return None


# ------------------------------------------------------------ brute force

def seconds_locked(ip):
    now = time.time()
    with _lock:
        recent = [t for t in _failures.get(ip, []) if now - t < LOCK_SECONDS]
        _failures[ip] = recent
        if len(recent) >= MAX_FAILURES:
            return int(LOCK_SECONDS - (now - recent[0])) + 1
    return 0


def record_failure(ip):
    with _lock:
        _failures.setdefault(ip, []).append(time.time())


def clear_failures(ip):
    with _lock:
        _failures.pop(ip, None)


def is_public(path):
    return path in PUBLIC_PATHS or path.startswith(PUBLIC_PREFIXES)


if __name__ == "__main__":
    if sys.argv[1:] == ["hash"]:
        pw = getpass.getpass("Mot de passe : ")
        if pw != getpass.getpass("Confirmation : "):
            sys.exit("Les mots de passe ne correspondent pas.")
        print(hash_password(pw))
    else:
        sys.exit("usage: python -m app.auth hash")
