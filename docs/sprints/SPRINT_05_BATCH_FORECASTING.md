# Sprint 5 - Batch Forecasting Pipeline

## Trạng thái

`Hoàn thành nghiệm thu chức năng ngày 2026-05-27 - rollout scheduler còn bị chặn`.

Sprint 5 mở sau khi Sprint 4 đóng. Pipeline batch forecast load model
`sku-demand-lightgbm` v5 (Staging) từ MLflow Model Registry, sinh forecast 56
ngày cho toàn bộ `15,972` SKU trong Gold và ghi `serving.sku_forecast` +
`serving.forecast_runs`. Vì model v5 chỉ học `100` SKU thuộc snapshot Sprint 3,
SKU ngoài training set được dự báo bằng baseline `seasonal_naive_lag_7` (đã thống
nhất là phương án B - Hybrid).

## Phạm vi triển khai

- Load production model: thử `Production`, fallback `Staging` của
  `sku-demand-lightgbm`.
- Universe SKU = `gold.daily_sku_sales` (toàn bộ `15,972` SKU hiện có).
- Inference frame anchored tại `forecast_date = 2025-09-05`, horizon `1..56`.
- LightGBM cho `100` SKU thuộc feature snapshot `sprint-03-v1-top100-a60-h56`;
  baseline `lag_7` cho `15,872` SKU còn lại; clip prediction âm về 0.
- Persist transaction-safe vào `serving.sku_forecast` (PK
  `run_id, item_code, target_date`) và state machine
  `running -> success/failed` trong `serving.forecast_runs`.
- Bổ sung cột `prediction_source` (`lightgbm` | `seasonal_naive_lag_7`) để
  audit chiến lược hybrid.

## Deliverables

| Deliverable | Artifact |
|---|---|
| Schema migration | `scripts/sprint_05_serving_schema.sql` |
| Forecasting module | `src/hbacc_prj/forecasting.py` |
| Batch forecast CLI | `scripts/run_batch_forecast.py` |
| Airflow DAG | `dags/dag_04_batch_forecast.py` |
| Unit + integration tests | `tests/test_sprint_05_forecast.py` |
| Makefile target | `forecast` |

## Quyết định kỹ thuật

- `sku_lookback_days = 0` (universe): chọn toàn bộ SKU trong Gold để đảm bảo
  acceptance "Sinh forecast đủ 56 ngày cho toàn bộ SKU". SKU không có hoạt
  động gần đây sẽ ra prediction 0 từ baseline; đó là hành vi đúng theo Sprint
  0 metric (`max(Quantity, 0)`), không gây vi phạm validation.
- `lookback_days = 200`: giới hạn cửa sổ dữ liệu Gold đọc về cho tính feature.
  `lag_56` + `rolling_56` vẫn đủ history, tránh tải bộ nhớ khi xử lý
  `15,972 SKU × 1,750 ngày`.
- LightGBM `item_code_id` map theo training SKU list của feature snapshot,
  SKU không có trong map nhận `-1` và đi nhánh baseline để không leak sang
  model.
- Persistence dùng `COPY ... FROM STDIN` mirror pattern PoC
  `scripts/load_submission_forecast.py` để giữ throughput cao.

## Acceptance checklist

| Tiêu chí | Kết quả |
|---|---|
| Sinh forecast đủ 56 ngày cho toàn bộ SKU | Đạt: `15,972` SKU × `56` horizon = `894,432` rows |
| Forecast lưu vào DB | Đạt: `serving.sku_forecast` + `serving.forecast_runs` |
| API đọc được forecast mới | Đạt: `/forecast-runs/latest`, `/forecast/{item_code}`, `/forecast/summary`, `/forecast/top-skus` trả run mới |
| Validation pass | Đạt: `0` negative, `0` duplicate, `0` missing SKU, `0` invalid horizon, `0` wrong target_date |
| Idempotent rerun | Đạt: rerun cùng `run_id` giữ nguyên `894,432` rows, state `success` |
| DAG end-to-end | Đạt: `dag_04_batch_forecast` 7/7 tasks success, run_id `sprint-05-dag-20260527` |

## Bằng chứng nghiệm thu

### CLI live run universe SKU

```bash
docker compose --env-file .env -f infra/docker-compose.yml \
  run --rm --no-deps --entrypoint bash airflow-scheduler -c \
  "python -m scripts.run_batch_forecast \
    --run-id sprint-05-universe-20250905 \
    --forecast-date 2025-09-05 \
    --lookback-days 200 --sku-lookback-days 0"
```

Output JSON:

```json
{
  "active_skus": 15972,
  "feature_version": "sprint-03-v1-top100-a60-h56",
  "forecast_date": "2025-09-05",
  "lookback_days": 200,
  "max_horizon": 56,
  "model_name": "sku-demand-lightgbm",
  "model_stage": "Staging",
  "model_version": "5",
  "prediction_source_counts": {
    "lightgbm": 5600,
    "seasonal_naive_lag_7": 888832
  },
  "rows_written": 894432,
  "run_id": "sprint-05-universe-20250905",
  "training_skus": 100,
  "validation": {
    "duplicate_keys": 0,
    "expected_skus": 15972,
    "horizons": 56,
    "invalid_horizons": 0,
    "missing_skus": 0,
    "negative_predictions": 0,
    "null_predictions": 0,
    "rows": 894432,
    "skus": 15972,
    "wrong_target_date": 0
  }
}
```

### Airflow DAG one-off

```bash
docker compose --env-file .env -f infra/docker-compose.yml \
  run --rm --no-deps --entrypoint bash airflow-scheduler -c \
  "airflow dags test dag_04_batch_forecast 2026-05-27"
```

Task chain:

```text
get_latest_model SUCCESS
build_inference_features SUCCESS
validate_inference_features SUCCESS
generate_predictions SUCCESS
validate_predictions SUCCESS
save_forecast SUCCESS
mark_latest_forecast_run SUCCESS
DagRun state=success
```

### Idempotency

Rerun cùng `run_id = sprint-05-universe-20250905`:

```text
rows_written=894432 (unchanged)
forecast_runs.status='success'
```

### Database verify

```sql
SELECT COUNT(*) AS rows, COUNT(DISTINCT item_code) AS skus,
       COUNT(DISTINCT target_date) AS dates,
       MIN(predicted_quantity), MAX(predicted_quantity)
FROM serving.sku_forecast WHERE run_id = 'sprint-05-universe-20250905';
-- rows=894432, skus=15972, dates=56, min=0, max=40
```

### API smoke

```bash
curl http://localhost:8000/forecast-runs/latest
# -> run_id='sprint-05-universe-20250905', status=success, row_count=894432

curl 'http://localhost:8000/forecast/SKU-08063?days=5'
# -> 5 ForecastPoint rows for SKU-08063 anchored 2025-09-05

curl 'http://localhost:8000/forecast/summary?target_date=2025-09-15'
# -> sku_count=15972, total_predicted_quantity=777.64

curl 'http://localhost:8000/forecast/top-skus?target_date=2025-09-15&limit=3'
# -> SKU-11142(40), SKU-09760(30.12), SKU-14516(20)
```

### Quality gate code

```bash
uv run ruff check api scripts tests src dags     # All checks passed
uv run pytest tests/test_sprint_05_forecast.py   # 5 passed, 1 skipped (integration)
```

## Blocker rollout runtime

Long-running `airflow-scheduler` container vẫn dùng image cũ chưa có mount
`src/`, `scripts/` (blocker quyền Docker host từ Sprint 2 chưa giải quyết).
Acceptance Sprint 5 dùng `docker compose run --rm` để spin one-off container
trên image mới, đồng pattern với Sprint 2-4.

`source_git_commit` tại thời điểm nghiệm thu: `8b36815`.
