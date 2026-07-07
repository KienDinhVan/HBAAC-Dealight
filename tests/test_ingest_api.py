"""Tests for /ingest upload endpoint and GcsUploader (DE GCS pipeline)."""
from __future__ import annotations

from unittest.mock import MagicMock

from api.app.clients.gcs import GcsUploader


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
