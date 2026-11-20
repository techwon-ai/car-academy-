"""Users, password hashing, sessions and login throttling (standard library only).

Sessions are stateless signed cookies (works on serverless): the payload holds
the user id, an expiry, and a fingerprint of the password hash, so changing a
password or deactivating a user ends their sessions everywhere.
"""

import base64
import hashlib
import hmac
import json
import os
import secrets
import time

import ledger as L
from ledger import LedgerError

SESSION_SECONDS = 12 * 3600
MIN_PASSWORD = 12
MAX_FAILS_PER_EMAIL = 5
MAX_FAILS_PER_IP = 20
WINDOW_SECONDS = 15 * 60
COOKIE = "tw_session"

_SCRYPT = dict(n=2 ** 14, r=8, p=1)


def hash_password(pw):
    salt = os.urandom(16)
    h = hashlib.scrypt(pw.encode(), salt=salt, dklen=32, **_SCRYPT)
    return "scrypt$%d$%d$%d$%s$%s" % (_SCRYPT["n"], _SCRYPT["r"], _SCRYPT["p"],
                                       base64.b64encode(salt).decode(), base64.b64encode(h).decode())


def verify_password(pw, stored):
    try:
        _, n, r, p, salt, h = stored.split("$")
        calc = hashlib.scrypt(pw.encode(), salt=base64.b64decode(salt), dklen=32, n=int(n), r=int(r), p=int(p))
        return hmac.compare_digest(calc, base64.b64decode(h))
    except (ValueError, TypeError):
        return False


# A real hash to compare against when the email is unknown, so response time
# doesn't reveal which emails have accounts.
_DUMMY_HASH = hash_password(secrets.token_hex(8))


def _check_password_rules(pw):
    if not isinstance(pw, str) or len(pw) < MIN_PASSWORD:
        raise LedgerError(f"Password must be at least {MIN_PASSWORD} characters")
    if len(pw) > 256:
        raise LedgerError("Password is too long")


def _norm_email(email):
    email = str(email or "").strip().lower()
    if "@" not in email or len(email) > 254 or " " in email:
        raise LedgerError("Enter a valid email address")
    return email


def _public(u):
    return {"id": u["id"], "email": u["email"], "name": u["name"], "is_admin": bool(u["is_admin"]),
            "is_active": bool(u["is_active"]), "created_at": u["created_at"]}


def secret_key(conn):
    row = conn.execute("SELECT value FROM app_secrets WHERE key = 'session_secret'").fetchone()
    if row:
        return row[0].encode()
    with L.tx(conn):
        conn.execute("INSERT INTO app_secrets(key, value) VALUES ('session_secret', ?) ON CONFLICT(key) DO NOTHING",
                     (secrets.token_hex(32),))
    return conn.execute("SELECT value FROM app_secrets WHERE key = 'session_secret'").fetchone()[0].encode()


def user_count(conn):
    return conn.execute("SELECT COUNT(*) FROM users").fetchone()[0]


def create_user(conn, email, password, name="", is_admin=False):
    email = _norm_email(email)
    _check_password_rules(password)
    with L.tx(conn):
        try:
            return conn.execute("INSERT INTO users(email, name, password_hash, is_admin) VALUES (?,?,?,?)",
                                (email, str(name or "").strip(), hash_password(password),
                                 1 if is_admin else 0)).lastrowid
        except L.db.IntegrityError:
            raise LedgerError(f"A user with email {email} already exists") from None


def bootstrap_admin(conn):
    """On a fresh database, create the first admin from ADMIN_EMAIL / ADMIN_PASSWORD."""
    if user_count(conn):
        return False
    email, pw = os.environ.get("ADMIN_EMAIL"), os.environ.get("ADMIN_PASSWORD")
    if not (email and pw):
        return False
    try:
        create_user(conn, email, pw, "Admin", is_admin=True)
    except LedgerError:
        if user_count(conn):  # another instance won the race
            return False
        raise
    return True


def list_users(conn):
    return [_public(u) for u in conn.execute("SELECT * FROM users ORDER BY email")]


def update_user(conn, actor, user_id, is_active=None, is_admin=None, password=None, name=None):
    with L.tx(conn):
        u = L._one(conn, "SELECT * FROM users WHERE id = ?", (L._int(user_id, "user_id"),), "User")
        if u["id"] == actor["id"] and (is_active is False or is_admin is False):
            raise LedgerError("You can't deactivate yourself or remove your own admin rights")
        if is_active is not None:
            conn.execute("UPDATE users SET is_active = ? WHERE id = ?", (1 if is_active else 0, u["id"]))
        if is_admin is not None:
            conn.execute("UPDATE users SET is_admin = ? WHERE id = ?", (1 if is_admin else 0, u["id"]))
        if name is not None:
            conn.execute("UPDATE users SET name = ? WHERE id = ?", (str(name).strip(), u["id"]))
        if password is not None:
            _check_password_rules(password)
            conn.execute("UPDATE users SET password_hash = ? WHERE id = ?", (hash_password(password), u["id"]))
        if not conn.execute("SELECT 1 FROM users WHERE is_admin = 1 AND is_active = 1").fetchone():
            raise LedgerError("There must be at least one active admin")


def change_own_password(conn, user, current, new):
    if not verify_password(str(current or ""), user["password_hash"]):
        raise LedgerError("Current password is incorrect")
    _check_password_rules(new)
    with L.tx(conn):
        conn.execute("UPDATE users SET password_hash = ? WHERE id = ?", (hash_password(new), user["id"]))


class TooManyAttempts(LedgerError):
    pass


def login(conn, email, password, ip=""):
    """Returns the user row, or raises LedgerError / TooManyAttempts."""
    try:
        email = _norm_email(email)
    except LedgerError:
        raise LedgerError("Incorrect email or password") from None
    now = int(time.time())
    since = now - WINDOW_SECONDS
    fails_email = conn.execute("SELECT COUNT(*) FROM login_attempts WHERE email = ? AND success = 0 AND at > ?",
                               (email, since)).fetchone()[0]
    fails_ip = conn.execute("SELECT COUNT(*) FROM login_attempts WHERE ip = ? AND success = 0 AND at > ?",
                            (ip, since)).fetchone()[0] if ip else 0
    if fails_email >= MAX_FAILS_PER_EMAIL or fails_ip >= MAX_FAILS_PER_IP:
        raise TooManyAttempts("Too many failed attempts. Wait 15 minutes and try again.")
    u = conn.execute("SELECT * FROM users WHERE email = ?", (email,)).fetchone()
    ok = verify_password(str(password or ""), u["password_hash"] if u else _DUMMY_HASH) and u and u["is_active"]
    with L.tx(conn):
        conn.execute("INSERT INTO login_attempts(email, ip, at, success) VALUES (?,?,?,?)",
                     (email, ip, now, 1 if ok else 0))
        conn.execute("DELETE FROM login_attempts WHERE at < ?", (now - 30 * 86400,))
    if not ok:
        raise LedgerError("Incorrect email or password")
    return u


# ---- signed session cookie ----

def _b64(b):
    return base64.urlsafe_b64encode(b).rstrip(b"=").decode()


def _unb64(s):
    return base64.urlsafe_b64decode(s + "=" * (-len(s) % 4))


def _pw_fingerprint(user):
    return hashlib.sha256(user["password_hash"].encode()).hexdigest()[:16]


def make_session(conn, user):
    payload = _b64(json.dumps({"uid": user["id"], "exp": int(time.time()) + SESSION_SECONDS,
                               "pv": _pw_fingerprint(user)}).encode())
    sig = _b64(hmac.new(secret_key(conn), payload.encode(), hashlib.sha256).digest())
    return f"{payload}.{sig}"


def read_session(conn, token):
    """Returns the active user for a valid token, else None."""
    try:
        payload, sig = str(token).split(".")
        good = _b64(hmac.new(secret_key(conn), payload.encode(), hashlib.sha256).digest())
        if not hmac.compare_digest(sig, good):
            return None
        data = json.loads(_unb64(payload))
        if data["exp"] < time.time():
            return None
        u = conn.execute("SELECT * FROM users WHERE id = ?", (int(data["uid"]),)).fetchone()
        if not u or not u["is_active"] or not hmac.compare_digest(data["pv"], _pw_fingerprint(u)):
            return None
        return u
    except (ValueError, KeyError, TypeError):
        return None
