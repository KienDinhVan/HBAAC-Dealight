# DE Pipeline trên GCP — Upload CSV kích hoạt Airflow DAG (theo DE_arch.png)

**Ngày:** 2026-07-07
**Trạng thái:** Đã duyệt (brainstorming với user)
**Phạm vi:** Core pipeline — Upload → Raw → Processing/DQ → Quarantine → Staging → Curated → Offline store (BigQuery). Online store (MemoryStore) và Backfill/Iceberg để giai đoạn sau.

## Bối cảnh & quyết định

- Môi trường: **GCP thật**. User đã có project, **chưa có service account key** → cần script setup gcloud.
- Trigger: **FastAPI endpoint** `POST /ingest/upload` (theo pattern `/retrain/trigger` hiện có), không dùng GCS event-driven.
- Approach: **Hướng A** — script Python theo stage (pattern `scripts/run_data_pipeline.py`) + DAG mới + client GCS/BigQuery trực tiếp. Không dùng `apache-airflow-providers-google` operators, không dùng Cloud Functions.

## Luồng dữ liệu

```
Frontend/curl ──POST /ingest/upload──► FastAPI
                                        │ 1. upload CSV → gs://<GCS_BUCKET>/landing/{batch_id}/
                                        │ 2. AirflowClient.trigger_dag("dag_07_de_gcs_pipeline",
                                        │       conf={batch_id, source_blob})
                                        ▼
dag_07:  ingest_raw ─► process_validate_to_staging ─► build_curated ─► load_offline_store
            │               │ fail rows      │ pass rows                     │
            ▼               ▼                ▼                               ▼
       raw/ (GCS)     quarantine/ (GCS)  staging/ (GCS)          BigQuery dealight.sales_daily
```

Ghi chú: validation và ghi staging là **một** task (`process_validate_to_staging`) — dòng fail vào quarantine, dòng pass ghi parquet vào staging trong cùng một lần đọc raw, tránh phải validate hai lần.

## Bố cục GCS

Một bucket duy nhất, tách tầng bằng prefix; mọi path key theo `batch_id` (UUID sinh lúc upload):

- `landing/{batch_id}/<original_name>.csv` — file gốc user upload
- `raw/batch_id={batch_id}/train.csv` — bản sao bất biến
- `quarantine/batch_id={batch_id}/rejects.csv` — dòng lỗi + cột `reject_reason`
- `staging/batch_id={batch_id}/transactions.parquet` — dữ liệu sạch, đã ép kiểu
- `curated/batch_id={batch_id}/sales_daily.parquet` — tổng hợp ngày × SKU

## Data Quality rules

Schema đầu vào: `Date, Stt, ItemCode, Quantity, UnitPrice, SalesAmount, Unit Cost, Cost Amount`.

- **Batch-level (fail cả DAG):** thiếu cột bắt buộc; 100% dòng bị reject.
- **Row-level (đưa vào quarantine kèm `reject_reason`):**
  - `Date` không parse được hoặc null
  - `ItemCode` null/rỗng
  - `Quantity` null hoặc âm
  - `UnitPrice` âm

Dòng pass được ép kiểu (date, numeric) và ghi parquet vào staging.

## Curated & Offline store

- Curated: aggregate staging theo `(date, item_code)` → `total_quantity, total_sales, total_cost, txn_count`.
- BigQuery: dataset `dealight` (env `BQ_DATASET`), bảng `sales_daily`, partition theo cột `date`, có cột `batch_id` và `loaded_at`.
- **Idempotency:** load = `DELETE WHERE batch_id = X` rồi append; mọi path GCS ghi đè theo `batch_id` → re-run DAG cùng batch an toàn, không nhân đôi dữ liệu.

## Thành phần code

| Thành phần | File | Vai trò |
|---|---|---|
| Logic thuần | `src/hbacc_prj/de_pipeline.py` | `validate_transactions(df) → (pass_df, reject_df)`, `build_curated(df)` — pure, không IO |
| CLI theo stage | `scripts/run_de_pipeline.py` | `--stage raw\|staging\|curated\|offline_store --batch-id ...` (stage `staging` = validate + ghi quarantine + ghi staging); chứa toàn bộ IO GCS/BigQuery |
| DAG | `dags/dag_07_de_gcs_pipeline.py` | 4 task BashOperator gọi CLI, đọc `batch_id`/`source_blob` từ `dag_run.conf` |
| GCS client API | `api/app/clients/gcs.py` | Wrapper mỏng upload stream lên GCS |
| Router | `api/app/routers/ingest.py` | `POST /ingest/upload` (multipart → landing → trigger DAG, trả `batch_id`+`dag_run_id`), `GET /ingest/runs/{dag_run_id}` |
| Schemas/deps/config | `api/app/schemas.py`, `api/app/deps.py`, `api/app/config.py` | Response models, `get_gcs_client`, settings mới |
| Setup GCP | `scripts/setup_gcp.sh` | gcloud: SA `dealight-pipeline` (Storage objectAdmin scope bucket, BigQuery dataEditor + jobUser), bucket `asia-southeast1`, dataset, key → `infra/secrets/gcp-key.json` (gitignored) |
| Infra | `infra/docker-compose.yml`, `infra/airflow/Dockerfile`, `.env.example`, `pyproject.toml` | Mount key vào api + airflow, env mới, deps `google-cloud-storage` + `google-cloud-bigquery` |
| Tests | `tests/test_de_pipeline.py`, `tests/test_ingest_api.py` | DQ rules, curated aggregation, endpoint (mock GCS + AirflowClient) |

Env mới: `GCP_PROJECT_ID`, `GCS_BUCKET`, `BQ_DATASET`, `GOOGLE_APPLICATION_CREDENTIALS`.

## Xử lý lỗi

- Endpoint: file không `.csv` hoặc rỗng → 400; upload GCS lỗi → 502; trigger Airflow lỗi sau khi file đã lên landing → 502 kèm GCS URI trong detail để trigger lại thủ công.
- DAG: mỗi task retry 1 lần; thiếu cột / 100% reject → fail run; re-run cùng `batch_id` idempotent.

## Kiểm thử

- Unit test không cần GCP thật: mock `google.cloud.storage` / `google.cloud.bigquery`; test DQ rules và aggregation trên DataFrame thuần; test endpoint với client mock. CI giữ nguyên.
- Smoke test thật (thủ công): chạy `setup_gcp.sh` → `curl -F "file=@data/raw/train.csv" http://localhost:8000/ingest/upload` → kiểm tra 4 prefix GCS + query bảng `dealight.sales_daily`.

## Ngoài phạm vi (YAGNI)

- Online store MemoryStore/Redis sync từ curated.
- Backfill với Iceberg (atomic partition overwrite, time travel).
- GCS event-driven trigger / Cloud Functions.
