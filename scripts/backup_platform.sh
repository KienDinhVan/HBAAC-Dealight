#!/usr/bin/env bash
set -euo pipefail

ENV_FILE="${ENV_FILE:-.env}"
COMPOSE_FILE="${COMPOSE_FILE:-infra/docker-compose.yml}"
BACKUP_ROOT="${BACKUP_ROOT:-data/backups}"
STAMP="$(date -u +%Y%m%dT%H%M%SZ)"
DEST="${BACKUP_ROOT}/${STAMP}"

if [[ ! -f "${ENV_FILE}" ]]; then
  printf 'Missing environment file: %s\n' "${ENV_FILE}" >&2
  exit 1
fi

set -a
source "${ENV_FILE}"
set +a
mkdir -p "${DEST}/minio"
DEST="$(cd "${DEST}" && pwd)"

docker compose --env-file "${ENV_FILE}" -f "${COMPOSE_FILE}" exec -T postgres \
  pg_dump -U "${POSTGRES_USER:-forecast}" -d "${POSTGRES_DB:-sku_forecasting}" \
  --format=custom > "${DEST}/sku_forecasting.dump"
docker compose --env-file "${ENV_FILE}" -f "${COMPOSE_FILE}" exec -T postgres \
  pg_dump -U "${POSTGRES_USER:-forecast}" -d mlflow --format=custom \
  > "${DEST}/mlflow.dump"
docker run --rm --network sku-demand-forecasting_default \
  -e MC_HOST_platform="http://${MINIO_ROOT_USER}:${MINIO_ROOT_PASSWORD}@minio:9000" \
  -v "${DEST}/minio:/backup" minio/mc:latest \
  mirror "platform/${MLFLOW_BUCKET:-mlflow}" /backup/mlflow

printf 'Backup written to %s\n' "${DEST}"
