"""JWT + password primitives for the dev/manager auth layer (Sprint 09)."""
from __future__ import annotations

import time
from dataclasses import dataclass

import bcrypt
import jwt

ALGORITHM = "HS256"
TOKEN_TTL_SECONDS = 12 * 3600
VALID_ROLES = {"dev", "manager"}


class AuthError(Exception):
    pass


@dataclass(frozen=True)
class AuthUser:
    username: str
    role: str  # 'dev' | 'manager'


def hash_password(password: str) -> str:
    return bcrypt.hashpw(password.encode(), bcrypt.gensalt()).decode()


def verify_password(password: str, password_hash: str) -> bool:
    try:
        return bcrypt.checkpw(password.encode(), password_hash.encode())
    except ValueError:
        return False


def create_token(user: AuthUser, secret: str, now: float | None = None) -> str:
    issued = int(now if now is not None else time.time())
    payload = {
        "sub": user.username,
        "role": user.role,
        "iat": issued,
        "exp": issued + TOKEN_TTL_SECONDS,
    }
    return jwt.encode(payload, secret, algorithm=ALGORITHM)


def decode_token(token: str, secret: str) -> AuthUser:
    try:
        payload = jwt.decode(token, secret, algorithms=[ALGORITHM])
    except jwt.PyJWTError as exc:
        raise AuthError(str(exc)) from exc
    username = payload.get("sub")
    role = payload.get("role")
    if not username or role not in VALID_ROLES:
        raise AuthError("invalid token claims")
    return AuthUser(username=username, role=role)
