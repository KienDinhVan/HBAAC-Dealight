from __future__ import annotations

import logging

from fastapi import APIRouter, Depends, HTTPException, status

from api.app.auth import AuthUser, create_token, verify_password
from api.app.config import get_settings
from api.app.deps import get_user_store, require_user
from api.app.infra.user_store import UserStore
from api.app.schemas import LoginRequest, LoginResponse, MeResponse

router = APIRouter(prefix="/api/v1/auth", tags=["auth"])
_logger = logging.getLogger(__name__)


@router.post("/login", response_model=LoginResponse)
def login(body: LoginRequest, store: UserStore = Depends(get_user_store)) -> LoginResponse:
    record = store.get_user(body.username)
    if record is None or not verify_password(body.password, record["password_hash"]):
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Invalid username or password")
    user = AuthUser(username=record["username"], role=record["role"])
    token = create_token(user, get_settings().jwt_secret)
    return LoginResponse(access_token=token, username=user.username, role=user.role)


@router.get("/me", response_model=MeResponse)
def me(user: AuthUser = Depends(require_user)) -> MeResponse:
    return MeResponse(username=user.username, role=user.role)
