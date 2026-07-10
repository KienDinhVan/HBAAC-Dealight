"""Tests for /ingest upload endpoint and GcsUploader (DE GCS pipeline)."""
from __future__ import annotations

import json
from datetime import datetime, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest
from fastapi.testclient import TestClient

from api.app.clients.gcs import GcsUploader
from api.app.main import app

BATCH_A = "a" * 32
BATCH_B = "b" * 32
SUMMARY_A = {
    "rows_in": 3,
    "rows_passed": 2,
    "rows_rejected": 1,
    "reject_ratio": 0.3333,
    "reject_reasons": {"invalid_date": 1},
}
QUARANTINE_CSV = (
    b"date,stt,item_code,quantity,reject_reason\n"
    b"bad-date,3,SKU-2,1,invalid_date\n"
)


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
    fake_airflow.list_task_instances = AsyncMock(
        return_value=[
            {"task_id": "sync_online_store", "state": None},
            {"task_id": "ingest_raw", "state": "success"},
        ]
    )
    fake_gcs = MagicMock()
    fake_gcs.upload_bytes = MagicMock(
        side_effect=lambda blob_name, data, content_type="text/csv": f"gs://test-bucket/{blob_name}"
    )
    fake_offline = MagicMock()
    fake_offline.batch_stats = MagicMock(
        return_value=[
            {
                "batch_id": "b1",
                "row_count": 7,
                "min_date": "2025-01-01",
                "max_date": "2025-01-02",
                "loaded_at": "2026-01-01T00:00:00",
            }
        ]
    )
    fake_online = MagicMock()
    fake_online.get_item = MagicMock(
        return_value={"date": "2025-01-03", "total_quantity": "2.0", "batch_id": "b1"}
    )
    app.state.airflow_client = fake_airflow
    app.state.gcs_uploader = fake_gcs
    app.state.offline_store = fake_offline
    app.state.online_store = fake_online
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


def test_upload_too_large_returns_413(client: TestClient, monkeypatch) -> None:
    from api.app.config import get_settings

    monkeypatch.setenv("MAX_UPLOAD_BYTES", "10")
    get_settings.cache_clear()
    try:
        resp = client.post("/ingest/upload", files=_csv_file(content=b"x" * 100))
        assert resp.status_code == 413
    finally:
        monkeypatch.delenv("MAX_UPLOAD_BYTES", raising=False)
        get_settings.cache_clear()


# --- New DE-in-UI endpoints ------------------------------------------------


def _fake_blob(batch_id: str, ts: str) -> SimpleNamespace:
    return SimpleNamespace(
        name=f"dq/batch_id={batch_id}/summary.json",
        time_created=datetime.fromisoformat(ts).replace(tzinfo=timezone.utc),
    )


def test_run_tasks_sorted_by_pipeline_order(client: TestClient) -> None:
    resp = client.get("/ingest/runs/manual__20260707T010000Z/tasks")
    assert resp.status_code == 200
    body = resp.json()
    assert body["state"] == "success"
    assert [t["task_id"] for t in body["tasks"]] == ["ingest_raw", "sync_online_store"]


def test_list_batches_returns_newest_first(client: TestClient) -> None:
    app.state.gcs_uploader.list_blobs = MagicMock(
        return_value=[
            _fake_blob(BATCH_A, "2026-01-01T00:00:00"),
            _fake_blob(BATCH_B, "2026-01-02T00:00:00"),
            SimpleNamespace(name="dq/other.txt", time_created=None),
        ]
    )
    app.state.gcs_uploader.download_bytes = MagicMock(
        return_value=json.dumps(SUMMARY_A).encode()
    )
    resp = client.get("/ingest/batches")
    assert resp.status_code == 200
    items = resp.json()["items"]
    assert [i["batch_id"] for i in items] == [BATCH_B, BATCH_A]
    assert items[0]["rows_in"] == 3
    assert items[0]["reject_ratio"] == pytest.approx(0.3333)


def test_batch_dq_returns_summary_and_preview(client: TestClient) -> None:
    def fake_download(blob_name: str, start=None, end=None) -> bytes:
        if blob_name.startswith("dq/"):
            return json.dumps(SUMMARY_A).encode()
        return QUARANTINE_CSV

    app.state.gcs_uploader.download_bytes = MagicMock(side_effect=fake_download)
    resp = client.get(f"/ingest/batches/{BATCH_A}/dq")
    assert resp.status_code == 200
    body = resp.json()
    assert body["summary"]["reject_reasons"] == {"invalid_date": 1}
    assert body["quarantine_preview"] == [
        {
            "date": "bad-date",
            "stt": "3",
            "item_code": "SKU-2",
            "quantity": "1",
            "reject_reason": "invalid_date",
        }
    ]
    assert body["preview_truncated"] is False


def test_batch_dq_rejects_invalid_id(client: TestClient) -> None:
    assert client.get("/ingest/batches/not-a-batch/dq").status_code == 400


def test_offline_store_stats_totals(client: TestClient) -> None:
    resp = client.get("/ingest/offline-store/stats")
    assert resp.status_code == 200
    body = resp.json()
    assert body["total_rows"] == 7
    assert body["batches"][0]["batch_id"] == "b1"


def test_offline_store_stats_passes_as_of(client: TestClient) -> None:
    resp = client.get("/ingest/offline-store/stats?as_of=2026-07-09T18:00:00")
    assert resp.status_code == 200
    as_of = app.state.offline_store.batch_stats.call_args.args[0]
    assert as_of == datetime(2026, 7, 9, 18, 0, 0)


def test_offline_store_stats_503_when_unconfigured(client: TestClient) -> None:
    app.state.offline_store = None
    assert client.get("/ingest/offline-store/stats").status_code == 503


def test_online_store_item_found(client: TestClient) -> None:
    resp = client.get("/ingest/online-store/SKU-1")
    assert resp.status_code == 200
    body = resp.json()
    assert body["found"] is True
    assert body["record"]["date"] == "2025-01-03"


def test_online_store_item_invalid_code(client: TestClient) -> None:
    assert client.get("/ingest/online-store/bad%20code").status_code == 400
