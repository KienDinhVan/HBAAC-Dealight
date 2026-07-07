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
