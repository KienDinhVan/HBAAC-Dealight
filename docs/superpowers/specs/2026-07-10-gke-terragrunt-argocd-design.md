# GKE + Terraform/Terragrunt + ArgoCD + GitHub Actions (ARC) — Design

**Date:** 2026-07-10
**Status:** Approved pending user review

## Goal

Deploy toàn bộ platform (forecast-api, web, Airflow, MLflow, Prometheus, Grafana)
lên GKE Autopilot, hạ tầng quản lý bằng Terraform modules + Terragrunt (1 env `prod`),
CD theo GitOps (ArgoCD), CI bằng GitHub Actions chạy trên self-hosted runners
trong cluster (Actions Runner Controller) với GKE Workload Identity — không dùng
JSON key ở bất kỳ đâu trong cluster/CI.

## Decisions (đã chốt với user)

| Quyết định | Lựa chọn |
|---|---|
| GKE mode | **Autopilot**, region `asia-southeast1`, project `gen-lang-client-0222711301` |
| Stateful | **Managed**: Cloud SQL Postgres 16 (private IP), Memorystore Redis, GCS (MLflow artifacts) — bỏ Postgres/Redis/MinIO container |
| Environments | 1 env **prod** (cấu trúc Terragrunt vẫn modules + live/ chuẩn) |
| CD | **ArgoCD** app-of-apps, auto-sync + prune + self-heal, manifests Kustomize trong repo này |
| CI | **GitHub Actions + self-hosted runners trên GKE (ARC) + Workload Identity**; build image bằng Kaniko (Autopilot cấm DinD) |
| Airflow | Giữ **LocalExecutor** (webserver + scheduler, image hiện có) |
| Chi phí | Chấp nhận bật liên tục ~**$200–290/tháng**; `terragrunt run-all destroy` khi không cần — data lake GCS/BigQuery nằm ngoài cluster, không mất |
| Cleanup | Đã xoá: zips, `dpis-finops-agent-develop/`, vLLM compose, Ansible + deploy.yml cũ (commit `chore: remove...`) |

## Kiến trúc

```
GitHub repo ──push──> GitHub Actions (runner = pod ARC trong GKE)
                         │  pytest → Kaniko build 3 images → Artifact Registry
                         │  → kustomize edit set image → git commit [skip ci]
                         ▼
                      ArgoCD (in-cluster) ──auto-sync──> namespace dealight
                         │
   GKE Autopilot ────────┤ forecast-api / web / airflow-webserver / airflow-scheduler
                         │ mlflow / prometheus / grafana
                         ▼
   Cloud SQL (private IP: sku_forecasting, airflow, mlflow DBs)
   Memorystore Redis (online store)  ·  GCS (data lake + mlflow artifacts)
   BigQuery Iceberg (offline store, đã có từ DE pipeline)
```

## Repo layout mới

```
infra/terraform/modules/
  network/             # VPC, subnet, Private Service Access
  gke-autopilot/       # cluster + WI pool bật sẵn
  cloudsql/            # PG16 private IP, 3 DBs + users, password vào Secret Manager
  memorystore/         # Redis basic 1GB
  artifact-registry/   # docker repo "dealight"
  workload-identity/   # GSA↔KSA bindings (generic, gọi nhiều lần)
  argocd/              # helm_release argo-cd + Application app-of-apps
  arc/                 # helm_release gha-runner-scale-set-controller + scale set repo này
infra/live/
  terragrunt.hcl       # remote state GCS bucket <project>-tfstate, provider/versions chung
  prod/<module>/terragrunt.hcl  # inputs + dependency giữa các stack
k8s/
  base/<service>/      # Deployment/Service/(Ingress)/ConfigMap per service
  overlays/prod/       # kustomization: image tags (CI bump), replicas, env
argocd/
  root-app.yaml        # app-of-apps
  apps/<service>.yaml  # 1 Application / service
.github/workflows/
  ci.yml               # giữ: lint + pytest (runner: arc scale set)
  cd.yml               # mới: build (Kaniko) + push AR + bump kustomize tag
```

## Chi tiết từng phần

### Terraform/Terragrunt
- Backend: bucket `gen-lang-client-0222711301-tfstate` (tạo tay 1 lần hoặc script bootstrap), prefix theo path Terragrunt.
- `terragrunt run-all apply` dựng theo dependency: network → (gke, cloudsql, memorystore) → registry → argocd/arc.
- Không import các resource GCP có sẵn của DE pipeline (bucket data, dataset, SA, BigLake connection) — chúng do `scripts/setup_gcp.sh` quản, spec này không đụng (tránh 2 nguồn quản lý 1 resource).

### Workload Identity mapping (không JSON key)
| KSA (ns dealight) | GSA | Quyền |
|---|---|---|
| `airflow` | `dealight-pipeline@…` (đã có) | GCS objectAdmin bucket data, BQ dataEditor dataset, jobUser, connection delegate (đã có) |
| `forecast-api` | `dealight-pipeline@…` (tái dùng) | upload landing/, trigger DAG qua HTTP nội bộ |
| `mlflow` | `dealight-mlflow@…` (mới) | objectAdmin bucket `…-mlflow-artifacts` (mới) |
| `arc-runner` | `dealight-ci@…` (mới) | Artifact Registry writer; push git bằng GITHUB_TOKEN của workflow |

### Workloads (namespace `dealight`)
- Mỗi service: Deployment + Service; probe lấy từ healthcheck compose hiện có.
- `airflow-init` Job (db migrate + tạo admin user) chạy như ArgoCD PreSync hook.
- Env đổi so với compose: `DATABASE_URL`/`AIRFLOW__DATABASE__SQL_ALCHEMY_CONN`/MLflow backend → Cloud SQL private IP (password từ k8s Secret); `REDIS_URL` → Memorystore IP; MLflow `--default-artifact-root gs://…-mlflow-artifacts/`; bỏ `GOOGLE_APPLICATION_CREDENTIALS` + volume secrets (WI thay thế); bỏ hẳn các env MinIO.
- Prometheus/Grafana: giữ config/dashboards hiện có qua ConfigMap + PVC nhỏ cho Grafana.
- Ingress GCLB duy nhất (`web` mặc định, `/api/*` → forecast-api). Airflow UI, Grafana, ArgoCD UI: `kubectl port-forward` (không expose công khai).
- `infra/docker-compose.yml` GIỮ NGUYÊN cho local dev — GKE là môi trường prod song song.

### Secrets
- Terraform tạo Secret Manager secrets (DB passwords random, ARC GitHub App key do user cung cấp, Discord webhook, OpenRouter key copy từ .env) và render thành k8s Secret qua provider kubernetes (module argocd/arc và stack riêng `k8s-secrets`).
- Manifest trong git chỉ tham chiếu `secretKeyRef` — không có secret value nào trong repo.

### CI/CD flow
1. `ci.yml`: push/PR → runner ARC → `uv run pytest tests/ -q` + lint + `terraform fmt -check`/`terragrunt hclfmt --check`.
2. `cd.yml`: push `main` (paths: api/, src/, dags/, frontend/, infra/airflow/) → 3 job Kaniko build/push `{api,web,airflow}:<git-sha>` → job bump: `kustomize edit set image` trong `k8s/overlays/prod` + commit `[skip ci]` → ArgoCD sync trong ≤3 phút.
3. Smoke sau sync: workflow chờ rồi curl Ingress IP `/health`.
4. Rollback = `git revert` commit bump tag (ArgoCD tự đưa về image cũ).

### Testing
- Unit: pytest hiện có (94 pass) chạy mỗi push.
- Static: `terraform validate` per module, `kustomize build` dry-run trong CI.
- Post-deploy: smoke `/health`, `/version`; upload CSV nhỏ qua `/ingest/upload` để chứng minh DAG chạy trên GKE (thủ công trong runbook demo).

## Out of scope
- Env dev/staging thứ hai; KubernetesExecutor; ESO (External Secrets Operator);
  expose Airflow/Grafana/ArgoCD công khai; HPA/autoscaling tùy chỉnh; import
  resource DE-pipeline có sẵn vào Terraform state.

## Rủi ro chính
- Autopilot cấm privileged → mọi build phải qua Kaniko (đã thiết kế).
- Cloud SQL private IP yêu cầu Private Service Access đúng — module network làm chuẩn ngay từ đầu.
- ARC cần GitHub App (user tạo, cấp quyền repo) — bước thủ công duy nhất ngoài Terraform.
- Chi phí: cluster bật liên tục theo lựa chọn user; destroy runbook có trong docs.
