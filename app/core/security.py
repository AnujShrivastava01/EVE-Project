"""Password hashing (Argon2id) and JWT helpers. No web/DB concerns live here."""

from datetime import UTC, datetime, timedelta
from typing import Any

import jwt
from argon2 import PasswordHasher
from argon2.exceptions import InvalidHashError, VerificationError

from app.core.config import get_settings
from app.core.exceptions import InvalidTokenError

_hasher = PasswordHasher()
# Verified against when the email is unknown so both failure paths take similar time.
DUMMY_PASSWORD_HASH = _hasher.hash("not-a-real-password")


def hash_password(password: str) -> str:
    return _hasher.hash(password)


def verify_password(password: str, password_hash: str) -> bool:
    try:
        return _hasher.verify(password_hash, password)
    except (VerificationError, InvalidHashError):
        return False


def create_access_token(subject: str, expires_delta: timedelta | None = None) -> tuple[str, int]:
    """Returns (token, expires_in_seconds)."""
    settings = get_settings()
    delta = expires_delta or timedelta(minutes=settings.access_token_expire_minutes)
    now = datetime.now(UTC)
    claims: dict[str, Any] = {"sub": subject, "iat": now, "exp": now + delta}
    token = jwt.encode(claims, settings.jwt_secret_key, algorithm=settings.jwt_algorithm)
    return token, int(delta.total_seconds())


def decode_access_token(token: str) -> str:
    """Returns the subject (user id). Raises InvalidTokenError for bad/expired tokens."""
    settings = get_settings()
    try:
        claims = jwt.decode(
            token,
            settings.jwt_secret_key,
            algorithms=[settings.jwt_algorithm],
            options={"require": ["sub", "exp"]},
        )
    except jwt.PyJWTError as exc:
        raise InvalidTokenError() from exc
    return str(claims["sub"])
