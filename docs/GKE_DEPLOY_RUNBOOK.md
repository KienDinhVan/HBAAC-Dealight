# GKE Deploy/Destroy Runbook

Toàn bộ platform (forecast-api, web, Airflow, MLflow, Prometheus, Grafana) chạy trên
GKE Autopilot `dealight-prod` (region `asia-southeast1`, project `gen-lang-client-0222711301`).
Hạ tầng: Terraform modules + Terragrunt (`infra/terraform`, `infra/live`). CD: ArgoCD GitOps
(`argocd/`, `k8s/`). CI/CD: GitHub Actions trên ARC self-hosted runners in-cluster
(`.github/workflows/ci.yml`, `cd.yml`). Không có JSON key nào — mọi auth GCP qua Workload Identity.

## 1. Prerequisites (làm tay, 1 lần)

1. **GitHub App cho ARC**: GitHub → Settings → Developer settings → GitHub Apps → New GitHub App.
   - Permissions (Repository): **Actions RW, Administration RW, Metadata RO**.
   - Install app vào repo `KienDinhVan/HBAAC-Dealight`.
   - Ghi lại **App ID**, **Installation ID**, tải **private key** `.pem`.
2. **GitHub PAT** (classic, scope `repo`) — ArgoCD đọc repo + Kaniko clone repo private.

## 2. Bootstrap (idempotent)

```bash
scripts/bootstrap_gke_platform.sh gen-lang-client-0222711301
```

Nạp giá trị secret (1 lần; `printf` tránh newline thừa):

```bash
P=gen-lang-client-0222711301
printf '%s' 'sk-or-...'          | gcloud secrets versions add openrouter-api-key --data-file=- --project $P
printf '%s' 'https://discord...' | gcloud secrets versions add discord-webhook-url --data-file=- --project $P
printf '%s' 'ghp_...'            | gcloud secrets versions add github-repo-pat --data-file=- --project $P
printf '%s' '<APP_ID>'           | gcloud secrets versions add arc-github-app-id --data-file=- --project $P
printf '%s' '<INSTALLATION_ID>'  | gcloud secrets versions add arc-github-app-installation-id --data-file=- --project $P
gcloud secrets versions add arc-github-app-private-key --data-file=/path/to/app.pem --project $P
```

## 3. Provision hạ tầng (~30–40 phút)

```bash
cd infra/live/prod
terragrunt run-all apply
```

Terragrunt tự resolve dependency: `network` → (`gke`, `cloudsql`, `memorystore`, `registry`)
→ `iam` → `k8s-platform` → (`argocd`, `arc`). GKE (~15ph) và Cloud SQL (~10ph) là lâu nhất.

Sau apply, lấy credentials:

```bash
gcloud container clusters get-credentials dealight-prod --region asia-southeast1 --project gen-lang-client-0222711301
```

## 4. First deploy (qua CD, không build tay)

Pods trong ns `dealight` sẽ **ImagePullBackOff** với tag `bootstrap` cho tới khi CD chạy lần đầu —
đây là trạng thái mong đợi.

1. Push code lên `main` (bất kỳ commit nào chạm paths của `cd.yml`).
2. `cd.yml` chạy trên runner `dealight-gke` (pod ARC): Kaniko build 5 images
   (`forecast-api`, `web`, `mlflow`, `airflow-base`, `airflow`) → push Artifact Registry
   → `kustomize edit set image` bump tag `<git-sha>` trong `k8s/overlays/prod`
   → commit `[skip ci]` → ArgoCD sync (≤3 phút) → smoke `/health`.
3. Theo dõi: GitHub Actions tab, hoặc `kubectl -n ci-builds get jobs`,
   `kubectl -n dealight get pods -w`.

Lưu ý smoke: chỉ chứng minh service healthy sau sync — `/version` không chứa git sha nên
không phân biệt image cũ/mới.

## 5. Access

| Service | Cách truy cập |
|---|---|
| Web UI | `kubectl -n dealight get ingress web` → mở `http://<EXTERNAL-IP>` (GCLB cần ~5–10ph sau lần tạo đầu) |
| API (qua web) | `http://<EXTERNAL-IP>/api/health` |
| Airflow | `kubectl -n dealight port-forward svc/airflow-webserver 8080:8080` → `http://localhost:8080` (user `admin`) |
| Grafana | `kubectl -n dealight port-forward svc/grafana 3000:3000` (user `admin`) |
| MLflow | `kubectl -n dealight port-forward svc/mlflow 5000:5000` |
| Prometheus | `kubectl -n dealight port-forward svc/prometheus 9090:9090` |
| ArgoCD | `kubectl -n argocd port-forward svc/argocd-server 8081:80` → `http://localhost:8081` (user `admin`) |

Passwords:

```bash
# ArgoCD admin
kubectl -n argocd get secret argocd-initial-admin-secret -o jsonpath='{.data.password}' | base64 -d; echo
# Airflow admin / Grafana admin (Terraform random)
kubectl -n dealight get secret platform-secrets -o jsonpath='{.data.AIRFLOW_ADMIN_PASSWORD}' | base64 -d; echo
kubectl -n dealight get secret platform-secrets -o jsonpath='{.data.GF_SECURITY_ADMIN_PASSWORD}' | base64 -d; echo
```

## 6. Rollback

```bash
git revert <commit "ci(cd): deploy ...">   # rồi push main
```

ArgoCD tự sync về image tag cũ trong ≤3 phút. (Revert chỉ chạm `k8s/overlays/prod` —
ngoài paths filter của cd.yml — nên không kích hoạt build lại, chỉ ArgoCD sync.)

## 7. Destroy (tiết kiệm chi phí)

```bash
cd infra/live/prod
terragrunt run-all destroy
```

- **Không mất**: data lake GCS (`…-dealight-data`), BigQuery dataset `dealight`, SA
  `dealight-pipeline`, BigLake connection (nằm ngoài Terraform, do `scripts/setup_gcp.sh` quản),
  Secret Manager secrets (shell do bootstrap tạo), tfstate bucket.
- **Mất** (chấp nhận): Cloud SQL DBs (airflow/mlflow/sku_forecasting metadata), Redis cache,
  MLflow artifacts bucket (`force_destroy`), images trong Artifact Registry.
- Dựng lại: mục 3 + 4 (secrets vẫn còn, không cần nạp lại).

## 8. Chi phí

~$200–290/tháng khi bật liên tục (GKE Autopilot pods + Cloud SQL db-g1-small + Memorystore 1GB
+ GCLB). Destroy khi không dùng — dựng lại mất ~40 phút + 1 lần CD chạy.

## Sprint 09 — auth + model promotion

One-time setup after deploying the sprint-09 image:

```bash
# 1. Add secrets (generate a strong JWT secret + user passwords)
kubectl -n dealight patch secret platform-secrets --type merge -p "{\"stringData\":{
  \"JWT_SECRET\": \"$(openssl rand -hex 32)\",
  \"SEED_DEV_PASSWORD\": \"<choose>\",
  \"SEED_MANAGER_PASSWORD\": \"<choose>\"
}}"
kubectl -n dealight rollout restart deploy/forecast-api

# 2. Apply the schema migration (same pattern as sprint 07)
POD=$(kubectl -n dealight get pod -l app=forecast-api -o jsonpath='{.items[0].metadata.name}')
kubectl -n dealight cp scripts/sprint_09_auth_promotion_schema.sql $POD:/tmp/s09.sql
kubectl -n dealight exec $POD -- python -c "import os,psycopg; c=psycopg.connect(os.environ['DATABASE_URL']); c.execute(open('/tmp/s09.sql').read()); c.commit()"

# 3. Seed users (reads SEED_* + DATABASE_URL from the pod env)
kubectl -n dealight exec $POD -- python scripts/seed_users.py

# 4. Bootstrap @production aliases (once)
kubectl -n dealight exec $POD -- python scripts/bootstrap_model_aliases.py
```

Smoke checklist:
- Login as dev1 and manager1 (http://136.68.214.220) — dev has no Approvals page.
- Models page shows versions of {dataset}-forecaster with alias badges.
- Dev requests promote on the @staging candidate; manager approves in Approvals.
- Next Predict CSV run uses the new version (check forecast-api logs for
  "Loading production model models:/...@production").
- Dev calling POST /api/v1/promotion-requests/{id}/approve directly gets 403.
