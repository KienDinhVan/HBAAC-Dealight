"""Tests for /ingest upload endpoint and GcsUploader (DE GCS pipeline)."""
from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest
from fastapi.testclient import TestClient

from api.app.clients.gcs import GcsUploader
from api.app.main import app


def test_gcs_uploader_uploads_and_returns_uri() -> None:
    uploader = GcsUploader("my-bucket", project="proj")
    fake_client = MagicMock()
    uploader._client = fake_client  # inject to avoid real GCS
    uri = uploader.upload_bytes("landing/b1/train.csv", b"a,b\n1,2\n")
    assert uri == "gs://my-bucket/landing/b1/train.csv"
    fake_client.bucket.assert_called_once_with("my-bucket")
    blob = fake_client.bucket.return_value.blob
    blob.assert_called_once_with("landing/b1/train.csv")
    blob.return_value.upload_from_string.assert_called_once_with(
        b"a,b\n1,2\n", content_type="text/csv"
    )


@pytest.fixture
def client() -> TestClient:
    fake_airflow = AsyncMock()
    fake_airflow.trigger_dag = AsyncMock(
        return_value={"dag_run_id": "manual__20260707T010000Z", "state": "queued"}
    )
    fake_airflow.get_dag_run = AsyncMock(
        return_value={
            "dag_run_id": "manual__20260707T010000Z",
            "state": "success",
            "execution_date": "2026-07-07T01:00:00+00:00",
            "start_date": "2026-07-07T01:00:00+00:00",
            "end_date": "2026-07-07T01:05:00+00:00",
            "note": None,
        }
    )
    fake_gcs = MagicMock()
    fake_gcs.upload_bytes = MagicMock(
        side_effect=lambda blob_name, data, content_type="text/csv": f"gs://test-bucket/{blob_name}"
    )
    app.state.airflow_client = fake_airflow
    app.state.gcs_uploader = fake_gcs
    return TestClient(app)


def _csv_file(name: str = "train.csv", content: bytes = b"Date,ItemCode\n2025-01-02,SKU-1\n"):
    return {"file": (name, content, "text/csv")}


def test_upload_returns_batch_and_dag_run(client: TestClient) -> None:
    resp = client.post("/ingest/upload", files=_csv_file())
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["dag_id"] == "dag_07_de_gcs_pipeline"
    assert body["dag_run_id"] == "manual__20260707T010000Z"
    assert body["source_uri"].startswith("gs://test-bucket/landing/")
    assert body["source_uri"].endswith("/train.csv")
    conf = app.state.airflow_client.trigger_dag.call_args.kwargs["conf"]
    assert conf["batch_id"] == body["batch_id"]
    assert conf["source_blob"] == f"landing/{body['batch_id']}/train.csv"


def test_upload_rejects_non_csv(client: TestClient) -> None:
    resp = client.post("/ingest/upload", files=_csv_file(name="train.xlsx"))
    assert resp.status_code == 400


def test_upload_rejects_empty_file(client: TestClient) -> None:
    resp = client.post("/ingest/upload", files=_csv_file(content=b""))
    assert resp.status_code == 400


def test_upload_returns_503_when_gcs_not_configured(client: TestClient) -> None:
    app.state.gcs_uploader = None
    resp = client.post("/ingest/upload", files=_csv_file())
    assert resp.status_code == 503


def test_upload_airflow_failure_returns_502_with_uri(client: TestClient) -> None:
    app.state.airflow_client.trigger_dag = AsyncMock(side_effect=RuntimeError("airflow down"))
    resp = client.post("/ingest/upload", files=_csv_file())
    assert resp.status_code == 502
    assert "gs://test-bucket/landing/" in resp.text


def test_ingest_run_status(client: TestClient) -> None:
    resp = client.get("/ingest/runs/manual__20260707T010000Z")
    assert resp.status_code == 200
    assert resp.json()["state"] == "success"


def test_upload_gcs_failure_returns_502(client: TestClient) -> None:
    app.state.gcs_uploader.upload_bytes = MagicMock(side_effect=RuntimeError("bucket gone"))
    resp = client.post("/ingest/upload", files=_csv_file())
    assert resp.status_code == 502
    assert "GCS upload failed" in resp.text
