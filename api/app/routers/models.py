"""Model registry views + promotion approval workflow (Sprint 09)."""
from __future__ import annotations

import logging

from fastapi import APIRouter, Depends, HTTPException, Query, Request, status

from api.app.auth import AuthUser
from api.app.clients.mlflow_registry import ModelRegistryClient
from api.app.deps import (
    get_model_registry,
    get_promotion_store,
    require_manager,
    require_user,
)
from api.app.infra.promotion_store import PromotionStore
from api.app.schemas import (
    ModelCompareResponse,
    ModelVersionsResponse,
    PromotionRequestCreate,
    PromotionRequestItem,
    PromotionRequestListResponse,
    PromotionReviewBody,
)

router = APIRouter(prefix="/api/v1", tags=["models"])
_logger = logging.getLogger(__name__)


def _dataset_model(dataset: str) -> str:
    return f"{dataset}-forecaster"


@router.get("/models/{dataset}/versions", response_model=ModelVersionsResponse)
def list_model_versions(
    dataset: str,
    registry: ModelRegistryClient = Depends(get_model_registry),
) -> ModelVersionsResponse:
    model_name = _dataset_model(dataset)
    try:
        versions = registry.list_versions(model_name)
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(status.HTTP_502_BAD_GATEWAY, f"MLflow error: {exc}") from exc
    return ModelVersionsResponse(dataset=dataset, model_name=model_name, versions=versions)


@router.get("/models/{dataset}/compare", response_model=ModelCompareResponse)
def compare_model_versions(
    dataset: str,
    candidate: str | None = Query(default=None),
    registry: ModelRegistryClient = Depends(get_model_registry),
) -> ModelCompareResponse:
    model_name = _dataset_model(dataset)
    try:
        target = candidate or registry.get_alias_version(model_name, "staging")
        if target is None:
            raise HTTPException(
                status.HTTP_404_NOT_FOUND, "No candidate: no @staging version exists"
            )
        result = registry.compare(model_name, target)
    except HTTPException:
        raise
    except ValueError as exc:
        raise HTTPException(status.HTTP_404_NOT_FOUND, str(exc)) from exc
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(status.HTTP_502_BAD_GATEWAY, f"MLflow error: {exc}") from exc
    return ModelCompareResponse(
        dataset=dataset, model_name=model_name,
        candidate=result["candidate"], production=result["production"],
    )


@router.post("/models/{dataset}/promotion-requests", response_model=PromotionRequestItem)
def create_promotion_request(
    dataset: str,
    body: PromotionRequestCreate,
    user: AuthUser = Depends(require_user),
    registry: ModelRegistryClient = Depends(get_model_registry),
    store: PromotionStore = Depends(get_promotion_store),
) -> PromotionRequestItem:
    model_name = _dataset_model(dataset)
    try:
        prod_version = registry.get_alias_version(model_name, "production")
        snapshot = registry.compare(model_name, body.candidate_version)
    except ValueError as exc:
        raise HTTPException(status.HTTP_404_NOT_FOUND, str(exc)) from exc
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(status.HTTP_502_BAD_GATEWAY, f"MLflow error: {exc}") from exc
    if prod_version == body.candidate_version:
        raise HTTPException(
            status.HTTP_409_CONFLICT, "Candidate is already the production version"
        )
    if store.has_pending(dataset, body.candidate_version):
        raise HTTPException(
            status.HTTP_409_CONFLICT,
            "A pending request for this version already exists",
        )
    row = store.create_request(
        dataset=dataset,
        model_name=model_name,
        candidate_version=body.candidate_version,
        current_prod_version=prod_version,
        metrics_snapshot=snapshot,
        requested_by=user.username,
        request_note=body.note,
    )
    return PromotionRequestItem(**row)


@router.get("/promotion-requests", response_model=PromotionRequestListResponse)
def list_promotion_requests(
    status_filter: str | None = Query(default=None, alias="status"),
    store: PromotionStore = Depends(get_promotion_store),
) -> PromotionRequestListResponse:
    rows = store.list_requests(status=status_filter)
    return PromotionRequestListResponse(items=[PromotionRequestItem(**r) for r in rows])


def _load_pending(store: PromotionStore, request_id: int) -> dict:
    row = store.get(request_id)
    if row is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "request not found")
    if row["status"] != "pending":
        raise HTTPException(
            status.HTTP_409_CONFLICT, f"request already {row['status']}"
        )
    return row


@router.post("/promotion-requests/{request_id}/approve", response_model=PromotionRequestItem)
def approve_promotion_request(
    request_id: int,
    body: PromotionReviewBody,
    request: Request,
    user: AuthUser = Depends(require_manager),
    registry: ModelRegistryClient = Depends(get_model_registry),
    store: PromotionStore = Depends(get_promotion_store),
) -> PromotionRequestItem:
    row = _load_pending(store, request_id)
    try:
        existing = {v["version"] for v in registry.list_versions(row["model_name"])}
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(status.HTTP_502_BAD_GATEWAY, f"MLflow error: {exc}") from exc
    if row["candidate_version"] not in existing:
        raise HTTPException(
            status.HTTP_409_CONFLICT, "candidate version no longer exists in MLflow"
        )
    try:
        registry.promote(row["model_name"], row["candidate_version"])
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(
            status.HTTP_502_BAD_GATEWAY, f"MLflow alias update failed: {exc}"
        ) from exc
    cache = getattr(request.app.state, "model_cache", None)
    if cache is not None:
        cache.invalidate(row["dataset"])
    updated = store.mark_reviewed(request_id, "approved", user.username, body.comment)
    return PromotionRequestItem(**(updated or row))


@router.post("/promotion-requests/{request_id}/reject", response_model=PromotionRequestItem)
def reject_promotion_request(
    request_id: int,
    body: PromotionReviewBody,
    user: AuthUser = Depends(require_manager),
    store: PromotionStore = Depends(get_promotion_store),
) -> PromotionRequestItem:
    _load_pending(store, request_id)
    updated = store.mark_reviewed(request_id, "rejected", user.username, body.comment)
    return PromotionRequestItem(**updated)  # type: ignore[arg-type]
