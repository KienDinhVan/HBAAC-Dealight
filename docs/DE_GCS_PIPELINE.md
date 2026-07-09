# DE GCS Pipeline — Vận hành

Triển khai theo sơ đồ `DE_arch.png` (spec: `docs/superpowers/specs/2026-07-07-de-gcs-pipeline-design.md`).

## Luồng

`POST /ingest/upload` (CSV) → GCS `landing/` → trigger `dag_07_de_gcs_pipeline`:
`ingest_raw` (→ `raw/`) → `process_validate_to_staging` (fail → `quarantine/` + `dq/summary.json`, pass → `staging/`)
→ `build_curated` (→ `curated/`) → `load_offline_store` (→ BigQuery `dealight.sales_daily`, swap atomic)
→ `sync_online_store` (→ Redis hash `sales_daily:<item_code>`, dòng mới nhất mỗi SKU).

Khi `BQ_BIGLAKE_CONNECTION` được đặt (mặc định sau khi chạy `setup_gcp.sh`),
`sales_daily` là **BigQuery-managed Iceberg table**: data + metadata Iceberg nằm tại
`gs://<bucket>/warehouse/sales_daily/`, hỗ trợ time travel (`FOR SYSTEM_TIME AS OF`)
và đọc được từ engine ngoài (Spark/Trino/DuckDB). Để trống biến này thì dùng
native BigQuery table như cũ. Kịch bản demo 6 tính chất: `docs/DEMO_E2E.md`.

## Setup một lần

1. `gcloud auth login` với tài khoản có quyền Owner/Editor trên project.
2. `scripts/setup_gcp.sh <project-id>` — tạo bucket, dataset, service account, key
   tại `infra/secrets/gcp-key.json` (đã gitignore). Sau khi tạo key:
   `chmod 644 infra/secrets/gcp-key.json` (container Airflow chạy uid 50000).
3. Điền `GCP_PROJECT_ID`, `GCS_BUCKET`, `BQ_DATASET` vào `.env`.
4. `docker compose -f infra/docker-compose.yml up -d --build forecast-api airflow-webserver airflow-scheduler redis`

## Data quality & cảnh báo

- Mỗi batch ghi `dq/batch_id=<id>/summary.json` (rows_in/passed/rejected, reject_ratio, breakdown lý do).
- Nếu `reject_ratio > DQ_REJECT_ALERT_RATIO` (mặc định 0.1) và `DISCORD_WEBHOOK_URL` được đặt,
  pipeline gửi cảnh báo Discord; lỗi gửi cảnh báo không làm fail batch.
- Batch bị reject 100% vẫn ghi summary + cảnh báo trước khi fail DAG.

## Smoke test

```bash
curl -F "file=@data/raw/train.csv" http://localhost:8000/ingest/upload
# -> {"batch_id": "...", "dag_run_id": "manual__...", ...}
curl http://localhost:8000/ingest/runs/<dag_run_id>
gcloud storage ls "gs://$GCS_BUCKET/**" | head
bq query --use_legacy_sql=false \
  'SELECT batch_id, COUNT(*) AS row_count FROM `'"$GCP_PROJECT_ID"'.dealight.sales_daily` GROUP BY batch_id'
docker exec sku-demand-forecasting-redis-1 redis-cli HGETALL "sales_daily:SKU-00002"
```

## Backfill / sửa dữ liệu lịch sử

Không cần DAG riêng: `load_offline_store` load batch vào bảng tạm rồi hoán đổi bằng
**một câu MERGE atomic** — mọi ngày có mặt trong batch bị thay thế hoàn toàn
(partition overwrite), khớp nhánh Backfill trong DE_arch (temporary table →
atomic partition overwrite). Hệ quả:

- **Backfill** = sửa file CSV rồi upload lại qua `/ingest/upload`. Các ngày trong
  file được ghi đè, các ngày ngoài file giữ nguyên.
- **Upload trùng** không nhân đôi dữ liệu — các ngày trùng bị thay thế, không append.
- **Chạy lại một batch**: trigger lại DAG với cùng conf (`batch_id`, `source_blob`)
  từ Airflow UI — mọi tầng GCS ghi đè theo `batch_id`, MERGE idempotent.
