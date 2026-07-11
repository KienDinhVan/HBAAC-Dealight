#!/usr/bin/env bash
# One-time bootstrap for the GKE platform: APIs, tfstate bucket, secret shells.
# Usage: scripts/bootstrap_gke_platform.sh <project-id>
set -euo pipefail

PROJECT_ID="${1:?Usage: scripts/bootstrap_gke_platform.sh <project-id>}"
REGION="asia-southeast1"
BUCKET="${PROJECT_ID}-tfstate"

echo ">> Enabling APIs"
gcloud services enable container.googleapis.com sqladmin.googleapis.com \
  redis.googleapis.com artifactregistry.googleapis.com \
  servicenetworking.googleapis.com secretmanager.googleapis.com \
  iamcredentials.googleapis.com --project "${PROJECT_ID}"

echo ">> Creating tfstate bucket gs://${BUCKET} (idempotent)"
gcloud storage buckets create "gs://${BUCKET}" --project "${PROJECT_ID}" \
  --location "${REGION}" --uniform-bucket-level-access 2>/dev/null || true
gcloud storage buckets update "gs://${BUCKET}" --versioning

echo ">> Creating secret shells (values added manually below)"
for s in openrouter-api-key discord-webhook-url github-repo-pat \
         arc-github-app-id arc-github-app-installation-id arc-github-app-private-key; do
  gcloud secrets create "$s" --replication-policy=automatic \
    --project "${PROJECT_ID}" 2>/dev/null || true
done

cat <<EOF

Done. Add secret values once (printf avoids trailing newline):
  printf '%s' 'sk-or-...'          | gcloud secrets versions add openrouter-api-key --data-file=- --project ${PROJECT_ID}
  printf '%s' 'https://discord...' | gcloud secrets versions add discord-webhook-url --data-file=- --project ${PROJECT_ID}
  printf '%s' 'ghp_...'            | gcloud secrets versions add github-repo-pat --data-file=- --project ${PROJECT_ID}
  printf '%s' '<APP_ID>'           | gcloud secrets versions add arc-github-app-id --data-file=- --project ${PROJECT_ID}
  printf '%s' '<INSTALLATION_ID>'  | gcloud secrets versions add arc-github-app-installation-id --data-file=- --project ${PROJECT_ID}
  gcloud secrets versions add arc-github-app-private-key --data-file=/path/to/app.pem --project ${PROJECT_ID}
EOF
