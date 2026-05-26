# Sprint 1 - Project Foundation & Local Infrastructure

## Trạng thái

`Hoàn thành - nghiệm thu ngày 2026-05-26` - mở sau khi Sprint 0 được phê
duyệt và đóng trước khi bắt đầu Sprint 2.

## Phạm vi

Sprint này chỉ dựng nền tảng local. Việc generate/load forecast bằng submission
không nằm trong acceptance Sprint 1 và được giữ trong Docker Compose dưới
profile `poc`.

## Quyết định kỹ thuật

- Quản lý Python dependency bằng `uv`.
- Dùng PostgreSQL chung server, ba database tách biệt:
  `sku_forecasting`, `mlflow`, `airflow`.
- Dùng MinIO bucket `mlflow` làm artifact store của MLflow.
- Airflow chạy `LocalExecutor`; `redis` và `airflow-worker` chưa cần trong MVP
  local vì plan đánh dấu worker optional.
- API `/health` kiểm tra readiness của database, không yêu cầu forecast run.

## Deliverables

| Deliverable | Artifact |
|---|---|
| Repository structure | `api/`, `src/`, `dags/`, `tests/`, `infra/`, `scripts/`, `data/`, `notebooks/` |
| Docker Compose | `infra/docker-compose.yml` |
| Environment template | `.env.example` |
| Database bootstrap | `infra/postgres/init-platform-databases.sh`, `scripts/init_db.sql` |
| MinIO + MLflow | Compose services và `infra/mlflow/Dockerfile` |
| Airflow | Compose webserver/scheduler/init và `dags/sprint_01_platform_health.py` |
| FastAPI skeleton | `api/app/main.py`: `/health`, `/version` |
| Monitoring containers | Prometheus và Grafana trong Compose |
| CI cơ bản | `.github/workflows/ci.yml` |

## Database schema gate

Database `sku_forecasting` phải chứa:

```text
raw, bronze, silver, gold, features, modeling, serving, monitoring
```

Metadata MLflow và Airflow nằm tại database riêng để tránh collision migration.

## Acceptance checklist

| Tiêu chí | Lệnh/bằng chứng cần chạy | Trạng thái |
|---|---|---|
| Compose hợp lệ | `docker compose ... config --quiet` | Đạt |
| Local stack chạy | `docker compose ... up --build -d`, chạy lại `up -d` sau sửa init | Đạt |
| PostgreSQL kết nối và đủ schemas | ba database và tám schema được truy vấn trực tiếp | Đạt |
| MinIO live và có bucket `mlflow` | health endpoint + `mc ls` thấy `mlflow/` | Đạt |
| MLflow UI/backend hoạt động | `GET :5000/health` trả `OK`, có 19 bảng metadata | Đạt |
| Airflow UI/scheduler hoạt động | UI/scheduler healthy, DAG parse không có import error | Đạt |
| API foundation hoạt động | `/health` và `/version` trả HTTP 200 | Đạt |
| Prometheus/Grafana hoạt động | ready/API health endpoint trả thành công | Đạt |
| Lint và tests pass | `ruff check`, `pytest -q` (`9 passed`) | Đạt |
| CI workflow tồn tại | `.github/workflows/ci.yml` | Đạt |

## Lệnh nghiệm thu dự kiến

```bash
cp .env.example .env
# Đổi secret trước khi chạy ngoài máy local.
docker compose --env-file .env -f infra/docker-compose.yml up --build -d
make smoke-infra
uv run ruff check api scripts tests src
uv run pytest -q
```

## Bằng chứng nghiệm thu

- PostgreSQL: tồn tại `sku_forecasting`, `mlflow`, `airflow`; database chính có
  `raw`, `bronze`, `silver`, `gold`, `features`, `modeling`, `serving`,
  `monitoring`.
- MinIO: endpoint live trả thành công; bucket `mlflow/` được tạo idempotent.
- MLflow: endpoint health trả `OK`; backend PostgreSQL đã tạo `19` bảng.
- Airflow: webserver và scheduler healthy; `sprint_01_platform_health` có
  trong `airflow dags list`, `airflow dags list-import-errors` trả rỗng.
- Lỗi quyền ghi scheduler log phát hiện trong nghiệm thu đã được sửa bằng bước
  `chown` sau migration trong `airflow-init`, sau đó `docker compose up -d`
  chạy lại thành công.
- API/monitoring: API health/version, Prometheus ready và Grafana API health
  đều trả thành công.
- Code quality: `uv run ruff check api scripts tests src` và
  `uv run pytest -q` pass (`9 passed`).
