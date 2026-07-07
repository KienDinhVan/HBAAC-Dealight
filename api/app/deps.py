from __future__ import annotations

from fastapi import Request

from api.app.agents.team import TeamLeadAgent
from api.app.clients.airflow import AirflowClient
from api.app.clients.duckdb_client import DuckDBClient
from api.app.clients.gcs import GcsUploader
from api.app.infra.approval import ApprovalStore
from api.app.repository import ForecastRepository


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
