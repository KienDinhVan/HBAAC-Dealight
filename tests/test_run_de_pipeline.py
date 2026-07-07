"""Tests for GCS stage runners using in-memory fakes (no real GCP)."""
from __future__ import annotations

import io
from unittest.mock import MagicMock

import pandas as pd
import pytest

from scripts.run_de_pipeline import (
    CURATED_BLOB,
    QUARANTINE_BLOB,
    RAW_BLOB,
    STAGING_BLOB,
    stage_curated,
    stage_offline_store,
    stage_raw,
    stage_staging,
)


class FakeBlob:
    def __init__(self, store: dict, name: str) -> None:
        self._store = store
        self.name = name

    def exists(self) -> bool:
        return self.name in self._store

    def upload_from_string(self, data, content_type: str | None = None) -> None:
        self._store[self.name] = data if isinstance(data, bytes) else data.encode()

    def download_as_bytes(self) -> bytes:
        return self._store[self.name]


class FakeBucket:
    def __init__(self) -> None:
        self.store: dict[str, bytes] = {}
        self.name = "fake-bucket"

    def blob(self, name: str) -> FakeBlob:
        return FakeBlob(self.store, name)

    def copy_blob(self, blob: FakeBlob, destination_bucket, new_name: str) -> FakeBlob:
        destination_bucket.store[new_name] = self.store[blob.name]
        return FakeBlob(destination_bucket.store, new_name)


VALID_CSV = (
    "Date,Stt,ItemCode,Quantity,UnitPrice,SalesAmount,Unit Cost,Cost Amount\n"
    "2025-01-02,1,SKU-1,2,10.0,20.0,4.0,8.0\n"
    "2025-01-02,2,SKU-1,3,10.0,30.0,4.0,12.0\n"
    "bad-date,3,SKU-2,1,5.0,5.0,2.0,2.0\n"
)


@pytest.fixture
def bucket() -> FakeBucket:
    return FakeBucket()


def test_stage_raw_copies_landing_to_raw(bucket: FakeBucket) -> None:
    bucket.store["landing/b1/train.csv"] = VALID_CSV.encode()
    summary = stage_raw(bucket, "b1", "landing/b1/train.csv")
    assert RAW_BLOB.format(batch_id="b1") in bucket.store
    assert summary["raw_blob"] == "raw/batch_id=b1/train.csv"


def test_stage_raw_fails_on_missing_source(bucket: FakeBucket) -> None:
    with pytest.raises(FileNotFoundError):
        stage_raw(bucket, "b1", "landing/b1/missing.csv")


def test_stage_staging_splits_pass_and_quarantine(bucket: FakeBucket) -> None:
    bucket.store[RAW_BLOB.format(batch_id="b1")] = VALID_CSV.encode()
    summary = stage_staging(bucket, "b1")
    assert summary == {"rows_in": 3, "rows_passed": 2, "rows_rejected": 1}
    staging = pd.read_parquet(io.BytesIO(bucket.store[STAGING_BLOB.format(batch_id="b1")]))
    assert len(staging) == 2
    rejects = pd.read_csv(io.BytesIO(bucket.store[QUARANTINE_BLOB.format(batch_id="b1")]))
    assert rejects["reject_reason"].tolist() == ["invalid_date"]


def test_stage_staging_fails_when_all_rows_rejected(bucket: FakeBucket) -> None:
    all_bad = (
        "Date,Stt,ItemCode,Quantity,UnitPrice,SalesAmount,Unit Cost,Cost Amount\n"
        "bad,1,SKU-1,-2,10.0,20.0,4.0,8.0\n"
    )
    bucket.store[RAW_BLOB.format(batch_id="b1")] = all_bad.encode()
    with pytest.raises(ValueError, match="All rows rejected"):
        stage_staging(bucket, "b1")


def test_stage_curated_writes_aggregated_parquet(bucket: FakeBucket) -> None:
    bucket.store[RAW_BLOB.format(batch_id="b1")] = VALID_CSV.encode()
    stage_staging(bucket, "b1")
    summary = stage_curated(bucket, "b1")
    assert summary["rows"] == 1  # 2 pass rows share (date, SKU-1)
    curated = pd.read_parquet(io.BytesIO(bucket.store[CURATED_BLOB.format(batch_id="b1")]))
    assert curated.iloc[0]["total_quantity"] == 5.0
    assert curated.iloc[0]["batch_id"] == "b1"


def test_stage_offline_store_deletes_batch_then_loads(bucket: FakeBucket) -> None:
    bq = MagicMock()
    bq.load_table_from_uri.return_value.output_rows = 7
    summary = stage_offline_store(
        "b1", project="proj", dataset="dealight", bucket_name="fake-bucket", bq_client=bq
    )
    bq.create_table.assert_called_once()
    delete_sql = bq.query.call_args.args[0]
    assert "DELETE" in delete_sql and "batch_id" in delete_sql
    bq.query.return_value.result.assert_called_once()
    load_uri = bq.load_table_from_uri.call_args.args[0]
    assert load_uri == "gs://fake-bucket/curated/batch_id=b1/sales_daily.parquet"
    assert summary == {"table": "proj.dealight.sales_daily", "loaded_rows": 7}
