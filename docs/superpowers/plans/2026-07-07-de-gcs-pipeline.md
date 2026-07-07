# DE GCS Pipeline Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Upload CSV qua FastAPI kích hoạt Airflow DAG đưa dữ liệu qua các tầng GCS (raw → staging → curated, dòng lỗi vào quarantine) rồi load vào BigQuery, theo spec `docs/superpowers/specs/2026-07-07-de-gcs-pipeline-design.md`.

**Architecture:** Logic DQ/aggregation thuần pandas trong `src/hbacc_prj/de_pipeline.py` (không IO). Toàn bộ IO GCS/BigQuery trong CLI `scripts/run_de_pipeline.py` chạy theo `--stage`, được DAG `dag_07_de_gcs_pipeline` gọi bằng BashOperator. FastAPI router `/ingest` upload file lên GCS landing rồi trigger DAG qua `AirflowClient` sẵn có.

**Tech Stack:** FastAPI, Airflow 2.10 (BashOperator), pandas + pyarrow, `google-cloud-storage`, `google-cloud-bigquery`, pytest.

## Global Constraints

- Python local `>=3.13`, quản lý bằng `uv` (chạy lệnh qua `uv run ...`). Container Airflow là Python 3.12 với constraint file 2.10.5.
- DAG id: `dag_07_de_gcs_pipeline`. Conf bắt buộc: `batch_id`, `source_blob`.
- Bố cục GCS (verbatim, key theo `batch_id`):
  - `landing/{batch_id}/<original_name>.csv`
  - `raw/batch_id={batch_id}/train.csv`
  - `quarantine/batch_id={batch_id}/rejects.csv`
  - `staging/batch_id={batch_id}/transactions.parquet`
  - `curated/batch_id={batch_id}/sales_daily.parquet`
- Cột CSV bắt buộc: `Date, Stt, ItemCode, Quantity, UnitPrice, SalesAmount, Unit Cost, Cost Amount`.
- DQ row-level: `Date` không parse được → `invalid_date`; `ItemCode` null/rỗng → `missing_item_code`; `Quantity` null hoặc âm → `invalid_quantity`; `UnitPrice` âm → `negative_unit_price`. Thiếu cột hoặc 100% reject → fail.
- Env mới: `GCP_PROJECT_ID`, `GCS_BUCKET`, `BQ_DATASET` (default `dealight`), `GOOGLE_APPLICATION_CREDENTIALS`.
- BigQuery: bảng `<project>.<dataset>.sales_daily`, partition theo `date`; idempotent = `DELETE WHERE batch_id` rồi `WRITE_APPEND`.
- Import `google.cloud.*` phải lazy (bên trong hàm) để unit test không cần cài/kết nối GCP.
- Test không được gọi mạng thật. Chạy test: `uv run pytest <file> -v`.

---

### Task 1: Logic thuần — validate + curated (`de_pipeline.py`)

**Files:**
- Create: `src/hbacc_prj/de_pipeline.py`
- Test: `tests/test_de_pipeline.py`

**Interfaces:**
- Consumes: không có (pandas thuần).
- Produces:
  - `REQUIRED_COLUMNS: tuple[str, ...]`
  - `class SchemaValidationError(ValueError)`
  - `validate_transactions(df: pd.DataFrame) -> ValidationResult` với `ValidationResult(passed: pd.DataFrame, rejected: pd.DataFrame)`; `passed` có cột snake_case đã ép kiểu `date, stt, item_code, quantity, unit_price, sales_amount, unit_cost, cost_amount`; `rejected` giữ cột gốc + `reject_reason` (nhiều lỗi nối bằng `;`).
  - `build_curated(staging_df: pd.DataFrame, batch_id: str) -> pd.DataFrame` với cột `date, item_code, total_quantity, total_sales, total_cost, txn_count, batch_id, loaded_at`.

- [ ] **Step 1: Viết test fail**

Tạo `tests/test_de_pipeline.py`:

```python
"""Tests for DE pipeline pure logic (DE_arch: processing + DQ validation)."""
from __future__ import annotations

import pandas as pd
import pytest

from hbacc_prj.de_pipeline import (
    SchemaValidationError,
    build_curated,
    validate_transactions,
)


def _make_df(**overrides) -> pd.DataFrame:
    base = {
        "Date": ["2025-01-02", "2025-01-02", "2025-01-03"],
        "Stt": [1, 2, 3],
        "ItemCode": ["SKU-1", "SKU-1", "SKU-2"],
        "Quantity": [2, 3, 1],
        "UnitPrice": [10.0, 10.0, 5.0],
        "SalesAmount": [20.0, 30.0, 5.0],
        "Unit Cost": [4.0, 4.0, 2.0],
        "Cost Amount": [8.0, 12.0, 2.0],
    }
    base.update(overrides)
    return pd.DataFrame(base)


def test_missing_column_raises_schema_error() -> None:
    df = _make_df().drop(columns=["ItemCode"])
    with pytest.raises(SchemaValidationError, match="ItemCode"):
        validate_transactions(df)


def test_all_valid_rows_pass_with_typed_snake_case_columns() -> None:
    result = validate_transactions(_make_df())
    assert len(result.passed) == 3
    assert len(result.rejected) == 0
    assert list(result.passed.columns) == [
        "date", "stt", "item_code", "quantity",
        "unit_price", "sales_amount", "unit_cost", "cost_amount",
    ]
    assert str(result.passed["date"].dtype) == "datetime64[ns]"
    assert result.passed["quantity"].tolist() == [2.0, 3.0, 1.0]


def test_bad_rows_are_rejected_with_reasons() -> None:
    df = _make_df(
        Date=["not-a-date", "2025-01-02", "2025-01-03"],
        ItemCode=["SKU-1", "", "SKU-2"],
        Quantity=[2, 3, -1],
    )
    result = validate_transactions(df)
    assert len(result.passed) == 0
    assert len(result.rejected) == 3
    reasons = result.rejected["reject_reason"].tolist()
    assert reasons == ["invalid_date", "missing_item_code", "invalid_quantity"]


def test_multiple_defects_join_reasons_with_semicolon() -> None:
    df = _make_df(
        Date=["not-a-date", "2025-01-02", "2025-01-03"],
        Quantity=[-5, 3, 1],
    )
    result = validate_transactions(df)
    assert result.rejected.iloc[0]["reject_reason"] == "invalid_date;invalid_quantity"
    assert len(result.passed) == 2


def test_negative_unit_price_rejected() -> None:
    df = _make_df(UnitPrice=[10.0, -1.0, 5.0])
    result = validate_transactions(df)
    assert len(result.rejected) == 1
    assert result.rejected.iloc[0]["reject_reason"] == "negative_unit_price"


def test_build_curated_aggregates_by_date_and_item() -> None:
    staging = validate_transactions(_make_df()).passed
    curated = build_curated(staging, batch_id="batch-1")
    assert list(curated.columns) == [
        "date", "item_code", "total_quantity", "total_sales",
        "total_cost", "txn_count", "batch_id", "loaded_at",
    ]
    # SKU-1 has two txns on 2025-01-02 -> aggregated to one row
    assert len(curated) == 2
    sku1 = curated[curated["item_code"] == "SKU-1"].iloc[0]
    assert sku1["total_quantity"] == 5.0
    assert sku1["total_sales"] == 50.0
    assert sku1["total_cost"] == 20.0
    assert sku1["txn_count"] == 2
    assert (curated["batch_id"] == "batch-1").all()
```

- [ ] **Step 2: Chạy test, xác nhận fail**

Run: `uv run pytest tests/test_de_pipeline.py -v`
Expected: FAIL/ERROR với `ModuleNotFoundError: No module named 'hbacc_prj.de_pipeline'`

- [ ] **Step 3: Viết implementation**

Tạo `src/hbacc_prj/de_pipeline.py`:

```python
"""Pure processing + data-quality logic for the DE GCS pipeline (DE_arch.png).

No GCS/BigQuery IO here — IO lives in scripts/run_de_pipeline.py so these
functions stay unit-testable with plain DataFrames.
"""
from __future__ import annotations

from dataclasses import dataclass

import pandas as pd

REQUIRED_COLUMNS: tuple[str, ...] = (
    "Date",
    "Stt",
    "ItemCode",
    "Quantity",
    "UnitPrice",
    "SalesAmount",
    "Unit Cost",
    "Cost Amount",
)

_STAGING_RENAME = {
    "Date": "date",
    "Stt": "stt",
    "ItemCode": "item_code",
    "Quantity": "quantity",
    "UnitPrice": "unit_price",
    "SalesAmount": "sales_amount",
    "Unit Cost": "unit_cost",
    "Cost Amount": "cost_amount",
}


class SchemaValidationError(ValueError):
    """Raised when the uploaded CSV is missing required columns."""


@dataclass(frozen=True)
class ValidationResult:
    passed: pd.DataFrame
    rejected: pd.DataFrame


def validate_transactions(df: pd.DataFrame) -> ValidationResult:
    missing = [column for column in REQUIRED_COLUMNS if column not in df.columns]
    if missing:
        raise SchemaValidationError(f"Missing required columns: {missing}")

    parsed_date = pd.to_datetime(df["Date"], errors="coerce")
    quantity = pd.to_numeric(df["Quantity"], errors="coerce")
    unit_price = pd.to_numeric(df["UnitPrice"], errors="coerce")
    item_code = df["ItemCode"].astype("string").str.strip()

    checks = [
        ("invalid_date", parsed_date.isna()),
        ("missing_item_code", item_code.isna() | (item_code == "")),
        ("invalid_quantity", quantity.isna() | (quantity < 0)),
        ("negative_unit_price", unit_price < 0),
    ]
    reasons = pd.Series([[] for _ in range(len(df))], index=df.index, dtype=object)
    for reason, mask in checks:
        for idx in df.index[mask.fillna(False)]:
            reasons.loc[idx].append(reason)

    bad_mask = reasons.map(len) > 0
    rejected = df.loc[bad_mask].copy()
    rejected["reject_reason"] = reasons.loc[bad_mask].map(";".join)

    passed = df.loc[~bad_mask, list(REQUIRED_COLUMNS)].rename(columns=_STAGING_RENAME)
    passed = passed.assign(
        date=parsed_date.loc[~bad_mask],
        item_code=item_code.loc[~bad_mask],
        quantity=quantity.loc[~bad_mask],
        unit_price=unit_price.loc[~bad_mask],
        sales_amount=pd.to_numeric(passed["sales_amount"], errors="coerce"),
        unit_cost=pd.to_numeric(passed["unit_cost"], errors="coerce"),
        cost_amount=pd.to_numeric(passed["cost_amount"], errors="coerce"),
    ).reset_index(drop=True)
    return ValidationResult(passed=passed, rejected=rejected.reset_index(drop=True))


def build_curated(staging_df: pd.DataFrame, batch_id: str) -> pd.DataFrame:
    curated = (
        staging_df.assign(date=staging_df["date"].dt.date)
        .groupby(["date", "item_code"], as_index=False)
        .agg(
            total_quantity=("quantity", "sum"),
            total_sales=("sales_amount", "sum"),
            total_cost=("cost_amount", "sum"),
            txn_count=("quantity", "size"),
        )
        .sort_values(["date", "item_code"])
        .reset_index(drop=True)
    )
    curated["batch_id"] = batch_id
    curated["loaded_at"] = pd.Timestamp.now(tz="UTC")
    return curated
```

- [ ] **Step 4: Chạy test, xác nhận pass**

Run: `uv run pytest tests/test_de_pipeline.py -v`
Expected: 6 test PASS

- [ ] **Step 5: Commit**

```bash
git add src/hbacc_prj/de_pipeline.py tests/test_de_pipeline.py
git commit -m "feat: DE pipeline pure logic - DQ validation + curated aggregation"
```

---

### Task 2: CLI theo stage với IO GCS/BigQuery (`run_de_pipeline.py`)

**Files:**
- Modify: `pyproject.toml` (thêm dependencies)
- Create: `scripts/run_de_pipeline.py`
- Test: `tests/test_run_de_pipeline.py`

**Interfaces:**
- Consumes: `validate_transactions`, `build_curated` từ Task 1.
- Produces (được DAG Task 5 gọi qua CLI, test gọi trực tiếp):
  - Blob templates: `RAW_BLOB = "raw/batch_id={batch_id}/train.csv"`, `STAGING_BLOB = "staging/batch_id={batch_id}/transactions.parquet"`, `QUARANTINE_BLOB = "quarantine/batch_id={batch_id}/rejects.csv"`, `CURATED_BLOB = "curated/batch_id={batch_id}/sales_daily.parquet"`
  - `stage_raw(bucket, batch_id: str, source_blob: str) -> dict`
  - `stage_staging(bucket, batch_id: str) -> dict`
  - `stage_curated(bucket, batch_id: str) -> dict`
  - `stage_offline_store(batch_id: str, *, project: str, dataset: str, bucket_name: str, bq_client=None) -> dict`
  - CLI: `python -m scripts.run_de_pipeline --stage raw|staging|curated|offline_store --batch-id X [--source-blob Y]`
  - Tham số `bucket` là object có interface `google.cloud.storage.Bucket` (test dùng fake).

- [ ] **Step 1: Thêm dependencies GCP**

```bash
uv add "google-cloud-storage>=2.16" "google-cloud-bigquery>=3.25"
```

Expected: `pyproject.toml` có 2 dòng mới trong `dependencies`, `uv.lock` cập nhật.

- [ ] **Step 2: Viết test fail**

Tạo `tests/test_run_de_pipeline.py`:

```python
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
    load_uri = bq.load_table_from_uri.call_args.args[0]
    assert load_uri == "gs://fake-bucket/curated/batch_id=b1/sales_daily.parquet"
    assert summary == {"table": "proj.dealight.sales_daily", "loaded_rows": 7}
```

- [ ] **Step 3: Chạy test, xác nhận fail**

Run: `uv run pytest tests/test_run_de_pipeline.py -v`
Expected: FAIL với `ModuleNotFoundError: No module named 'scripts.run_de_pipeline'`

- [ ] **Step 4: Viết implementation**

Tạo `scripts/run_de_pipeline.py`:

```python
"""Stage runner for the DE GCS pipeline (DE_arch.png).

Each --stage moves one batch through the GCS layers:
raw -> staging (+quarantine) -> curated -> offline_store (BigQuery).
All google.cloud imports are lazy so unit tests run without GCP credentials.
"""
from __future__ import annotations

import argparse
import io
import json
import os

import pandas as pd

from hbacc_prj.de_pipeline import build_curated, validate_transactions

RAW_BLOB = "raw/batch_id={batch_id}/train.csv"
STAGING_BLOB = "staging/batch_id={batch_id}/transactions.parquet"
QUARANTINE_BLOB = "quarantine/batch_id={batch_id}/rejects.csv"
CURATED_BLOB = "curated/batch_id={batch_id}/sales_daily.parquet"

BQ_TABLE_NAME = "sales_daily"


def stage_raw(bucket, batch_id: str, source_blob: str) -> dict:
    source = bucket.blob(source_blob)
    if not source_blob or not source.exists():
        raise FileNotFoundError(f"Source blob not found: {source_blob!r}")
    raw_name = RAW_BLOB.format(batch_id=batch_id)
    bucket.copy_blob(source, bucket, raw_name)
    return {"raw_blob": raw_name}


def stage_staging(bucket, batch_id: str) -> dict:
    raw_bytes = bucket.blob(RAW_BLOB.format(batch_id=batch_id)).download_as_bytes()
    df = pd.read_csv(io.BytesIO(raw_bytes))
    result = validate_transactions(df)

    if len(result.rejected) > 0:
        bucket.blob(QUARANTINE_BLOB.format(batch_id=batch_id)).upload_from_string(
            result.rejected.to_csv(index=False), content_type="text/csv"
        )
    if len(result.passed) == 0:
        raise ValueError(f"All rows rejected for batch {batch_id} — see quarantine/")

    buffer = io.BytesIO()
    result.passed.to_parquet(buffer, index=False)
    bucket.blob(STAGING_BLOB.format(batch_id=batch_id)).upload_from_string(
        buffer.getvalue(), content_type="application/octet-stream"
    )
    return {
        "rows_in": int(len(df)),
        "rows_passed": int(len(result.passed)),
        "rows_rejected": int(len(result.rejected)),
    }


def stage_curated(bucket, batch_id: str) -> dict:
    staging_bytes = bucket.blob(STAGING_BLOB.format(batch_id=batch_id)).download_as_bytes()
    staging = pd.read_parquet(io.BytesIO(staging_bytes))
    curated = build_curated(staging, batch_id=batch_id)
    buffer = io.BytesIO()
    curated.to_parquet(buffer, index=False)
    bucket.blob(CURATED_BLOB.format(batch_id=batch_id)).upload_from_string(
        buffer.getvalue(), content_type="application/octet-stream"
    )
    return {"rows": int(len(curated))}


def stage_offline_store(
    batch_id: str, *, project: str, dataset: str, bucket_name: str, bq_client=None
) -> dict:
    from google.cloud import bigquery

    client = bq_client or bigquery.Client(project=project)
    table_id = f"{project}.{dataset}.{BQ_TABLE_NAME}"
    schema = [
        bigquery.SchemaField("date", "DATE"),
        bigquery.SchemaField("item_code", "STRING"),
        bigquery.SchemaField("total_quantity", "FLOAT64"),
        bigquery.SchemaField("total_sales", "FLOAT64"),
        bigquery.SchemaField("total_cost", "FLOAT64"),
        bigquery.SchemaField("txn_count", "INT64"),
        bigquery.SchemaField("batch_id", "STRING"),
        bigquery.SchemaField("loaded_at", "TIMESTAMP"),
    ]
    table = bigquery.Table(table_id, schema=schema)
    table.time_partitioning = bigquery.TimePartitioning(field="date")
    client.create_table(table, exists_ok=True)

    client.query(
        f"DELETE FROM `{table_id}` WHERE batch_id = @batch_id",
        job_config=bigquery.QueryJobConfig(
            query_parameters=[
                bigquery.ScalarQueryParameter("batch_id", "STRING", batch_id)
            ]
        ),
    ).result()

    uri = f"gs://{bucket_name}/" + CURATED_BLOB.format(batch_id=batch_id)
    load_job = client.load_table_from_uri(
        uri,
        table_id,
        job_config=bigquery.LoadJobConfig(
            source_format=bigquery.SourceFormat.PARQUET,
            write_disposition=bigquery.WriteDisposition.WRITE_APPEND,
        ),
    )
    load_job.result()
    return {"table": table_id, "loaded_rows": int(load_job.output_rows or 0)}


def _gcs_bucket(bucket_name: str, project: str):
    from google.cloud import storage

    return storage.Client(project=project or None).bucket(bucket_name)


def main() -> None:
    parser = argparse.ArgumentParser(description="Run one DE GCS pipeline stage.")
    parser.add_argument(
        "--stage",
        required=True,
        choices=["raw", "staging", "curated", "offline_store"],
    )
    parser.add_argument("--batch-id", required=True)
    parser.add_argument("--source-blob", default="")
    parser.add_argument("--bucket", default=os.environ.get("GCS_BUCKET", ""))
    parser.add_argument("--project", default=os.environ.get("GCP_PROJECT_ID", ""))
    parser.add_argument("--dataset", default=os.environ.get("BQ_DATASET", "dealight"))
    args = parser.parse_args()

    if not args.bucket:
        raise SystemExit("GCS_BUCKET (or --bucket) is required")

    if args.stage == "offline_store":
        if not args.project:
            raise SystemExit("GCP_PROJECT_ID (or --project) is required for offline_store")
        summary = stage_offline_store(
            args.batch_id,
            project=args.project,
            dataset=args.dataset,
            bucket_name=args.bucket,
        )
    else:
        bucket = _gcs_bucket(args.bucket, args.project)
        if args.stage == "raw":
            summary = stage_raw(bucket, args.batch_id, args.source_blob)
        elif args.stage == "staging":
            summary = stage_staging(bucket, args.batch_id)
        else:
            summary = stage_curated(bucket, args.batch_id)

    print(json.dumps({"stage": args.stage, "batch_id": args.batch_id, **summary}, default=str))


if __name__ == "__main__":
    main()
```

- [ ] **Step 5: Chạy test, xác nhận pass**

Run: `uv run pytest tests/test_run_de_pipeline.py tests/test_de_pipeline.py -v`
Expected: tất cả PASS

- [ ] **Step 6: Commit**

```bash
git add pyproject.toml uv.lock scripts/run_de_pipeline.py tests/test_run_de_pipeline.py
git commit -m "feat: DE pipeline stage runner with GCS/BigQuery IO"
```

---

### Task 3: Config + GCS uploader cho API

**Files:**
- Modify: `api/app/config.py` (thêm 3 field vào `Settings` + 3 dòng vào `get_settings`)
- Create: `api/app/clients/gcs.py`
- Test: `tests/test_ingest_api.py` (phần uploader; file sẽ được Task 4 mở rộng)

**Interfaces:**
- Consumes: không có.
- Produces:
  - `Settings.gcp_project_id: str = ""`, `Settings.gcs_bucket: str = ""`, `Settings.bq_dataset: str = "dealight"` (env: `GCP_PROJECT_ID`, `GCS_BUCKET`, `BQ_DATASET`)
  - `class GcsUploader: __init__(self, bucket_name: str, project: str | None = None)`; `upload_bytes(self, blob_name: str, data: bytes, content_type: str = "text/csv") -> str` trả về `gs://<bucket>/<blob_name>`. Client GCS khởi tạo lazy trong lần upload đầu.

- [ ] **Step 1: Viết test fail**

Tạo `tests/test_ingest_api.py`:

```python
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
```

- [ ] **Step 2: Chạy test, xác nhận fail**

Run: `uv run pytest tests/test_ingest_api.py -v`
Expected: FAIL với `ModuleNotFoundError: No module named 'api.app.clients.gcs'`

- [ ] **Step 3: Viết implementation**

Tạo `api/app/clients/gcs.py`:

```python
from __future__ import annotations


class GcsUploader:
    """Thin wrapper around google-cloud-storage for landing-zone uploads.

    The storage client is created lazily so app startup and unit tests never
    touch the network.
    """

    def __init__(self, bucket_name: str, project: str | None = None) -> None:
        self._bucket_name = bucket_name
        self._project = project
        self._client = None

    def upload_bytes(
        self, blob_name: str, data: bytes, content_type: str = "text/csv"
    ) -> str:
        if self._client is None:
            from google.cloud import storage

            self._client = storage.Client(project=self._project)
        blob = self._client.bucket(self._bucket_name).blob(blob_name)
        blob.upload_from_string(data, content_type=content_type)
        return f"gs://{self._bucket_name}/{blob_name}"
```

Sửa `api/app/config.py` — thêm vào cuối class `Settings` (sau `enable_agents`):

```python
    # --- DE GCS pipeline (DE_arch) ---
    gcp_project_id: str = ""
    gcs_bucket: str = ""
    bq_dataset: str = "dealight"
```

và thêm vào lời gọi `Settings(...)` trong `get_settings` (sau dòng `enable_agents=...`):

```python
        gcp_project_id=os.getenv("GCP_PROJECT_ID", defaults.gcp_project_id),
        gcs_bucket=os.getenv("GCS_BUCKET", defaults.gcs_bucket),
        bq_dataset=os.getenv("BQ_DATASET", defaults.bq_dataset),
```

- [ ] **Step 4: Chạy test, xác nhận pass**

Run: `uv run pytest tests/test_ingest_api.py -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add api/app/clients/gcs.py api/app/config.py tests/test_ingest_api.py
git commit -m "feat: GcsUploader client + GCP settings"
```

---

### Task 4: Router `/ingest` — upload CSV, trigger DAG

**Files:**
- Create: `api/app/routers/ingest.py`
- Modify: `api/app/schemas.py` (thêm 2 model cuối file)
- Modify: `api/app/deps.py` (thêm `get_gcs_uploader`)
- Modify: `api/app/main.py` (import router + include + khởi tạo `app.state.gcs_uploader` trong `_lifespan`)
- Test: `tests/test_ingest_api.py` (mở rộng)

**Interfaces:**
- Consumes: `GcsUploader` (Task 3), `AirflowClient.trigger_dag/get_dag_run` (sẵn có), `get_airflow_client` (sẵn có).
- Produces:
  - `POST /ingest/upload` (multipart field `file`) → `IngestUploadResponse(batch_id, source_uri, dag_id, dag_run_id, state)`
  - `GET /ingest/runs/{dag_run_id}` → `IngestRunStatusResponse(dag_id, dag_run_id, state, execution_date, start_date, end_date, note)`
  - `INGEST_DAG_ID = "dag_07_de_gcs_pipeline"` (Task 5 phải dùng đúng id này)
  - `api.app.deps.get_gcs_uploader(request) -> GcsUploader | None` đọc `request.app.state.gcs_uploader`

- [ ] **Step 1: Viết test fail**

Thêm vào `tests/test_ingest_api.py` (gộp import `MagicMock`/`AsyncMock` lên đầu file):

```python
import pytest
from fastapi.testclient import TestClient
from unittest.mock import AsyncMock

from api.app.main import app


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
```

- [ ] **Step 2: Chạy test, xác nhận fail**

Run: `uv run pytest tests/test_ingest_api.py -v`
Expected: các test mới FAIL 404 (router chưa tồn tại)

- [ ] **Step 3: Viết implementation**

Tạo `api/app/routers/ingest.py`:

```python
from __future__ import annotations

import logging
from pathlib import Path
from uuid import uuid4

from fastapi import APIRouter, Depends, File, HTTPException, UploadFile, status
from starlette.concurrency import run_in_threadpool

from api.app.clients.airflow import AirflowClient
from api.app.clients.gcs import GcsUploader
from api.app.deps import get_airflow_client, get_gcs_uploader
from api.app.schemas import IngestRunStatusResponse, IngestUploadResponse

INGEST_DAG_ID = "dag_07_de_gcs_pipeline"

router = APIRouter(prefix="/ingest", tags=["ingest"])
_logger = logging.getLogger(__name__)


@router.post("/upload", response_model=IngestUploadResponse)
async def upload(
    file: UploadFile = File(...),
    airflow: AirflowClient = Depends(get_airflow_client),
    gcs: GcsUploader | None = Depends(get_gcs_uploader),
) -> IngestUploadResponse:
    if gcs is None:
        raise HTTPException(
            status.HTTP_503_SERVICE_UNAVAILABLE,
            "GCS is not configured (set GCS_BUCKET / GOOGLE_APPLICATION_CREDENTIALS)",
        )
    filename = Path(file.filename or "").name
    if not filename.lower().endswith(".csv"):
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "Only .csv files are accepted")
    data = await file.read()
    if not data:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "Uploaded file is empty")

    batch_id = uuid4().hex
    source_blob = f"landing/{batch_id}/{filename}"
    try:
        source_uri = await run_in_threadpool(gcs.upload_bytes, source_blob, data)
    except Exception as exc:  # noqa: BLE001
        _logger.exception("GCS landing upload failed")
        raise HTTPException(
            status.HTTP_502_BAD_GATEWAY, f"GCS upload failed: {exc}"
        ) from exc

    try:
        result = await airflow.trigger_dag(
            INGEST_DAG_ID,
            conf={"batch_id": batch_id, "source_blob": source_blob},
            note=f"CSV upload: {filename}",
        )
    except Exception as exc:  # noqa: BLE001
        _logger.exception("Airflow trigger failed after upload to %s", source_uri)
        raise HTTPException(
            status.HTTP_502_BAD_GATEWAY,
            f"File uploaded to {source_uri} but Airflow trigger failed: {exc}",
        ) from exc

    return IngestUploadResponse(
        batch_id=batch_id,
        source_uri=source_uri,
        dag_id=INGEST_DAG_ID,
        dag_run_id=result.get("dag_run_id", ""),
        state=result.get("state"),
    )


@router.get("/runs/{dag_run_id}", response_model=IngestRunStatusResponse)
async def run_status(
    dag_run_id: str,
    airflow: AirflowClient = Depends(get_airflow_client),
) -> IngestRunStatusResponse:
    try:
        run = await airflow.get_dag_run(INGEST_DAG_ID, dag_run_id)
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(
            status.HTTP_502_BAD_GATEWAY, f"Airflow query failed: {exc}"
        ) from exc
    return IngestRunStatusResponse(
        dag_id=INGEST_DAG_ID,
        dag_run_id=run.get("dag_run_id", dag_run_id),
        state=run.get("state"),
        execution_date=run.get("execution_date"),
        start_date=run.get("start_date"),
        end_date=run.get("end_date"),
        note=run.get("note"),
    )
```

Thêm cuối `api/app/schemas.py`:

```python
class IngestUploadResponse(BaseModel):
    batch_id: str
    source_uri: str
    dag_id: str
    dag_run_id: str
    state: str | None = None


class IngestRunStatusResponse(BaseModel):
    dag_id: str
    dag_run_id: str
    state: str | None = None
    execution_date: datetime | None = None
    start_date: datetime | None = None
    end_date: datetime | None = None
    note: str | None = None
```

Thêm vào `api/app/deps.py`:

```python
from api.app.clients.gcs import GcsUploader


def get_gcs_uploader(request: Request) -> GcsUploader | None:
    return getattr(request.app.state, "gcs_uploader", None)
```

Sửa `api/app/main.py`:
1. Thêm import: `from api.app.clients.gcs import GcsUploader` và `from api.app.routers import ingest as ingest_router_module`.
2. Trong `_lifespan`, ngay sau khối `app.state.airflow_client = AirflowClient(...)`:

```python
    app.state.gcs_uploader = (
        GcsUploader(settings.gcs_bucket, project=settings.gcp_project_id or None)
        if settings.gcs_bucket
        else None
    )
```

3. Sau `app.include_router(retrain_router_module.router)`:

```python
app.include_router(ingest_router_module.router)
```

- [ ] **Step 4: Chạy test, xác nhận pass**

Run: `uv run pytest tests/test_ingest_api.py -v`
Expected: 7 test PASS

- [ ] **Step 5: Chạy toàn bộ test suite tránh regression**

Run: `uv run pytest tests/ -v`
Expected: PASS (các test sẵn có không đổi)

- [ ] **Step 6: Commit**

```bash
git add api/app/routers/ingest.py api/app/schemas.py api/app/deps.py api/app/main.py tests/test_ingest_api.py
git commit -m "feat: /ingest/upload endpoint - CSV to GCS landing + trigger DAG"
```

---

### Task 5: DAG `dag_07_de_gcs_pipeline`

**Files:**
- Create: `dags/dag_07_de_gcs_pipeline.py`

**Interfaces:**
- Consumes: CLI `scripts.run_de_pipeline` (Task 2), conf `{batch_id, source_blob}` từ endpoint (Task 4).
- Produces: DAG id `dag_07_de_gcs_pipeline` với 4 task: `ingest_raw` → `process_validate_to_staging` → `build_curated` → `load_offline_store`.

- [ ] **Step 1: Viết DAG**

Tạo `dags/dag_07_de_gcs_pipeline.py`:

```python
from __future__ import annotations

from datetime import datetime

from airflow import DAG
from airflow.operators.bash import BashOperator

PIPELINE_COMMAND = (
    "python -m scripts.run_de_pipeline "
    "--batch-id '{{ dag_run.conf[\"batch_id\"] }}' "
    "--source-blob '{{ dag_run.conf.get(\"source_blob\", \"\") }}' "
)


def pipeline_task(task_id: str, stage: str) -> BashOperator:
    return BashOperator(
        task_id=task_id,
        bash_command=PIPELINE_COMMAND + f"--stage {stage}",
        retries=1,
    )


with DAG(
    dag_id="dag_07_de_gcs_pipeline",
    start_date=datetime(2026, 1, 1),
    schedule=None,
    catchup=False,
    tags=["pipeline", "de-arch", "gcp"],
) as dag:
    ingest_raw = pipeline_task("ingest_raw", "raw")
    process_validate_to_staging = pipeline_task("process_validate_to_staging", "staging")
    build_curated = pipeline_task("build_curated", "curated")
    load_offline_store = pipeline_task("load_offline_store", "offline_store")

    ingest_raw >> process_validate_to_staging >> build_curated >> load_offline_store
```

- [ ] **Step 2: Kiểm tra syntax**

Run: `uv run python -m py_compile dags/dag_07_de_gcs_pipeline.py && echo OK`
Expected: `OK` (airflow không cài ở local — chỉ check syntax; DAG import thật được verify ở smoke test Task 7)

- [ ] **Step 3: Commit**

```bash
git add dags/dag_07_de_gcs_pipeline.py
git commit -m "feat: dag_07 - GCS layered pipeline triggered by CSV upload"
```

---

### Task 6: Wiring hạ tầng — compose, Airflow image, env, gitignore

**Files:**
- Modify: `infra/docker-compose.yml` (env + volume cho `x-airflow-common` và `forecast-api`)
- Modify: `infra/airflow/Dockerfile` (thêm pip packages)
- Modify: `.env.example` (thêm block GCP)
- Modify: `.gitignore` (thêm `infra/secrets/`)

**Interfaces:**
- Consumes: env names từ Task 2/3 (`GCP_PROJECT_ID`, `GCS_BUCKET`, `BQ_DATASET`, `GOOGLE_APPLICATION_CREDENTIALS`).
- Produces: key file mount tại `/opt/project/secrets/gcp-key.json` trong cả container Airflow lẫn API (Task 7 hướng dẫn user đặt key tại `infra/secrets/gcp-key.json`).

- [ ] **Step 1: Sửa `infra/docker-compose.yml`**

Trong `x-airflow-common` → `environment: &airflow-environment`, thêm sau dòng `PYTHONPATH: /opt/project/src:/opt/project`:

```yaml
    GCP_PROJECT_ID: ${GCP_PROJECT_ID:-}
    GCS_BUCKET: ${GCS_BUCKET:-}
    BQ_DATASET: ${BQ_DATASET:-dealight}
    GOOGLE_APPLICATION_CREDENTIALS: /opt/project/secrets/gcp-key.json
```

Trong `x-airflow-common` → `volumes:`, thêm cuối danh sách:

```yaml
    - ./secrets:/opt/project/secrets:ro
```

Trong service `forecast-api` → `environment:`, thêm sau dòng `CORS_ORIGINS: ...`:

```yaml
      GCP_PROJECT_ID: ${GCP_PROJECT_ID:-}
      GCS_BUCKET: ${GCS_BUCKET:-}
      BQ_DATASET: ${BQ_DATASET:-dealight}
      GOOGLE_APPLICATION_CREDENTIALS: /opt/project/secrets/gcp-key.json
```

Trong service `forecast-api` → `volumes:`, thêm cuối danh sách:

```yaml
      - ./secrets:/opt/project/secrets:ro
```

- [ ] **Step 2: Sửa `infra/airflow/Dockerfile`**

Trong lệnh `RUN pip install --no-cache-dir --constraint "${AIRFLOW_CONSTRAINTS}" \`, thêm 3 dòng sau `"boto3" \`:

```dockerfile
    "google-cloud-storage" \
    "google-cloud-bigquery" \
    "pyarrow" \
```

- [ ] **Step 3: Sửa `.env.example`**

Thêm cuối file:

```bash

# --- DE GCS pipeline (DE_arch) — upload CSV -> Airflow -> GCS -> BigQuery ---
# Run scripts/setup_gcp.sh to create these resources and the key file.
GCP_PROJECT_ID=
GCS_BUCKET=
BQ_DATASET=dealight
# Key file must exist at infra/secrets/gcp-key.json (mounted read-only into containers).
```

- [ ] **Step 4: Sửa `.gitignore`**

Thêm cuối file:

```
# GCP service account keys — never commit
infra/secrets/
```

- [ ] **Step 5: Validate compose config**

Run: `mkdir -p infra/secrets && docker compose -f infra/docker-compose.yml config --quiet && echo COMPOSE_OK`
Expected: `COMPOSE_OK`

- [ ] **Step 6: Commit**

```bash
git add infra/docker-compose.yml infra/airflow/Dockerfile .env.example .gitignore
git commit -m "chore: wire GCP env, key mount and deps into compose + airflow image"
```

---

### Task 7: Script setup GCP + runbook

**Files:**
- Create: `scripts/setup_gcp.sh`
- Create: `docs/DE_GCS_PIPELINE.md`

**Interfaces:**
- Consumes: env names + đường dẫn key `infra/secrets/gcp-key.json` (Task 6).
- Produces: bucket + dataset + service account + key file; tài liệu vận hành + smoke test.

- [ ] **Step 1: Viết `scripts/setup_gcp.sh`**

```bash
#!/usr/bin/env bash
# One-time GCP setup for the DE GCS pipeline (DE_arch.png).
# Usage: scripts/setup_gcp.sh <project-id> [bucket-name]
set -euo pipefail

PROJECT_ID="${1:?Usage: scripts/setup_gcp.sh <project-id> [bucket-name]}"
BUCKET="${2:-${PROJECT_ID}-dealight-data}"
REGION="asia-southeast1"
DATASET="dealight"
SA_NAME="dealight-pipeline"
SA_EMAIL="${SA_NAME}@${PROJECT_ID}.iam.gserviceaccount.com"
KEY_PATH="infra/secrets/gcp-key.json"

echo ">> Enabling APIs"
gcloud services enable storage.googleapis.com bigquery.googleapis.com \
  --project "${PROJECT_ID}"

echo ">> Creating service account ${SA_EMAIL} (idempotent)"
gcloud iam service-accounts create "${SA_NAME}" \
  --project "${PROJECT_ID}" \
  --display-name "Dealight DE pipeline" 2>/dev/null || true

echo ">> Creating bucket gs://${BUCKET} (idempotent)"
gcloud storage buckets create "gs://${BUCKET}" \
  --project "${PROJECT_ID}" \
  --location "${REGION}" \
  --uniform-bucket-level-access 2>/dev/null || true

echo ">> Creating BigQuery dataset ${DATASET} (idempotent)"
bq --project_id "${PROJECT_ID}" --location "${REGION}" mk --dataset \
  "${PROJECT_ID}:${DATASET}" 2>/dev/null || true

echo ">> Granting roles"
gcloud storage buckets add-iam-policy-binding "gs://${BUCKET}" \
  --member "serviceAccount:${SA_EMAIL}" \
  --role roles/storage.objectAdmin
gcloud projects add-iam-policy-binding "${PROJECT_ID}" \
  --member "serviceAccount:${SA_EMAIL}" \
  --role roles/bigquery.dataEditor --condition=None
gcloud projects add-iam-policy-binding "${PROJECT_ID}" \
  --member "serviceAccount:${SA_EMAIL}" \
  --role roles/bigquery.jobUser --condition=None

echo ">> Creating key at ${KEY_PATH}"
mkdir -p "$(dirname "${KEY_PATH}")"
gcloud iam service-accounts keys create "${KEY_PATH}" \
  --iam-account "${SA_EMAIL}"

cat <<EOF

Done. Add to your .env:
  GCP_PROJECT_ID=${PROJECT_ID}
  GCS_BUCKET=${BUCKET}
  BQ_DATASET=${DATASET}

Then rebuild + restart:
  docker compose -f infra/docker-compose.yml up -d --build forecast-api airflow-webserver airflow-scheduler
EOF
```

Run: `chmod +x scripts/setup_gcp.sh && bash -n scripts/setup_gcp.sh && echo SYNTAX_OK`
Expected: `SYNTAX_OK`

- [ ] **Step 2: Viết `docs/DE_GCS_PIPELINE.md`**

````markdown
# DE GCS Pipeline — Vận hành

Triển khai theo sơ đồ `DE_arch.png` (spec: `docs/superpowers/specs/2026-07-07-de-gcs-pipeline-design.md`).

## Luồng

`POST /ingest/upload` (CSV) → GCS `landing/` → trigger `dag_07_de_gcs_pipeline`:
`ingest_raw` (→ `raw/`) → `process_validate_to_staging` (fail → `quarantine/`, pass → `staging/`)
→ `build_curated` (→ `curated/`) → `load_offline_store` (→ BigQuery `dealight.sales_daily`).

## Setup một lần

1. `gcloud auth login` với tài khoản có quyền Owner/Editor trên project.
2. `scripts/setup_gcp.sh <project-id>` — tạo bucket, dataset, service account, key
   tại `infra/secrets/gcp-key.json` (đã gitignore).
3. Điền `GCP_PROJECT_ID`, `GCS_BUCKET`, `BQ_DATASET` vào `.env`.
4. `docker compose -f infra/docker-compose.yml up -d --build forecast-api airflow-webserver airflow-scheduler`

## Smoke test

```bash
curl -F "file=@data/raw/train.csv" http://localhost:8000/ingest/upload
# -> {"batch_id": "...", "dag_run_id": "manual__...", ...}
curl http://localhost:8000/ingest/runs/<dag_run_id>
gcloud storage ls "gs://$GCS_BUCKET/**" | head
bq query --use_legacy_sql=false \
  'SELECT batch_id, COUNT(*) AS rows FROM `'"$GCP_PROJECT_ID"'.dealight.sales_daily` GROUP BY batch_id'
```

## Chạy lại một batch (idempotent)

Trigger lại DAG với cùng conf (`batch_id`, `source_blob`) từ Airflow UI —
mọi tầng GCS ghi đè theo `batch_id`, BigQuery DELETE batch cũ trước khi append.
````

- [ ] **Step 3: Commit**

```bash
git add scripts/setup_gcp.sh docs/DE_GCS_PIPELINE.md
git commit -m "docs: GCP setup script + DE pipeline runbook"
```

---

## Verification cuối (sau tất cả task)

- [ ] `uv run pytest tests/ -v` — toàn bộ pass
- [ ] `uv run ruff check src/hbacc_prj/de_pipeline.py scripts/run_de_pipeline.py api/app/routers/ingest.py api/app/clients/gcs.py dags/dag_07_de_gcs_pipeline.py` — sạch
- [ ] Smoke test thật theo `docs/DE_GCS_PIPELINE.md` (cần user chạy `setup_gcp.sh` trước vì cần `gcloud auth login`)
