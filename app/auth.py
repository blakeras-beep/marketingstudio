"""Accounts, roles and sessions.

Roles:
  admin      creates and manages users; can edit everything
  marketing  edits marketing inputs (features, lot size, utilities, ...)
  csm        views and downloads flyers only

Passwords are hashed with scrypt (stdlib). Sessions are random tokens in an
HttpOnly cookie; only the token's SHA-256 is stored. The first admin is created
from ADMIN_EMAIL / ADMIN_PASSWORD when no active admin exists.
"""
from __future__ import annotations

import hashlib
import hmac
import os
import re
import secrets
from datetime import datetime, timedelta, timezone

from fastapi import HTTPException, Request

from . import db

ROLES = ("admin", "marketing", "csm")
ROLE_LABELS = {"admin": "Admin", "marketing": "Marketing Director", "csm": "CSM"}
COOKIE = "ms_session"
SESSION_DAYS = int(os.environ.get("SESSION_DAYS", "30"))
MIN_PASSWORD = 10
EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")


def now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


# ---------------------------------------------------------------- passwords

def hash_password(pw: str) -> str:
    salt = secrets.token_bytes(16)
    dk = hashlib.scrypt(pw.encode(), salt=salt, n=2**14, r=8, p=1, dklen=32)
    return f"scrypt$16384$8$1${salt.hex()}${dk.hex()}"


def verify_password(pw: str, stored: str) -> bool:
    try:
        algo, n, r, p, salt, dk = stored.split("$")
        if algo != "scrypt":
            return False
        calc = hashlib.scrypt(pw.encode(), salt=bytes.fromhex(salt), n=int(n), r=int(r), p=int(p),
                              dklen=len(bytes.fromhex(dk)))
        return hmac.compare_digest(calc.hex(), dk)
    except (ValueError, TypeError):
        return False


def check_password_rules(pw: str) -> str | None:
    if len(pw or "") < MIN_PASSWORD:
        return f"Password must be at least {MIN_PASSWORD} characters."
    return None


# ---------------------------------------------------------------- users

def _clean_email(email: str) -> str:
    return (email or "").strip().lower()


def get_user(user_id: int) -> dict | None:
    with db.connect() as c:
        return c.one("SELECT id, email, name, role, active FROM users WHERE id = ?", (user_id,))


def list_users() -> list[dict]:
    with db.connect() as c:
        return c.q("SELECT id, email, name, role, active, created_at FROM users ORDER BY active DESC, name")


def create_user(email: str, name: str, role: str, password: str, created_by: int | None) -> dict:
    email, name = _clean_email(email), (name or "").strip()
    if not EMAIL_RE.match(email):
        raise ValueError("Enter a valid email address.")
    if not name:
        raise ValueError("Enter a name.")
    if role not in ROLES:
        raise ValueError("Pick a role.")
    err = check_password_rules(password)
    if err:
        raise ValueError(err)
    with db.connect() as c:
        if c.one("SELECT id FROM users WHERE email = ?", (email,)):
            raise ValueError("A user with that email already exists.")
        c.x("INSERT INTO users (email, name, role, password_hash, active, created_at, created_by) "
            "VALUES (?, ?, ?, ?, 1, ?, ?)", (email, name, role, hash_password(password), now(), created_by))
        return c.one("SELECT id, email, name, role, active FROM users WHERE email = ?", (email,))


def _active_admins(c, excluding: int | None = None) -> int:
    row = c.one("SELECT COUNT(*) AS n FROM users WHERE role = 'admin' AND active = 1 AND id != ?",
                (excluding if excluding is not None else -1,))
    return int(row["n"])


def update_user(user_id: int, *, name: str | None = None, role: str | None = None,
                active: bool | None = None) -> None:
    with db.connect() as c:
        u = c.one("SELECT id, role, active FROM users WHERE id = ?", (user_id,))
        if not u:
            raise ValueError("No such user.")
        losing_admin = (u["role"] == "admin" and u["active"]) and (
            (role is not None and role != "admin") or (active is False))
        if losing_admin and _active_admins(c, excluding=user_id) == 0:
            raise ValueError("There must always be at least one active admin.")
        if role is not None:
            if role not in ROLES:
                raise ValueError("Pick a role.")
            c.x("UPDATE users SET role = ? WHERE id = ?", (role, user_id))
        if name is not None:
            if not name.strip():
                raise ValueError("Enter a name.")
            c.x("UPDATE users SET name = ? WHERE id = ?", (name.strip(), user_id))
        if active is not None:
            c.x("UPDATE users SET active = ? WHERE id = ?", (1 if active else 0, user_id))
            if not active:
                c.x("DELETE FROM sessions WHERE user_id = ?", (user_id,))


def set_password(user_id: int, password: str) -> None:
    err = check_password_rules(password)
    if err:
        raise ValueError(err)
    with db.connect() as c:
        c.x("UPDATE users SET password_hash = ? WHERE id = ?", (hash_password(password), user_id))
        c.x("DELETE FROM sessions WHERE user_id = ?", (user_id,))  # sign out everywhere


def authenticate(email: str, password: str) -> dict | None:
    with db.connect() as c:
        u = c.one("SELECT id, email, name, role, active, password_hash FROM users WHERE email = ?",
                  (_clean_email(email),))
    if not u or not u["active"] or not verify_password(password, u["password_hash"]):
        return None
    u.pop("password_hash")
    return u


def bootstrap_admin() -> str | None:
    """Create the first admin from env when there is no active admin. Returns a note for the log."""
    email, pw = _clean_email(os.environ.get("ADMIN_EMAIL", "")), os.environ.get("ADMIN_PASSWORD", "")
    with db.connect() as c:
        if _active_admins(c):
            return None
    if not email or not pw:
        return "No admin exists. Set ADMIN_EMAIL and ADMIN_PASSWORD to create the first one."
    with db.connect() as c:
        existing = c.one("SELECT id FROM users WHERE email = ?", (email,))
    if existing:  # e.g. the admin was deactivated by accident: restore them
        update_user(existing["id"], role="admin", active=True)
        set_password(existing["id"], pw)
        return f"Restored admin {email} from ADMIN_EMAIL."
    create_user(email, os.environ.get("ADMIN_NAME", "Admin"), "admin", pw, None)
    return f"Created admin {email} from ADMIN_EMAIL."


# ---------------------------------------------------------------- sessions

def _hash_token(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


def start_session(user_id: int) -> str:
    token = secrets.token_urlsafe(32)
    expires = (datetime.now(timezone.utc) + timedelta(days=SESSION_DAYS)).isoformat(timespec="seconds")
    with db.connect() as c:
        c.x("INSERT INTO sessions (token_hash, user_id, created_at, expires_at) VALUES (?, ?, ?, ?)",
            (_hash_token(token), user_id, now(), expires))
    return token


def end_session(token: str | None) -> None:
    if token:
        with db.connect() as c:
            c.x("DELETE FROM sessions WHERE token_hash = ?", (_hash_token(token),))


def user_for_token(token: str | None) -> dict | None:
    if not token:
        return None
    with db.connect() as c:
        row = c.one("SELECT u.id, u.email, u.name, u.role, u.active, s.expires_at FROM sessions s "
                    "JOIN users u ON u.id = s.user_id WHERE s.token_hash = ?", (_hash_token(token),))
        if not row:
            return None
        if not row["active"] or row["expires_at"] < now():
            c.x("DELETE FROM sessions WHERE token_hash = ?", (_hash_token(token),))
            return None
    row.pop("expires_at")
    return row


# ---------------------------------------------------------------- request helpers

def current_user(request: Request) -> dict | None:
    return getattr(request.state, "user", None)


def can_edit_inputs(user: dict | None) -> bool:
    return bool(user) and user["role"] in ("admin", "marketing")


def require(request: Request, *roles: str) -> dict:
    user = current_user(request)
    if user is None:
        raise HTTPException(401, "Sign in required")
    if roles and user["role"] not in roles:
        raise HTTPException(403, "You don't have permission to do that")
    return user


def same_origin(request: Request) -> bool:
    """CSRF guard for form posts: the Origin (or Referer) must be this host."""
    origin = request.headers.get("origin") or request.headers.get("referer") or ""
    if not origin:
        return True  # non-browser clients; the SameSite=Lax cookie already blocks cross-site posts
    host = request.headers.get("x-forwarded-host") or request.headers.get("host") or ""
    m = re.match(r"^https?://([^/]+)", origin)
    return bool(m) and m.group(1) == host
