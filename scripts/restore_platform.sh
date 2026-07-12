#!/usr/bin/env bash
set -euo pipefail

if [[ "${1:-}" != "--confirm-restore" || -z "${2:-}" ]]; then
  printf 'Usage: %s --confirm-restore data/backups/<timestamp>\n' "$0" >&2
  exit 2
fi

ENV_FILE="${ENV_FILE:-.env}"
COMPOSE_FILE="${COMPOSE_FILE:-infra/docker-compose.yml}"
SOURCE="$(cd "$2" && pwd)"

set -a
source "${ENV_FILE}"
set +a

test -f "${SOURCE}/sku_forecasting.dump"
test -f "${SOURCE}/mlflow.dump"
docker compose --env-file "${ENV_FILE}" -f "${COMPOSE_FILE}" exec -T postgres \
  pg_restore -U "${POSTGRES_USER:-forecast}" -d "${POSTGRES_DB:-sku_forecasting}" \
  --clean --if-exists < "${SOURCE}/sku_forecasting.dump"
docker compose --env-file "${ENV_FILE}" -f "${COMPOSE_FILE}" exec -T postgres \
  pg_restore -U "${POSTGRES_USER:-forecast}" -d mlflow --clean --if-exists \
  < "${SOURCE}/mlflow.dump"
docker run --rm --network sku-demand-forecasting_default \
  -e MC_HOST_platform="http://${MINIO_ROOT_USER}:${MINIO_ROOT_PASSWORD}@minio:9000" \
  -v "${SOURCE}/minio:/backup:ro" minio/mc:latest \
  mirror /backup/mlflow "platform/${MLFLOW_BUCKET:-mlflow}"

printf 'Restore completed from %s\n' "${SOURCE}"
