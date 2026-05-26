# Sprint 2 - Bronze/Silver/Gold Data Pipeline

## Trạng thái

`Hoàn thành nghiệm thu chức năng ngày 2026-05-26 - rollout service còn bị chặn`.

Sprint 2 được thực hiện sau khi Sprint 1 đã nghiệm thu. Pipeline đã chạy trên
toàn bộ file raw, được chạy lại để kiểm tra idempotency, và DAG Airflow đã chạy
thành công bằng image job mới. Việc thay container Airflow/API chạy thường
trực chưa hoàn tất vì Docker daemon trên host trả lỗi quyền khi Compose dừng
container cũ; blocker vận hành này được theo dõi riêng khỏi gate chức năng.

## Phạm vi triển khai

- Ingest `data/raw/train.csv` vào `raw.transactions` với `batch_id`,
  `source_file`, `source_row_number` và `ingested_at`.
- Parse/chuẩn hóa dữ liệu gần raw trong `bronze.transactions`.
- Clean dữ liệu, đánh dấu validity và tách sales/returns trong
  `silver.transactions_clean`.
- Aggregate theo `(date, item_code)` trong `gold.daily_sku_sales`.
- Chạy data-quality checks và orchestration DAG
  `dag_01_ingest_transform`.

## Quyết định dữ liệu

- Raw giữ các giá trị business dưới dạng text để dữ liệu nguồn không bị thay
  đổi trước khi transform.
- `sales_quantity = max(quantity, 0)`,
  `return_quantity = max(-quantity, 0)` và `net_quantity` giữ giá trị ròng.
- `gold.daily_sku_sales` có primary key `(date, item_code)`.
- Input hiện tại là full snapshot `train.csv`; khi chạy lại cùng batch,
  pipeline thay dữ liệu Gold từ snapshot đó để không nhân đôi record. Cơ chế
  incremental/upsert theo partition sẽ được bổ sung khi có nguồn incremental.

## Deliverables

| Deliverable | Artifact |
|---|---|
| Schema Raw/Bronze/Silver/Gold | `scripts/sprint_02_pipeline_schema.sql` |
| Transform và quality logic | `src/hbacc_prj/pipeline.py` |
| CLI chạy pipeline | `scripts/run_data_pipeline.py` |
| Airflow DAG | `dags/dag_01_ingest_transform.py` |
| Airflow image có PostgreSQL driver | `infra/airflow/Dockerfile` |
| Runtime mounts/env cho DAG | `infra/docker-compose.yml` |
| Unit/integration/idempotency tests | `tests/test_sprint_02_pipeline.py` |

## Acceptance checklist

| Tiêu chí | Kết quả |
|---|---|
| Raw tạo được Gold | Đạt: `711,980` raw rows thành `507,050` gold rows |
| Gold unique theo `(date, item_code)` | Đạt: `0` duplicate keys |
| Rerun không nhân đôi dữ liệu Gold | Đạt: lần chạy lại vẫn `507,050` rows, `0` duplicate |
| Data quality checks pass | Đạt: `0` invalid Silver, null key, negative return và invalid transaction count |
| Unit + integration tests pass | Đạt: `13 passed` |
| DAG chạy đúng thứ tự task | Đạt: `airflow dags test dag_01_ingest_transform 2026-05-27` success |
| Runtime services dùng image/mount mới | Chờ: Docker host không dừng được container đang chạy để recreate |

## Bằng chứng nghiệm thu

Pipeline full snapshot và idempotency được chạy hai lần:

```bash
PYTHONPATH=src uv run python -m scripts.run_data_pipeline \
  --database-url 'postgresql://forecast:***@127.0.0.1:5432/sku_forecasting' \
  --batch-id train-full --stage all
```

Kết quả ở cả hai lần:

```text
raw_rows=711980
bronze_rows=711980
silver_rows=711980
valid_silver_rows=711980
invalid_silver_rows=0
gold_rows=507050
duplicate_gold_keys=0
null_gold_dates=0
null_gold_item_codes=0
negative_return_quantity=0
invalid_transaction_count=0
```

Airflow DAG chạy thật theo thứ tự:

```text
validate_raw_file -> ingest_raw -> build_bronze -> build_silver
-> build_gold -> run_data_quality_checks
DagRun state=success
```

Quality gate của code:

```bash
uv run ruff check api scripts tests src dags
uv run pytest -q
docker compose --env-file .env.example -f infra/docker-compose.yml config --quiet
```

## Blocker rollout runtime

`docker compose ... up --build -d` build được image mới và tạo replacement
container, nhưng thất bại khi dừng API container đang chạy:

```text
Error response from daemon: cannot stop container: ...: permission denied
```

Scheduler/webserver hiện đang healthy bằng image cũ; image job mới đã được
xác minh qua lần chạy DAG thành công. Theo chỉ đạo tiếp tục sprint ngày
2026-05-26, các sprint sau được nghiệm thu bằng `docker compose run` trên image
mới trong khi blocker thay long-running container tiếp tục được ghi nhận.
