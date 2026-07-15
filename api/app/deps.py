from __future__ import annotations

from fastapi import Depends, HTTPException, Request, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer

from api.app.agents.team import TeamLeadAgent
from api.app.auth import AuthError, AuthUser, decode_token
from api.app.clients.airflow import AirflowClient
from api.app.clients.bigquery import OfflineStoreClient
from api.app.clients.duckdb_client import DuckDBClient
from api.app.clients.gcs import GcsUploader
from api.app.clients.redis_store import OnlineStoreClient
from api.app.config import get_settings
from api.app.infra.approval import ApprovalStore
from api.app.infra.user_store import UserStore
from api.app.repository import ForecastRepository

_bearer = HTTPBearer(auto_error=False)


def get_repository(request: Request) -> ForecastRepository:
    return request.app.state.repository


def get_team_lead(request: Request) -> TeamLeadAgent:
    return request.app.state.team_lead


def get_approval_store(request: Request) -> ApprovalStore:
    return request.app.state.approval_store


def get_airflow_client(request: Request) -> AirflowClient:
    return request.app.state.airflow_client


def get_duckdb(request: Request) -> DuckDBClient:
    return request.app.state.duckdb


def get_gcs_uploader(request: Request) -> GcsUploader | None:
    return getattr(request.app.state, "gcs_uploader", None)


def get_offline_store(request: Request) -> OfflineStoreClient | None:
    return getattr(request.app.state, "offline_store", None)


def get_online_store(request: Request) -> OnlineStoreClient | None:
    return getattr(request.app.state, "online_store", None)


def get_user_store(request: Request) -> UserStore:
    return request.app.state.user_store


def require_user(
    credentials: HTTPAuthorizationCredentials | None = Depends(_bearer),
) -> AuthUser:
    if credentials is None:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Missing bearer token")
    try:
        return decode_token(credentials.credentials, get_settings().jwt_secret)
    except AuthError as exc:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, f"Invalid token: {exc}") from exc


def require_manager(user: AuthUser = Depends(require_user)) -> AuthUser:
    if user.role != "manager":
        raise HTTPException(status.HTTP_403_FORBIDDEN, "Manager role required")
    return user
