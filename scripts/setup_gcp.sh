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
BIGLAKE_CONNECTION="dealight-biglake"

echo ">> Enabling APIs"
gcloud services enable storage.googleapis.com bigquery.googleapis.com \
  bigqueryconnection.googleapis.com --project "${PROJECT_ID}"

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

echo ">> Setting lifecycle rules (landing/ deleted after 30d, raw/ to Nearline after 90d)"
LIFECYCLE_FILE="$(mktemp)"
cat > "${LIFECYCLE_FILE}" <<'JSON'
{
  "rule": [
    {"action": {"type": "Delete"},
     "condition": {"age": 30, "matchesPrefix": ["landing/"]}},
    {"action": {"type": "SetStorageClass", "storageClass": "NEARLINE"},
     "condition": {"age": 90, "matchesPrefix": ["raw/"]}}
  ]
}
JSON
gcloud storage buckets update "gs://${BUCKET}" --lifecycle-file="${LIFECYCLE_FILE}"
rm -f "${LIFECYCLE_FILE}"

echo ">> Granting roles (BigQuery dataEditor scoped to the dataset, not the project)"
gcloud storage buckets add-iam-policy-binding "gs://${BUCKET}" \
  --member "serviceAccount:${SA_EMAIL}" \
  --role roles/storage.objectAdmin
bq --project_id "${PROJECT_ID}" query --use_legacy_sql=false \
  "GRANT \`roles/bigquery.dataEditor\` ON SCHEMA \`${PROJECT_ID}.${DATASET}\` TO 'serviceAccount:${SA_EMAIL}'"
gcloud projects add-iam-policy-binding "${PROJECT_ID}" \
  --member "serviceAccount:${SA_EMAIL}" \
  --role roles/bigquery.jobUser --condition=None
# Drop the broad project-level grant if a previous version of this script added it.
gcloud projects remove-iam-policy-binding "${PROJECT_ID}" \
  --member "serviceAccount:${SA_EMAIL}" \
  --role roles/bigquery.dataEditor --condition=None 2>/dev/null || true

echo ">> Creating BigLake connection ${BIGLAKE_CONNECTION} for Iceberg tables (idempotent)"
bq --project_id "${PROJECT_ID}" mk --connection --location "${REGION}" \
  --connection_type CLOUD_RESOURCE "${BIGLAKE_CONNECTION}" 2>/dev/null || true
CONNECTION_SA="$(bq --project_id "${PROJECT_ID}" show --connection --format json \
  "${PROJECT_ID}.${REGION}.${BIGLAKE_CONNECTION}" \
  | python3 -c 'import json,sys; print(json.load(sys.stdin)["cloudResource"]["serviceAccountId"])')"
echo ">> Granting bucket write access to connection SA ${CONNECTION_SA}"
gcloud storage buckets add-iam-policy-binding "gs://${BUCKET}" \
  --member "serviceAccount:${CONNECTION_SA}" \
  --role roles/storage.objectAdmin
# The pipeline SA must be allowed to reference the connection in CREATE TABLE DDL.
gcloud projects add-iam-policy-binding "${PROJECT_ID}" \
  --member "serviceAccount:${SA_EMAIL}" \
  --role roles/bigquery.connectionUser --condition=None

if [[ -f "${KEY_PATH}" ]]; then
  echo ">> Key already exists at ${KEY_PATH} — skipping (delete it to force a new key)"
else
  echo ">> Creating key at ${KEY_PATH}"
  mkdir -p "$(dirname "${KEY_PATH}")"
  gcloud iam service-accounts keys create "${KEY_PATH}" \
    --iam-account "${SA_EMAIL}"
  chmod 644 "${KEY_PATH}"  # containers run as non-root uids and must read it
fi

cat <<EOF

Done. Add to your .env:
  GCP_PROJECT_ID=${PROJECT_ID}
  GCS_BUCKET=${BUCKET}
  BQ_DATASET=${DATASET}
  BQ_BIGLAKE_CONNECTION=${BIGLAKE_CONNECTION}
  BQ_LOCATION=${REGION}

Then rebuild + restart:
  docker compose -f infra/docker-compose.yml up -d --build forecast-api airflow-webserver airflow-scheduler
EOF
