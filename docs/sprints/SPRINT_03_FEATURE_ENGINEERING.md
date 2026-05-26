# Sprint 3 - Feature Engineering & Offline Feature Store

## Trạng thái

`Hoàn thành nghiệm thu chức năng ngày 2026-05-26 - rollout scheduler còn bị chặn`.

Sprint 3 được mở sau khi pipeline Gold của Sprint 2 đạt acceptance. Vì Docker
host chưa cho thay long-running Airflow container, DAG được nghiệm thu bằng
one-off container dùng đúng image runtime mới.

## Phạm vi MVP

Feature snapshot chính thức cho vòng nghiệm thu:

```text
feature_version = sprint-03-v1-top100-a60-h56
top_skus = 100
as_of_dates = 60
horizons = 1..56
```

Snapshot này phục vụ training pipeline và kiểm thử end-to-end local; chưa đại
diện cho forecast toàn bộ `15,972` SKU.

## Deliverables

| Deliverable | Artifact |
|---|---|
| Feature logic và quality validation | `src/hbacc_prj/features.py` |
| Offline feature schema | `scripts/sprint_03_feature_schema.sql` |
| Feature build CLI | `scripts/run_feature_pipeline.py` |
| Feature metadata | `feature_registry.yaml` |
| Airflow DAG | `dags/dag_02_build_features.py` |
| Leakage/unit tests | `tests/test_sprint_03_features.py` |

## Quyết định chống leakage

- Lag, rolling, SKU statistics và price history chỉ dùng giá trị trước
  `as_of_date`.
- Calendar features lấy từ `target_date`, là thông tin biết trước.
- `target_date > as_of_date` và target quantity được lấy từ Gold tại đúng ngày
  horizon.
- Primary key feature store là
  `(feature_version, as_of_date, item_code, horizon)`.

## Acceptance checklist

| Tiêu chí | Kết quả |
|---|---|
| Sinh training frame horizon `1..56` | Đạt: `56` horizon |
| Feature table có schema ổn định | Đạt: `features.offline_sku_features` + registry YAML |
| Không data leakage | Đạt: test lag/rolling và `target_not_future=0` |
| Data quality pass | Đạt: null/duplicate/inf/invalid horizon/negative target đều `0` |
| Training job load được feature table | Đạt: Sprint 4 đã train trực tiếp từ snapshot |
| DAG chạy end-to-end | Đạt: `dag_02_build_features`, logical date `2026-06-04`, state `success` |

## Bằng chứng nghiệm thu

Kết quả build và chạy lại idempotent:

```text
rows=336000
skus=100
as_of_dates=60
horizons=56
duplicate_keys=0
null_keys=0
infinite_features=0
negative_targets=0
target_not_future=0
```

Database lưu snapshot từ `2025-05-13` đến `2025-07-11` theo `as_of_date`.
DAG nghiệm thu sử dụng image Airflow có dependency đã kiểm tra bằng `pip check`
và thực hiện chuỗi task từ load Gold đến validate/save offline features.
