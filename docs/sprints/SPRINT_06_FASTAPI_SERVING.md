# Sprint 6 - FastAPI Serving Layer

## Trạng thái

`Hoàn thành nghiệm thu chức năng ngày 2026-05-27 - rollout long-running container
còn pending`.

Sprint 6 mở sau khi Sprint 5 đóng. API đọc trực tiếp `serving.sku_forecast` cho
run `sprint-05-universe-20250905` (`15,972` SKU x `56` horizon = `894,432`
rows) và serve qua FastAPI. Toàn bộ endpoint trong plan §6.1 đã có, plus
prometheus `/metrics` và OpenAPI doc tự động.

## Phạm vi triển khai

- Endpoint: `/health`, `/version`, `/forecast-runs/latest`, `/model/current`,
  `/forecast/{item_code}` (regex validated), `/forecast/top-skus` (offset
  pagination), `/forecast/summary`, `/metrics` (hidden from OpenAPI).
- Request validation: `item_code` route regex `^[A-Za-z0-9._\-]{1,64}$`, `days`
  bounded `1..56`, `limit` `1..1000`, `offset >= 0`, ISO date parsing cho
  `target_date`/`forecast_date`.
- Error contract: 200 happy path, 404 missing SKU/run, 422 invalid params,
  503 khi database `ping()` fail. Body lỗi giữ ngắn (`{"detail": ...}`) -
  không leak stacktrace/DSN/driver.
- CORS: middleware mount conditional theo env `CORS_ORIGINS` (default empty =
  off). Cấu hình rõ ràng - không có wildcard `*`.
- Observability: middleware đo `http_requests_total`,
  `http_request_duration_seconds`, `forecast_not_found_total`,
  `database_connection_errors_total`.

## Deliverables

| Deliverable | Artifact |
|---|---|
| API entrypoint | `api/app/main.py` (CORS mount, item_code regex) |
| Settings | `api/app/config.py` (`cors_origins` field, env parse) |
| Repository (read-only PG) | `api/app/repository.py` |
| Pydantic schemas | `api/app/schemas.py` |
| Container image | `api/Dockerfile` |
| Database indexes | `scripts/sprint_05_serving_schema.sql` |
| Contract + security tests | `tests/test_sprint_06_api.py` |
| Load test (k6) | `tests/load/forecast_k6.js` |
| Makefile targets | `smoke`, `load-test` |
| Env knob | `CORS_ORIGINS` in `.env.example` |

## Quyết định kỹ thuật

- `item_code` validated qua `Path(pattern=...)` thay vì validator riêng để
  invalid input về 422 ngay tại routing layer (path traversal, whitespace,
  control char đều bị reject trước khi vào repository).
- CORS off-by-default theo nguyên tắc least-privilege: API là internal read
  endpoint cho dashboard/Grafana, mở CORS chỉ khi operator set
  `CORS_ORIGINS=https://dashboard.host`. Test
  `test_default_settings_have_no_wildcard_cors` chặn pattern `["*"]`.
- `/metrics` giữ `include_in_schema=False` để OpenAPI không expose Prometheus
  surface (lo cho client SDK auto-generated).
- Database error swallow: middleware bắt `Exception` từ `ping()` -> trả
  `503 Database unavailable`, đồng thời tăng counter
  `database_connection_errors_total`. Body lỗi đã được FastAPI sanitize
  (không có traceback).
- Load test scope per-endpoint threshold (`endpoint:health`,
  `endpoint:forecast_sku`) cho scenario `warmup` (10 VUs) vì target plan §6.3
  là baseline; sustained 50 VUs giữ ngưỡng tổng `p95 < 1s`.

## Acceptance checklist

| Tiêu chí (plan §6) | Kết quả |
|---|---|
| API trả forecast đúng cho SKU | Đạt: `/forecast/SKU-08063?days=3` trả 3 ForecastPoint anchored `2025-09-05` |
| API response theo schema | Đạt: 35 contract test pass; OpenAPI 3.x hợp lệ, 7 path public |
| API có `/metrics` | Đạt: prometheus payload, không lộ trong OpenAPI |
| 10 VUs p95 < 500 ms | Đạt: warmup p95 = `66.33 ms` |
| 50 VUs p95 < 1000 ms | Đạt: sustained p95 = `561.84 ms` |
| `/health` p95 < 100 ms | Đạt: `61.89 ms` ở warmup |
| `/forecast/{sku}` p95 < 500 ms | Đạt: `65.69 ms` ở warmup |
| Error rate < 1 % | Đạt: `http_req_failed = 0.55 %`, `forecast_errors = 0 %` |
| Không expose stacktrace | Đạt: 503 body `{"detail":"Database unavailable"}` |
| Validate path param `item_code` | Đạt: regex `^[A-Za-z0-9._\-]{1,64}$` -> 422 cho whitespace, `;`, `'`, dài > 64 |
| CORS config rõ ràng | Đạt: env `CORS_ORIGINS` parse, default empty = no middleware, không wildcard |
| Docker image chạy được | Đạt: `forecast-api` container healthy, 3h uptime trên image `fa796ac36288` |

## Bằng chứng nghiệm thu

### Quality gate code

```bash
uv run ruff check api scripts tests src dags        # All checks passed
uv run pytest tests/test_sprint_06_api.py tests/test_api.py -q
# -> 35 passed in 0.53s
DATABASE_URL='postgresql://forecast:***@localhost:5432/sku_forecasting' \
  uv run pytest -q
# -> 53 passed, 2 warnings in 4.14s
```

### Live smoke trên image runtime

```text
GET /health                                          200 forecast_ready=true
GET /version                                         200 service=sku-forecast-api
GET /forecast-runs/latest                            200 run_id=sprint-05-dag-20260527
GET /model/current                                   200 mirror latest_run
GET /forecast/SKU-08063?days=3                       200 3 ForecastPoint rows
GET /forecast/top-skus?target_date=2025-09-15&limit=3 200 SKU-11142, SKU-09760, SKU-14516
GET /forecast/summary?target_date=2025-09-15         200 sku_count=15972 total=777.64
GET /forecast/UNKNOWN                                404 {"detail":"Forecast not found for UNKNOWN"}
GET /forecast/top-skus                               422 {"detail":[{"type":"missing","loc":["query","target_date"]}]}
GET /metrics                                         200 prometheus payload (http_requests_total ...)
GET /openapi.json                                    200 7 paths, title="SKU Forecast API", /metrics absent
```

### Load test k6

```bash
make load-test
```

```text
scenarios: (100.00%) 2 scenarios, 60 max VUs, 2m5s max duration
  - warmup    : 10 VUs   30s
  - sustained : 50 VUs   60s (startTime 35s)

THRESHOLDS
  forecast_errors                                            rate=0.00%
  http_req_duration{endpoint:forecast_sku,scenario:warmup}   p(95)=65.69ms   < 500
  http_req_duration{endpoint:health,scenario:warmup}         p(95)=61.89ms   < 100
  http_req_duration{scenario:sustained}                      p(95)=561.84ms  < 1000
  http_req_duration{scenario:warmup}                         p(95)=66.33ms   < 500
  http_req_failed                                            rate=0.55%      < 1%

TOTAL: 15068 checks, 100% succeeded, 0 functional errors
       3767 iterations, 39.4 it/s
       k6 exit code = 0
```

### Security smoke (pytest)

```text
test_health_returns_503_when_database_unavailable           PASS
test_database_outage_does_not_leak_stacktrace               PASS
test_repository_exception_on_protected_route_does_not_leak  PASS
test_item_code_pattern_is_restrictive                       PASS
test_forecast_invalid_item_code_returns_422[SKU 00001]      PASS
test_forecast_invalid_item_code_returns_422[SKU;DROP]       PASS
test_forecast_invalid_item_code_returns_422[SKU'OR'1'='1]   PASS
test_forecast_invalid_item_code_returns_422[A * 65]         PASS
test_settings_cors_origins_default_is_empty                 PASS
test_settings_cors_origins_parses_csv                       PASS
test_cors_middleware_restricts_origin_when_configured       PASS
test_default_settings_have_no_wildcard_cors                 PASS
test_openapi_document_is_well_formed                        PASS
test_openapi_hides_internal_metrics_endpoint                PASS
test_openapi_does_not_leak_database_url                     PASS
```

## Blocker rollout runtime

Container `forecast-api` long-running vẫn dùng image
`sha256:fa796ac36288...` build từ commit `3a81bcb` (Sprint 6 T1). Patch T2-T4
(item_code regex + CORS mount + `.env.example` knob) chưa được rebuild vào
image runtime do cùng blocker quyền Docker host từ Sprint 2 chưa giải quyết.
Acceptance Sprint 6 dùng pattern Sprint 5: pytest + k6 chứng minh hành vi mới
trên code mới; smoke curl chứng minh image hiện hành phục vụ Sprint 5 dataset
ổn định.

`source_git_commit` tại thời điểm nghiệm thu: `3a81bcb` (Sprint 6 T1) +
patch chưa commit cho T2-T4.
