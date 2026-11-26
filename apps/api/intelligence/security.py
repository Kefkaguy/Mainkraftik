import hashlib
import secrets
from datetime import timedelta

from argon2 import PasswordHasher
from argon2.exceptions import InvalidHashError, VerificationError
from fastapi import HTTPException, Request

from intelligence.config import settings
from intelligence.db import connection
from intelligence.util import now

COOKIE = "intelligence_session"
COOKIE_PATH = "/intelligence"
hasher = PasswordHasher()


def hash_token(token: str):
    return hashlib.sha256(token.encode()).hexdigest()


def origin_check(request: Request):
    if request.headers.get("origin") != settings.origin:
        raise HTTPException(403, "Request origin is not permitted")


def require_session(request: Request):
    token = request.cookies.get(COOKIE, "")
    if not token:
        raise HTTPException(401, "Sign in to continue")
    with connection() as conn:
        row = conn.execute(
            "SELECT * FROM sessions WHERE token_hash=%s AND revoked_at IS NULL AND expires_at>now()",
            (hash_token(token),),
        ).fetchone()
    if not row:
        raise HTTPException(401, "Your session has expired. Sign in again.")
    if request.method not in {"GET", "HEAD", "OPTIONS"}:
        origin_check(request)
        if not secrets.compare_digest(request.headers.get("x-csrf-token", ""), row["csrf_token"]):
            raise HTTPException(403, "Refresh the page before retrying this action")
    return row


def login(username: str, password: str, address: str):
    if not settings.admin_password_hash:
        raise HTTPException(503, "Administrator password has not been configured")
    address_hash = hash_token(address)
    with connection() as conn:
        conn.execute("SELECT pg_advisory_xact_lock(hashtext(%s))", (address_hash,))
        recent = conn.execute(
            "SELECT count(*) AS n FROM login_attempts WHERE address_hash=%s AND attempted_at>now()-interval '15 minutes' AND NOT succeeded",
            (address_hash,),
        ).fetchone()["n"]
        if recent >= 8:
            raise HTTPException(429, "Too many attempts. Try again in 15 minutes.")
        try:
            valid = hasher.verify(settings.admin_password_hash, password)
        except (InvalidHashError, VerificationError):
            valid = False
        valid = valid and secrets.compare_digest(username, settings.admin_username)
        conn.execute(
            "INSERT INTO login_attempts(address_hash,succeeded) VALUES(%s,%s)", (address_hash, valid)
        )
        conn.commit()  # Failed attempts must persist before the HTTP exception.
        if not valid:
            raise HTTPException(401, "Username or password is incorrect")
        token, csrf = secrets.token_urlsafe(48), secrets.token_urlsafe(32)
        expires = now() + timedelta(days=7)
        conn.execute(
            "INSERT INTO sessions(token_hash,csrf_token,expires_at) VALUES(%s,%s,%s)",
            (hash_token(token), csrf, expires),
        )
    return token, csrf, expires
