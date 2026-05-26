# Sprint 4 - Model Training Pipeline & MLflow Registry

## Trạng thái

`Hoàn thành nghiệm thu chức năng ngày 2026-05-26 - rollout scheduler còn bị chặn`.

## Phạm vi triển khai

- Load `features.offline_sku_features` từ snapshot Sprint 3.
- Split train/validation theo `target_date`, validation `28` ngày cuối.
- So sánh ba baseline: last value, seasonal lag 7 và moving average 28.
- Train LightGBM với seed cố định `2026`, clip prediction âm.
- Tính `MAE`, `RMSE`, `WAPE`, `SMAPE`, metric theo horizon và SKU group.
- Log metrics, evaluation report, source bundle và model artifact vào MLflow.
- Register vào `sku-demand-lightgbm` ở `Staging` chỉ khi:

```text
lightgbm_wape <= best_baseline_wape * 1.05
```

## Deliverables

| Deliverable | Artifact |
|---|---|
| Training/evaluation/MLflow logic | `src/hbacc_prj/training.py` |
| Training CLI | `scripts/train_model.py` |
| Evaluation report local từ integration run | `data/features/evaluation_sprint_04.json` |
| Airflow DAG | `dags/dag_03_train_model.py` |
| Unit/model tests | `tests/test_sprint_04_training.py` |
| Airflow runtime dependencies | `infra/airflow/Dockerfile` |

## Acceptance checklist

| Tiêu chí | Kết quả |
|---|---|
| Training pipeline chạy thành công | Đạt: DAG run logical date `2026-06-03` state `success` |
| Time split không overlap | Đạt: unit test pass |
| Metrics và baseline comparison | Đạt: WAPE rule pass |
| Model log vào MLflow/MinIO | Đạt: artifact roots `evaluation`, `model`, `source` |
| Model load lại và predict | Đạt: `model_reloaded_and_predicted=true` |
| Model đăng ký Registry khi pass | Đạt: `sku-demand-lightgbm` version `5`, `Staging` |
| Reproducibility | Đạt: các run Airflow liên tiếp cho cùng WAPE `0.931972857866525` |

## Kết quả model candidate

```text
registered_model = sku-demand-lightgbm
version = 5
stage = Staging
mlflow_run_id = 6e3748b5752e4a5893a3a813d28591a8
lightgbm_wape = 0.931972857866525
best_baseline_wape = 1.255653655743978
passed_registration_rule = true
source_git_commit = 8b36815
```

Version `1` đến `4` sinh trong quá trình integration/khắc phục runtime đã được
chuyển sang `Archived`; chỉ version `5` còn ở `Staging`.

Source bundle trong artifact version `5` gồm:

```text
dag_03_train_model.py
feature_registry.yaml
features.py
train_model.py
training.py
```

`source_git_commit` là HEAD hiện tại của repository; source bundle được log
kèm model để bao gồm cả thay đổi workspace chưa commit khi nghiệm thu.

## Ghi chú runtime

Airflow image mới đã được build với Airflow constraints, `pip check` pass và bổ
sung `libgomp1` cho LightGBM. Long-running scheduler/webserver hiện chưa thể
recreate do blocker quyền Docker host đã ghi ở Sprint 2; DAG được nghiệm thu
thành công qua one-off container dùng image mới.
