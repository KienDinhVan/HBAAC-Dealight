# Multi-dataset Forecast Platform — Design

Date: 2026-07-14
Status: Approved (backend scope; web UI dataset-selector deferred)

## Problem

The platform is hardcoded to one dataset (HBAAC SKU sales): `dags/dag_01..07`
point at `data/raw/train.csv` and BigQuery table `sales_daily`; `src/hbacc_prj`
modules assume SKU semantics. Goal: ingest from many sources (batch files with
differing schemas, external databases, REST APIs) and run the same
demand-forecast pipeline per dataset, onboarded via YAML config in the repo
(GitOps).

Out of scope: streaming sources, non-forecast ML problem types, UI/API-based
dataset registration, web UI dataset selector (deferred until backend done).

## Approach (chosen: A — canonical schema + dataset registry)

Every source is normalized into one canonical schema after ingest; the
feature/train/forecast pipeline only knows the canonical schema. Alternatives
rejected: per-dataset pipeline copies (drift, n× maintenance), full plugin
platform with dynamic registration (over-engineered for a single problem type).

## 1. Canonical schema

| column    | type   | meaning                                   |
|-----------|--------|--------------------------------------------|
| entity_id | STRING | forecast unit ("SKU123", "store_45|item_9") |
| ds        | DATE   | day                                        |
| quantity  | FLOAT  | value to forecast                          |
| attrs     | JSON   | optional attributes (price, category, ...) |

Feature engineering (lags, rolling, calendar) reads the first three columns
plus attrs keys explicitly whitelisted in the dataset config.

## 2. Dataset config — `datasets/<name>.yaml`

```yaml
name: hbaac_sku                  # identifier: DAG/table/model suffix
source:
  type: file                     # file | database | api
  location: gs://.../raw/train.csv
  format: csv
  # database: secret_ref: pg-conn-xxx, query: "SELECT ..."
  # api:      secret_ref: api-key-xxx, endpoint, pagination
mapping:                         # source column -> canonical
  entity_id: sku_code
  ds: order_date
  quantity: qty_sold
  attrs: [price, category]
schedule: "0 2 * * *"            # ingest + forecast cadence
training:
  schedule: "0 4 * * 0"          # weekly retrain
  validation_days: 28
  min_wape_improvement: 0.02     # promote threshold
postprocess: hbaac_key_skus      # optional named hook
```

Credentials are never in YAML — only references to K8s Secrets / GCP Secret
Manager entries.

## 3. Connectors — `src/hbacc_prj/connectors/`

Three modules (`file.py`, `database.py`, `api.py`) with one interface:
`fetch(config) -> raw DataFrame`. A shared `normalize(df, mapping)` maps to
the canonical schema and validates (missing columns, wrong dtypes, duplicate
entity/ds pairs) — fail fast with a clear per-column/row error report.

## 4. Storage

- Raw: `gs://<bucket>/raw/{dataset}/{batch_id}/...` (existing pattern + dataset level)
- BigQuery Iceberg: one table per dataset `{dataset}_daily`. Existing
  `sales_daily` stays as-is and becomes the table for dataset `hbaac_sku`
  (no breaking migration).

## 5. DAG factory

`dags/factory.py` reads `datasets/*.yaml` and generates per dataset:
`ingest_{name}`, `features_{name}`, `train_{name}`, `forecast_{name}`,
`monitor_{name}`. Backfill/platform-health DAGs remain shared. Legacy
`dag_01..05` are deleted only after `hbaac_sku.yaml` runs green and matches
the old pipeline (golden test).

## 6. `hbacc_prj` refactor

- `data.py`, `features.py`, `training.py`, `forecasting.py` take a
  `DatasetConfig` (dataclass parsed from YAML) instead of hardcoded
  paths/table names; core vocabulary `sku` -> `entity_id`.
- HBAAC-specific logic (`postprocess_key_skus.py`, `segments.py`) becomes an
  optional named hook declared in YAML (`postprocess: hbaac_key_skus`);
  datasets without the key skip it.

## 7. MLflow + forecast-api

- MLflow: experiment `{dataset}`, registered model `{dataset}-forecaster`.
  Current model keeps its name; alias to the new name.
- API: existing routes unchanged (implicitly `hbaac_sku`) for backward
  compatibility — the deployed web UI keeps working untouched. New routes
  `/api/v1/{dataset}/forecast/...` and `/api/v1/datasets`.

## 8. Error handling

- Invalid/missing YAML fields: DAG factory skips that dataset and logs a
  clear error; other datasets' DAGs are unaffected.
- Source data violating canonical schema: fail in normalize with a
  column/row error report; write a monitoring event (sprint-07 tables).
- Connector network/API failures: Airflow retries + Discord webhook alert
  (existing mechanism).

## 9. Testing

- Unit: normalize/mapping fixtures per source type; YAML config validation.
- Integration: a second sample dataset (small CSV, deliberately different
  schema) in-repo proves "new dataset = one YAML file" end-to-end.
- Golden test: `hbaac_sku` through the new pipeline must match the old
  pipeline's output (WAPE unchanged).

## 10. Implementation order

1. `DatasetConfig` + canonical schema + normalize/validate (+ tests)
2. `file` connector + convert HBAAC to `datasets/hbaac_sku.yaml`
3. DAG factory; run old and new DAGs side by side -> golden test -> delete old
4. `database` + `api` connectors (+ second sample dataset)
5. Multi-dataset MLflow + API routes
6. (Deferred) Web UI dataset selector: dropdown fed by `/api/v1/datasets`,
   prefix in `frontend/src/lib/api.ts`, dynamic entity labels.
