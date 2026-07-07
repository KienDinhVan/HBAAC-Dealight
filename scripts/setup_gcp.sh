#!/usr/bin/env bash
# One-time GCP setup for the DE GCS pipeline (DE_arch.png).
# Usage: scripts/setup_gcp.sh <project-id> [bucket-name]
set -euo pipefail

PROJECT_ID="${1:?Usage: scripts/setup_gcp.sh <project-id> [bucket-name]}"
BUCKET="${2:-${PROJECT_ID}-dealight-data}"
REGION="asia-southeast1"
DATASET="dealight"
SA_NAME="dealight-pipeline"
SA_EMAIL="${SA_NAME}@${PROJECT_ID}.iam.gserviceaccount.com"
KEY_PATH="infra/secrets/gcp-key.json"

echo ">> Enabling APIs"
gcloud services enable storage.googleapis.com bigquery.googleapis.com \
  --project "${PROJECT_ID}"

echo ">> Creating service account ${SA_EMAIL} (idempotent)"
gcloud iam service-accounts create "${SA_NAME}" \
  --project "${PROJECT_ID}" \
  --display-name "Dealight DE pipeline" 2>/dev/null || true

echo ">> Creating bucket gs://${BUCKET} (idempotent)"
gcloud storage buckets create "gs://${BUCKET}" \
  --project "${PROJECT_ID}" \
  --location "${REGION}" \
  --uniform-bucket-level-access 2>/dev/null || true

echo ">> Creating BigQuery dataset ${DATASET} (idempotent)"
bq --project_id "${PROJECT_ID}" --location "${REGION}" mk --dataset \
  "${PROJECT_ID}:${DATASET}" 2>/dev/null || true

echo ">> Granting roles"
gcloud storage buckets add-iam-policy-binding "gs://${BUCKET}" \
  --member "serviceAccount:${SA_EMAIL}" \
  --role roles/storage.objectAdmin
gcloud projects add-iam-policy-binding "${PROJECT_ID}" \
  --member "serviceAccount:${SA_EMAIL}" \
  --role roles/bigquery.dataEditor --condition=None
gcloud projects add-iam-policy-binding "${PROJECT_ID}" \
  --member "serviceAccount:${SA_EMAIL}" \
  --role roles/bigquery.jobUser --condition=None

echo ">> Creating key at ${KEY_PATH}"
mkdir -p "$(dirname "${KEY_PATH}")"
gcloud iam service-accounts keys create "${KEY_PATH}" \
  --iam-account "${SA_EMAIL}"

cat <<EOF

Done. Add to your .env:
  GCP_PROJECT_ID=${PROJECT_ID}
  GCS_BUCKET=${BUCKET}
  BQ_DATASET=${DATASET}

Then rebuild + restart:
  docker compose -f infra/docker-compose.yml up -d --build forecast-api airflow-webserver airflow-scheduler
EOF
