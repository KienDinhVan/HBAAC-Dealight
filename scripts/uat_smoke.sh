#!/usr/bin/env bash
set -euo pipefail

API_URL="${API_URL:-http://localhost:8000}"
PROMETHEUS_URL="${PROMETHEUS_URL:-http://localhost:9090}"
GRAFANA_URL="${GRAFANA_URL:-http://localhost:3000}"

curl --fail --silent --show-error "${API_URL}/health"
curl --fail --silent --show-error "${API_URL}/forecast-runs/latest"
curl --fail --silent --show-error "${API_URL}/forecast/SKU-08063?days=3"
curl --fail --silent --show-error "${API_URL}/forecast/summary?target_date=2025-09-15"
curl --fail --silent --show-error "${API_URL}/forecast/top-skus?target_date=2025-09-15&limit=3"
curl --fail --silent --show-error "${API_URL}/monitoring/latest"
curl --fail --silent --show-error "${API_URL}/metrics" | grep -q "forecast_latest_row_count"
curl --fail --silent --show-error "${PROMETHEUS_URL}/-/ready"
curl --fail --silent --show-error "${GRAFANA_URL}/api/health"
printf '\nUAT smoke requests passed.\n'
