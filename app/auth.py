"""Optional login for the web UI (one user, scrypt hash, signed cookie), plus the API key that Sonarr/Radarr use."""
import base64
import hashlib
import hmac
import secrets
import time

from . import db

COOKIE = "plexarr_session"
MAX_AGE = 30 * 86400


def _secret() -> bytes:
    s = db.get_state("session_secret")
    if not s:
        s = secrets.token_hex(32)
        db.set_state("session_secret", s)
    return bytes.fromhex(s)


def hash_password(password: str) -> str:
    salt = secrets.token_bytes(16)
    h = hashlib.scrypt(password.encode(), salt=salt, n=2 ** 14, r=8, p=1)
    return f"{salt.hex()}${h.hex()}"


def check_password(password: str, stored: str) -> bool:
    try:
        salt, h = stored.split("$")
        got = hashlib.scrypt(password.encode(), salt=bytes.fromhex(salt), n=2 ** 14, r=8, p=1)
        return hmac.compare_digest(got.hex(), h)
    except ValueError:
        return False


def enabled() -> bool:
    s = db.get_settings()
    return bool(s["auth_enabled"] and s["auth_user"] and db.get_state("auth_hash"))


def set_credentials(user: str, password: str):
    db.set_state("auth_hash", hash_password(password))
    db.set_settings({"auth_user": user.strip()})
    # Changing the password logs out every other session.
    db.set_state("session_epoch", int(time.time()))


def make_cookie(user: str) -> str:
    exp = int(time.time()) + MAX_AGE
    payload = f"{user}|{exp}|{db.get_state('session_epoch', 0)}"
    sig = hmac.new(_secret(), payload.encode(), hashlib.sha256).hexdigest()
    return base64.urlsafe_b64encode(f"{payload}|{sig}".encode()).decode()


def check_cookie(value: str | None) -> str | None:
    if not value:
        return None
    try:
        user, exp, epoch, sig = base64.urlsafe_b64decode(value.encode()).decode().rsplit("|", 3)
    except (ValueError, UnicodeDecodeError):
        return None
    payload = f"{user}|{exp}|{epoch}"
    good = hmac.new(_secret(), payload.encode(), hashlib.sha256).hexdigest()
    if not hmac.compare_digest(sig, good) or int(exp) < time.time():
        return None
    if str(db.get_state("session_epoch", 0)) != epoch or user != db.get_settings()["auth_user"]:
        return None
    return user


def api_key() -> str:
    """Key for the Newznab and SABnzbd APIs (and other tools); created on first use."""
    key = db.get_state("api_key")
    if not key:
        key = new_api_key()
    return key


def new_api_key() -> str:
    key = secrets.token_hex(16)
    db.set_state("api_key", key)
    return key


def check_api_key(value: str | None) -> bool:
    return bool(value) and hmac.compare_digest(value, api_key())
