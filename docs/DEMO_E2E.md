# Demo E2E — chứng minh 6 tính chất của DE pipeline

Kịch bản demo trên hệ thống thật (GCP + Airflow + BigQuery Iceberg + Redis).
Vận hành chi tiết: xem `docs/DE_GCS_PIPELINE.md`.

## Chuẩn bị

```bash
export GCP_PROJECT_ID=gen-lang-client-0222711301
export GCS_BUCKET=${GCP_PROJECT_ID}-dealight-data
docker compose -f infra/docker-compose.yml up -d
curl -s http://localhost:8000/health   # {"status":"ok",...}
```

`.env` cần có `BQ_BIGLAKE_CONNECTION=dealight-biglake` — bảng `sales_daily`
là **BigQuery-managed Iceberg table**, data + metadata nằm mở tại
`gs://$GCS_BUCKET/warehouse/sales_daily/`.

---

## 1. End-to-end

Một lệnh upload kéo dữ liệu qua toàn bộ kiến trúc: API → GCS landing →
Airflow DAG → raw → staging (+quarantine) → curated → BigQuery (Iceberg) → Redis.

```bash
curl -s -F "file=@data/raw/train.csv" http://localhost:8000/ingest/upload
# -> {"batch_id":"<BATCH>","dag_run_id":"manual__...","state":"queued",...}
export BATCH=<BATCH>  DAG_RUN=<dag_run_id>

# Theo dõi đến khi success (5 task: ingest_raw -> ... -> sync_online_store)
curl -s http://localhost:8000/ingest/runs/$DAG_RUN   # {"state":"success"}

# Dữ liệu hiện diện ở mọi tầng:
gcloud storage ls "gs://$GCS_BUCKET/raw/batch_id=$BATCH/" \
                  "gs://$GCS_BUCKET/staging/batch_id=$BATCH/" \
                  "gs://$GCS_BUCKET/curated/batch_id=$BATCH/"
bq query --use_legacy_sql=false \
  'SELECT COUNT(*) AS row_count FROM `'"$GCP_PROJECT_ID"'.dealight.sales_daily`'
docker exec sku-demand-forecasting-redis-1 redis-cli DBSIZE   # ~15971 SKU
```

**Kỳ vọng:** DAG success; ~487k dòng trong BigQuery; ~16k hash trong Redis.

## 2. Reproducible (tái lập)

Upload **cùng một file lần thứ hai** — kết quả logic không đổi (không nhân đôi):

```bash
bq query --use_legacy_sql=false 'SELECT COUNT(*) AS c, ROUND(SUM(total_sales),2) AS s
  FROM `'"$GCP_PROJECT_ID"'.dealight.sales_daily`'          # ghi lại c, s
curl -s -F "file=@data/raw/train.csv" http://localhost:8000/ingest/upload
# ... chờ DAG success rồi chạy lại query trên
```

**Kỳ vọng:** `c` và `s` giống hệt trước — swap theo ngày là idempotent
(temp table + DELETE/INSERT nguyên tử), pipeline thuần hàm theo `batch_id`.

## 3. Traceable (truy vết)

Mỗi batch có `batch_id` xuyên suốt mọi tầng — từ file gốc đến từng dòng BigQuery:

```bash
gcloud storage ls "gs://$GCS_BUCKET/**batch_id=$BATCH**"        # raw/staging/curated/quarantine/dq
gcloud storage cat "gs://$GCS_BUCKET/dq/batch_id=$BATCH/summary.json"
bq query --use_legacy_sql=false 'SELECT batch_id, MIN(loaded_at) AS loaded_at, COUNT(*) AS row_count
  FROM `'"$GCP_PROJECT_ID"'.dealight.sales_daily` GROUP BY batch_id'
docker exec sku-demand-forecasting-redis-1 redis-cli HGET "sales_daily:SKU-00002" batch_id
```

**Kỳ vọng:** cùng một `batch_id` xuất hiện ở GCS (5 prefix), BigQuery
(`batch_id`, `loaded_at` trên từng dòng), Redis, và log Airflow của từng task
(Airflow UI → dag_07 → run → logs).

## 4. Backfillable (sửa dữ liệu lịch sử)

Sửa một ngày trong quá khứ bằng cách upload file chỉ chứa ngày đó — các ngày
khác giữ nguyên, và **Iceberg time travel** cho xem trạng thái trước khi sửa:

```bash
DATE=2024-01-15   # chọn một ngày có trong dữ liệu
# Ghi lại trạng thái trước:
bq query --use_legacy_sql=false 'SELECT COUNT(*) c, ROUND(SUM(total_quantity),1) q
  FROM `'"$GCP_PROJECT_ID"'.dealight.sales_daily` WHERE date = "'"$DATE"'"'
TS_BEFORE=$(date -u +%Y-%m-%dT%H:%M:%SZ)

# Tạo file backfill chỉ chứa ngày đó, nhân đôi Quantity để thấy khác biệt:
python3 - <<'PY'
import pandas as pd
df = pd.read_csv("data/raw/train.csv")
day = df[df["Date"] == "2024-01-15"].copy()
day["Quantity"] = day["Quantity"] * 2
day.to_csv("/tmp/backfill_one_day.csv", index=False)
PY
curl -s -F "file=@/tmp/backfill_one_day.csv" http://localhost:8000/ingest/upload
# ... chờ DAG success

# Sau: chỉ ngày $DATE thay đổi, tổng các ngày khác giữ nguyên
bq query --use_legacy_sql=false 'SELECT COUNT(*) c, ROUND(SUM(total_quantity),1) q
  FROM `'"$GCP_PROJECT_ID"'.dealight.sales_daily` WHERE date = "'"$DATE"'"'
# Time travel về trước khi backfill (Iceberg snapshot):
bq query --use_legacy_sql=false 'SELECT COUNT(*) c, ROUND(SUM(total_quantity),1) q
  FROM `'"$GCP_PROJECT_ID"'.dealight.sales_daily`
  FOR SYSTEM_TIME AS OF TIMESTAMP("'"$TS_BEFORE"'") WHERE date = "'"$DATE"'"'
```

**Kỳ vọng:** `q` sau backfill ≈ 2× trước; query time-travel trả đúng giá trị cũ;
tổng row_count các ngày khác không đổi.

## 5. Observable (quan sát được)

```bash
# DQ summary mỗi batch (rows_in/passed/rejected + lý do):
gcloud storage cat "gs://$GCS_BUCKET/dq/batch_id=$BATCH/summary.json" | python3 -m json.tool
# Cảnh báo Discord khi reject ratio vượt ngưỡng — demo bằng ngưỡng thấp:
#   .env: DQ_REJECT_ALERT_RATIO=0.01 -> restart airflow -> upload lại -> tin nhắn Discord
# Metrics API (Prometheus format):
curl -s http://localhost:8000/metrics | grep http_requests_total | head -3
# Airflow UI: http://localhost:8080 (log từng task, retry, duration)
# Grafana: http://localhost:3000, Prometheus: http://localhost:9090
```

**Kỳ vọng:** summary JSON có `reject_ratio` (~0.0526 với train.csv gốc —
phần lớn là hàng trả lại quantity âm); alert Discord đến khi hạ ngưỡng.

## 6. Production-ready

Checklist chứng minh (mỗi mục kiểm được bằng lệnh):

| Hạng mục | Bằng chứng |
|---|---|
| Test tự động | `uv run pytest -q` — toàn bộ suite pass (95+ tests, pipeline có unit test với fake GCS/BQ/Redis) |
| Idempotency / exactly-once theo ngày | mục 2 — upload trùng không nhân đôi |
| Atomic write | swap DELETE+INSERT trong 1 transaction; không có trạng thái nửa vời |
| Retry | task Airflow `retries=1`; DAG re-trigger cùng conf là an toàn |
| Least-privilege IAM | SA chỉ có objectAdmin trên bucket + dataEditor trên dataset (không project-wide): `gcloud projects get-iam-policy $GCP_PROJECT_ID --flatten=bindings --filter="bindings.members:dealight-pipeline" --format="value(bindings.role)"` |
| Lifecycle & cost | `gcloud storage buckets describe gs://$GCS_BUCKET --format="json(lifecycle)"` — landing xoá sau 30d, raw xuống Nearline sau 90d |
| Secrets | `git check-ignore infra/secrets/gcp-key.json .env` — không vào git |
| Open format (không lock-in) | bảng Iceberg: `gcloud storage ls "gs://$GCS_BUCKET/warehouse/sales_daily/metadata/"` — metadata Iceberg chuẩn mở, Spark/Trino/DuckDB đọc được trực tiếp |
| Healthcheck | `docker compose -f infra/docker-compose.yml ps` — mọi service `healthy` |
