"""Tests for JWT + password primitives (Sprint 09 auth)."""
from __future__ import annotations

import pytest

from api.app.auth import (
    AuthError,
    AuthUser,
    TOKEN_TTL_SECONDS,
    create_token,
    decode_token,
    hash_password,
    verify_password,
)

SECRET = "test-secret"


def test_password_hash_roundtrip() -> None:
    digest = hash_password("s3cret")
    assert digest != "s3cret"
    assert verify_password("s3cret", digest)
    assert not verify_password("wrong", digest)


def test_verify_password_bad_hash_returns_false() -> None:
    assert not verify_password("x", "not-a-bcrypt-hash")


def test_token_roundtrip() -> None:
    user = AuthUser(username="dev1", role="dev")
    token = create_token(user, SECRET)
    assert decode_token(token, SECRET) == user


def test_expired_token_rejected() -> None:
    user = AuthUser(username="dev1", role="dev")
    token = create_token(user, SECRET, now=1_000_000.0)  # far past
    with pytest.raises(AuthError):
        decode_token(token, SECRET)


def test_wrong_secret_rejected() -> None:
    token = create_token(AuthUser(username="m1", role="manager"), SECRET)
    with pytest.raises(AuthError):
        decode_token(token, "other-secret")


def test_bad_role_claim_rejected() -> None:
    import jwt as pyjwt

    token = pyjwt.encode(
        {"sub": "x", "role": "root", "exp": 9_999_999_999}, SECRET, algorithm="HS256"
    )
    with pytest.raises(AuthError):
        decode_token(token, SECRET)


def test_ttl_is_12_hours() -> None:
    assert TOKEN_TTL_SECONDS == 12 * 3600
