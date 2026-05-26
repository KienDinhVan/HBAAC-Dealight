# Batch Forecast Serving PoC (chưa nghiệm thu production)

## Pham vi da trien khai

Day la PoC ky thuat duoc dung som de kiem tra huong batch-serving trong
`sku_demand_forecasting_sprint_plan.md`. Lo trinh nghiem thu chinh thuc bat dau
tu Sprint 0 tai `docs/sprints/SPRINT_00_KICKOFF.md`; PoC nay khong duoc tinh la
Sprint 5, 6 hoac 8 da hoan thanh.

```text
approved submission CSV
  -> loader validation
  -> PostgreSQL serving.sku_forecast + serving.forecast_runs
  -> FastAPI read-only serving
  -> Prometheus scrape
  -> Grafana container
```

API phuc vu forecast da compute san. API khong train hoac chay model trong request.

Mac dinh stack nap submission duoc nop cuoi:

```text
data/artifacts/submission_FINAL_twostage_top300_lb730_s7_b1200_seedens20_alpha0.575_keysku_cautious_mapoldnew_a0.05.csv
```

Ket qua public da ghi nhan cua artifact nay la `0.48729`. Submission co diem public tot hon
`0.48694` van duoc giu trong `data/artifacts` va co the promote bang cau hinh `.env`.

## Khoi dong tren VM

Yeu cau:

- Docker Engine va Docker Compose plugin.
- Port `8000` cho API; Prometheus va Grafana mac dinh chi bind `localhost`.
- File `.env` khong commit va password PostgreSQL du manh.

```bash
cp .env.example .env
# Sua POSTGRES_PASSWORD trong .env truoc khi deploy VM.
docker compose --env-file .env -f infra/docker-compose.yml up --build -d
```

Loader se:

1. Validate schema CSV, prediction khong am/khong missing.
2. Chuyen moi SKU thanh 56 ban ghi horizon.
3. Ghi idempotent vao `serving.sku_forecast`.
4. Danh dau successful run trong `serving.forecast_runs`.

Submission hien tai co `15,972` SKU, do do PoC nap `894,432` dong forecast.

## Endpoints

```http
GET /health
GET /version
GET /forecast-runs/latest
GET /model/current
GET /forecast/{item_code}?days=56
GET /forecast/top-skus?target_date=2025-09-06&limit=100
GET /forecast/summary?target_date=2025-09-06
GET /metrics
```

Smoke test:

```bash
curl --fail http://localhost:8000/health
curl --fail http://localhost:8000/forecast-runs/latest
curl --fail "http://localhost:8000/forecast/SKU-00001?days=2"
curl --fail http://localhost:8000/metrics >/dev/null
```

## Chuyen submission cho PoC

De dung submission public score `0.48694` cho PoC, sua `.env`:

```dotenv
PRODUCTION_SUBMISSION_PATH=data/artifacts/submission_twostage_active_recent_top_profit_top300_a50_r56_lb730_s7_tc_sun0_eos0_b900_2025-09-05_alpha0.60_keysku_cautious.csv
FORECAST_RUN_ID=submission-best-public-048694-20250905
MODEL_NAME=twostage-keysku-cautious
MODEL_VERSION=public-0.48694
```

Sau do chay:

```bash
docker compose --env-file .env -f infra/docker-compose.yml --profile poc run --rm forecast-loader
docker compose --env-file .env -f infra/docker-compose.yml restart forecast-api
```

## Van hanh co ban

```bash
make up
make smoke
make logs
make down
```

Database duoc luu trong Docker volume `postgres-data`. Khi dua len VM that, can dat backup
`pg_dump` dinh ky va khong xoa volume khi redeploy.

## Chua nam trong PoC nay

Nhung phan sau van can trien khai theo sprint tiep theo:

- Bronze/Silver/Gold ingestion tu transaction moi.
- Feature pipeline va training retrain.
- MLflow model registry.
- Airflow scheduling.
- Drift monitoring va alert.
- CI/CD va Ansible cho remote VM.
