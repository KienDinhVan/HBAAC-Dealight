# GKE + Terragrunt + ArgoCD + ARC Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Deploy toàn bộ platform (forecast-api, web, Airflow, MLflow, Prometheus, Grafana) lên GKE Autopilot với hạ tầng Terraform/Terragrunt, CD GitOps bằng ArgoCD, CI GitHub Actions trên self-hosted ARC runners in-cluster.

**Architecture:** Terraform modules + Terragrunt live/prod tạo network/GKE/Cloud SQL/Memorystore/AR/IAM-WI; workloads là Kustomize manifests (`k8s/`) do ArgoCD app-of-apps sync; CI build image bằng Kaniko Jobs (Autopilot cấm DinD) rồi bump tag kustomize.

**Tech Stack:** Terraform ≥1.7 (google ~>6.0, kubernetes ~>2.33, helm ~>2.16, random ~>3.6), Terragrunt ≥0.55, GKE Autopilot, ArgoCD (argo-helm), ARC gha-runner-scale-set 0.9.3, Kaniko v1.23.2, Kustomize.

**Spec:** `docs/superpowers/specs/2026-07-10-gke-terragrunt-argocd-design.md`

## Global Constraints

- Project `gen-lang-client-0222711301`, region `asia-southeast1`, cluster `dealight-prod`, namespace app `dealight`.
- Repo: `https://github.com/KienDinhVan/HBAAC-Dealight.git` (owner `KienDinhVan`).
- **Không JSON key ở bất kỳ đâu** — mọi auth GCP trong cluster/CI qua Workload Identity.
- **Không import/không đụng** resource DE-pipeline có sẵn: bucket `gen-lang-client-0222711301-dealight-data`, dataset `dealight`, SA `dealight-pipeline@…`, connection `dealight-biglake` — chỉ `data` source.
- tfstate: bucket `gen-lang-client-0222711301-tfstate`, prefix = `path_relative_to_include()`.
- Artifact Registry: `asia-southeast1-docker.pkg.dev/gen-lang-client-0222711301/dealight`; images `forecast-api`, `web`, `airflow-base`, `airflow`, `mlflow`; tag = git SHA.
- `infra/docker-compose.yml` giữ nguyên (local dev). Mọi kustomization phải `kustomize build` sạch; mọi module phải `terraform validate` sạch.
- Chạy lệnh terraform qua terragrunt tại `infra/live/prod/<stack>`; không chạy `terraform apply` trực tiếp trong module.

## Prerequisites (user làm tay, 1 lần)

1. GitHub App cho ARC: Settings → Developer settings → GitHub Apps → New. Permissions (Repository): Actions RW, Administration RW, Metadata RO. Install vào repo `KienDinhVan/HBAAC-Dealight`. Ghi lại App ID, Installation ID, tải private key `.pem`.
2. GitHub PAT (classic, scope `repo`) cho ArgoCD đọc repo + CI push bump commit.
3. Sau Task 1 chạy: nạp giá trị secret (lệnh in ra từ script bootstrap).

---

### Task 1: Bootstrap script + Terragrunt root + gitignore

**Files:**
- Create: `scripts/bootstrap_gke_platform.sh`
- Create: `infra/live/terragrunt.hcl`
- Modify: `.gitignore` (thêm block terraform)

**Interfaces:**
- Produces: bucket tfstate, 6 Secret Manager secrets (shell rỗng), root include cho mọi stack với `inputs = {project_id, region, repo_url}` và generated `versions.tf` (providers google/random/kubernetes/helm).

- [ ] **Step 1: Viết `scripts/bootstrap_gke_platform.sh`**

```bash
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
```

- [ ] **Step 2: `bash -n scripts/bootstrap_gke_platform.sh` → không lỗi; chạy `scripts/bootstrap_gke_platform.sh gen-lang-client-0222711301` → bucket + 6 secrets tồn tại (chạy lại lần 2 vẫn OK — idempotent)**

- [ ] **Step 3: Viết `infra/live/terragrunt.hcl`**

```hcl
locals {
  project_id = "gen-lang-client-0222711301"
  region     = "asia-southeast1"
  repo_url   = "https://github.com/KienDinhVan/HBAAC-Dealight.git"
}

remote_state {
  backend = "gcs"
  generate = {
    path      = "backend.tf"
    if_exists = "overwrite"
  }
  config = {
    bucket   = "${local.project_id}-tfstate"
    prefix   = path_relative_to_include()
    project  = local.project_id
    location = local.region
  }
}

generate "versions" {
  path      = "versions.tf"
  if_exists = "overwrite"
  contents  = <<EOF
terraform {
  required_version = ">= 1.7"
  required_providers {
    google     = { source = "hashicorp/google", version = "~> 6.0" }
    random     = { source = "hashicorp/random", version = "~> 3.6" }
    kubernetes = { source = "hashicorp/kubernetes", version = "~> 2.33" }
    helm       = { source = "hashicorp/helm", version = "~> 2.16" }
  }
}
provider "google" {
  project = "${local.project_id}"
  region  = "${local.region}"
}
EOF
}

inputs = {
  project_id = local.project_id
  region     = local.region
  repo_url   = local.repo_url
}
```

- [ ] **Step 4: Thêm vào cuối `.gitignore`**

```
# Terraform / Terragrunt
**/.terraform/
*.tfstate
*.tfstate.*
*.tfplan
.terragrunt-cache/
```

- [ ] **Step 5: `terragrunt hclfmt --check infra/live/terragrunt.hcl` → pass. Commit**

```bash
git add scripts/bootstrap_gke_platform.sh infra/live/terragrunt.hcl .gitignore
git commit -m "feat(infra): bootstrap script + terragrunt root for GKE platform"
```

---

### Task 2: Module `network` + stack

**Files:**
- Create: `infra/terraform/modules/network/{main.tf,variables.tf,outputs.tf}`
- Create: `infra/live/prod/network/terragrunt.hcl`

**Interfaces:**
- Produces outputs: `network_id` (string, self_link), `network_name` (string), `subnet_name` (string). PSA peering sẵn sàng cho Cloud SQL/Memorystore private IP.

- [ ] **Step 1: `infra/terraform/modules/network/variables.tf`**

```hcl
variable "project_id" { type = string }
variable "region" { type = string }
variable "network_name" {
  type    = string
  default = "dealight-vpc"
}
variable "subnet_cidr" {
  type    = string
  default = "10.10.0.0/20"
}
```

- [ ] **Step 2: `infra/terraform/modules/network/main.tf`**

```hcl
resource "google_compute_network" "vpc" {
  name                    = var.network_name
  auto_create_subnetworks = false
}

resource "google_compute_subnetwork" "subnet" {
  name                     = "${var.network_name}-subnet"
  ip_cidr_range            = var.subnet_cidr
  region                   = var.region
  network                  = google_compute_network.vpc.id
  private_ip_google_access = true
}

# Private Service Access: dải IP dành cho Cloud SQL/Memorystore private IP.
resource "google_compute_global_address" "psa_range" {
  name          = "${var.network_name}-psa"
  purpose       = "VPC_PEERING"
  address_type  = "INTERNAL"
  prefix_length = 16
  network       = google_compute_network.vpc.id
}

resource "google_service_networking_connection" "psa" {
  network                 = google_compute_network.vpc.id
  service                 = "servicenetworking.googleapis.com"
  reserved_peering_ranges = [google_compute_global_address.psa_range.name]
}
```

- [ ] **Step 3: `infra/terraform/modules/network/outputs.tf`**

```hcl
output "network_id" { value = google_compute_network.vpc.id }
output "network_name" { value = google_compute_network.vpc.name }
output "subnet_name" { value = google_compute_subnetwork.subnet.name }
```

- [ ] **Step 4: `infra/live/prod/network/terragrunt.hcl`**

```hcl
include "root" {
  path = find_in_parent_folders()
}

terraform {
  source = "../../../terraform/modules/network"
}
```

- [ ] **Step 5: Validate + commit**

```bash
terraform -chdir=infra/terraform/modules/network init -backend=false && \
terraform -chdir=infra/terraform/modules/network validate
git add infra/terraform/modules/network infra/live/prod/network
git commit -m "feat(infra): network module (VPC + subnet + PSA)"
```
Expected: `Success! The configuration is valid.`

---

### Task 3: Module `gke-autopilot` + stack

**Files:**
- Create: `infra/terraform/modules/gke-autopilot/{main.tf,variables.tf,outputs.tf}`
- Create: `infra/live/prod/gke/terragrunt.hcl`

**Interfaces:**
- Consumes: outputs `network_id`, `subnet_name` từ stack network.
- Produces outputs: `cluster_name` (string), `cluster_location` (string). Các stack k8s về sau kết nối bằng `data.google_container_cluster` theo tên `dealight-prod`.

- [ ] **Step 1: `infra/terraform/modules/gke-autopilot/variables.tf`**

```hcl
variable "project_id" { type = string }
variable "region" { type = string }
variable "network_id" { type = string }
variable "subnet_name" { type = string }
variable "cluster_name" {
  type    = string
  default = "dealight-prod"
}
```

- [ ] **Step 2: `infra/terraform/modules/gke-autopilot/main.tf`**

```hcl
resource "google_container_cluster" "autopilot" {
  name             = var.cluster_name
  location         = var.region
  enable_autopilot = true
  network          = var.network_id
  subnetwork       = var.subnet_name

  # VPC-native; Autopilot tự quản secondary ranges.
  ip_allocation_policy {}

  release_channel {
    channel = "REGULAR"
  }

  deletion_protection = false
}
```

- [ ] **Step 3: `infra/terraform/modules/gke-autopilot/outputs.tf`**

```hcl
output "cluster_name" { value = google_container_cluster.autopilot.name }
output "cluster_location" { value = google_container_cluster.autopilot.location }
```

- [ ] **Step 4: `infra/live/prod/gke/terragrunt.hcl`**

```hcl
include "root" {
  path = find_in_parent_folders()
}

terraform {
  source = "../../../terraform/modules/gke-autopilot"
}

dependency "network" {
  config_path = "../network"

  mock_outputs = {
    network_id  = "mock-network"
    subnet_name = "mock-subnet"
  }
}

inputs = {
  network_id  = dependency.network.outputs.network_id
  subnet_name = dependency.network.outputs.subnet_name
}
```

- [ ] **Step 5: Validate + commit**

```bash
terraform -chdir=infra/terraform/modules/gke-autopilot init -backend=false && \
terraform -chdir=infra/terraform/modules/gke-autopilot validate
git add infra/terraform/modules/gke-autopilot infra/live/prod/gke
git commit -m "feat(infra): GKE Autopilot module"
```

---

### Task 4: Module `cloudsql` + stack

**Files:**
- Create: `infra/terraform/modules/cloudsql/{main.tf,variables.tf,outputs.tf}`
- Create: `infra/live/prod/cloudsql/terragrunt.hcl`

**Interfaces:**
- Consumes: `network_id` từ network.
- Produces outputs: `private_ip` (string), `db_user` = `"forecast"`, `db_password` (string, sensitive). 3 database: `sku_forecasting`, `airflow`, `mlflow`.

- [ ] **Step 1: `infra/terraform/modules/cloudsql/variables.tf`**

```hcl
variable "project_id" { type = string }
variable "region" { type = string }
variable "network_id" { type = string }
variable "instance_name" {
  type    = string
  default = "dealight-pg"
}
```

- [ ] **Step 2: `infra/terraform/modules/cloudsql/main.tf`**

```hcl
resource "random_password" "db" {
  length  = 24
  special = false
}

resource "google_secret_manager_secret" "db_password" {
  secret_id = "cloudsql-forecast-password"
  replication {
    auto {}
  }
}

resource "google_secret_manager_secret_version" "db_password" {
  secret      = google_secret_manager_secret.db_password.id
  secret_data = random_password.db.result
}

resource "google_sql_database_instance" "pg" {
  name             = var.instance_name
  database_version = "POSTGRES_16"
  region           = var.region

  settings {
    tier              = "db-g1-small"
    availability_type = "ZONAL"
    disk_size         = 20

    ip_configuration {
      ipv4_enabled    = false
      private_network = var.network_id
    }
  }

  deletion_protection = false
}

resource "google_sql_user" "forecast" {
  name     = "forecast"
  instance = google_sql_database_instance.pg.name
  password = random_password.db.result
}

resource "google_sql_database" "db" {
  for_each = toset(["sku_forecasting", "airflow", "mlflow"])
  name     = each.key
  instance = google_sql_database_instance.pg.name
}
```

- [ ] **Step 3: `infra/terraform/modules/cloudsql/outputs.tf`**

```hcl
output "private_ip" { value = google_sql_database_instance.pg.private_ip_address }
output "db_user" { value = google_sql_user.forecast.name }
output "db_password" {
  value     = random_password.db.result
  sensitive = true
}
```

- [ ] **Step 4: `infra/live/prod/cloudsql/terragrunt.hcl`**

```hcl
include "root" {
  path = find_in_parent_folders()
}

terraform {
  source = "../../../terraform/modules/cloudsql"
}

dependency "network" {
  config_path = "../network"

  mock_outputs = {
    network_id = "mock-network"
  }
}

inputs = {
  network_id = dependency.network.outputs.network_id
}
```

- [ ] **Step 5: Validate + commit**

```bash
terraform -chdir=infra/terraform/modules/cloudsql init -backend=false && \
terraform -chdir=infra/terraform/modules/cloudsql validate
git add infra/terraform/modules/cloudsql infra/live/prod/cloudsql
git commit -m "feat(infra): Cloud SQL PG16 private-IP module (3 DBs)"
```

---

### Task 5: Modules `memorystore` + `artifact-registry` + stacks

**Files:**
- Create: `infra/terraform/modules/memorystore/{main.tf,variables.tf,outputs.tf}`
- Create: `infra/terraform/modules/artifact-registry/{main.tf,variables.tf,outputs.tf}`
- Create: `infra/live/prod/memorystore/terragrunt.hcl`, `infra/live/prod/registry/terragrunt.hcl`

**Interfaces:**
- Consumes: `network_id` từ network (memorystore).
- Produces outputs: memorystore `redis_host` (string), `redis_port` (number); registry `repository_id` = `"dealight"`, `registry_url` = `"asia-southeast1-docker.pkg.dev/<project>/dealight"`.

- [ ] **Step 1: `infra/terraform/modules/memorystore/variables.tf`**

```hcl
variable "project_id" { type = string }
variable "region" { type = string }
variable "network_id" { type = string }
```

- [ ] **Step 2: `infra/terraform/modules/memorystore/main.tf`**

```hcl
resource "google_redis_instance" "redis" {
  name               = "dealight-redis"
  tier               = "BASIC"
  memory_size_gb     = 1
  region             = var.region
  redis_version      = "REDIS_7_0"
  authorized_network = var.network_id
  connect_mode       = "PRIVATE_SERVICE_ACCESS"
}
```

- [ ] **Step 3: `infra/terraform/modules/memorystore/outputs.tf`**

```hcl
output "redis_host" { value = google_redis_instance.redis.host }
output "redis_port" { value = google_redis_instance.redis.port }
```

- [ ] **Step 4: `infra/terraform/modules/artifact-registry/variables.tf` + `main.tf` + `outputs.tf`**

```hcl
# variables.tf
variable "project_id" { type = string }
variable "region" { type = string }
```

```hcl
# main.tf
resource "google_artifact_registry_repository" "dealight" {
  repository_id = "dealight"
  format        = "DOCKER"
  location      = var.region
  description   = "Dealight platform images (api/web/airflow/mlflow)"
}
```

```hcl
# outputs.tf
output "repository_id" { value = google_artifact_registry_repository.dealight.repository_id }
output "registry_url" {
  value = "${var.region}-docker.pkg.dev/${var.project_id}/${google_artifact_registry_repository.dealight.repository_id}"
}
```

- [ ] **Step 5: `infra/live/prod/memorystore/terragrunt.hcl`**

```hcl
include "root" {
  path = find_in_parent_folders()
}

terraform {
  source = "../../../terraform/modules/memorystore"
}

dependency "network" {
  config_path = "../network"

  mock_outputs = {
    network_id = "mock-network"
  }
}

inputs = {
  network_id = dependency.network.outputs.network_id
}
```

- [ ] **Step 6: `infra/live/prod/registry/terragrunt.hcl`**

```hcl
include "root" {
  path = find_in_parent_folders()
}

terraform {
  source = "../../../terraform/modules/artifact-registry"
}
```

- [ ] **Step 7: Validate cả 2 module (`terraform -chdir=... init -backend=false && validate`) + commit**

```bash
git add infra/terraform/modules/memorystore infra/terraform/modules/artifact-registry \
  infra/live/prod/memorystore infra/live/prod/registry
git commit -m "feat(infra): memorystore redis + artifact registry modules"
```

---

### Task 6: Module `iam` (GSAs + Workload Identity + MLflow bucket) + stack

**Files:**
- Create: `infra/terraform/modules/iam/{main.tf,variables.tf,outputs.tf}`
- Create: `infra/live/prod/iam/terragrunt.hcl`

**Interfaces:**
- Consumes: `repository_id` từ registry.
- Produces outputs: `pipeline_gsa_email`, `mlflow_gsa_email`, `ci_gsa_email` (string), `mlflow_bucket` (string, `<project>-mlflow-artifacts`).
- WI bindings (KSA → GSA): `dealight/airflow` + `dealight/forecast-api` → `dealight-pipeline` (SA CÓ SẴN — chỉ data source); `dealight/mlflow` → `dealight-mlflow` (mới); `arc-runners/arc-runner` + `ci-builds/kaniko-builder` → `dealight-ci` (mới).

- [ ] **Step 1: `infra/terraform/modules/iam/variables.tf`**

```hcl
variable "project_id" { type = string }
variable "region" { type = string }
variable "registry_repository_id" { type = string }
```

- [ ] **Step 2: `infra/terraform/modules/iam/main.tf`**

```hcl
# SA DE-pipeline có sẵn (do scripts/setup_gcp.sh quản) — tuyệt đối không tạo/sửa.
data "google_service_account" "pipeline" {
  account_id = "dealight-pipeline"
}

resource "google_service_account" "mlflow" {
  account_id   = "dealight-mlflow"
  display_name = "Dealight MLflow artifact writer"
}

resource "google_service_account" "ci" {
  account_id   = "dealight-ci"
  display_name = "Dealight CI (ARC runners + Kaniko)"
}

resource "google_storage_bucket" "mlflow_artifacts" {
  name                        = "${var.project_id}-mlflow-artifacts"
  location                    = var.region
  uniform_bucket_level_access = true
  force_destroy               = true
}

resource "google_storage_bucket_iam_member" "mlflow_writer" {
  bucket = google_storage_bucket.mlflow_artifacts.name
  role   = "roles/storage.objectAdmin"
  member = "serviceAccount:${google_service_account.mlflow.email}"
}

resource "google_artifact_registry_repository_iam_member" "ci_push" {
  repository = var.registry_repository_id
  location   = var.region
  role       = "roles/artifactregistry.writer"
  member     = "serviceAccount:${google_service_account.ci.email}"
}

locals {
  wi_bindings = {
    airflow      = { gsa = data.google_service_account.pipeline.name, ksa = "dealight/airflow" }
    forecast_api = { gsa = data.google_service_account.pipeline.name, ksa = "dealight/forecast-api" }
    mlflow       = { gsa = google_service_account.mlflow.name, ksa = "dealight/mlflow" }
    arc_runner   = { gsa = google_service_account.ci.name, ksa = "arc-runners/arc-runner" }
    kaniko       = { gsa = google_service_account.ci.name, ksa = "ci-builds/kaniko-builder" }
  }
}

resource "google_service_account_iam_member" "workload_identity" {
  for_each           = local.wi_bindings
  service_account_id = each.value.gsa
  role               = "roles/iam.workloadIdentityUser"
  member             = "serviceAccount:${var.project_id}.svc.id.goog[${each.value.ksa}]"
}
```

- [ ] **Step 3: `infra/terraform/modules/iam/outputs.tf`**

```hcl
output "pipeline_gsa_email" { value = data.google_service_account.pipeline.email }
output "mlflow_gsa_email" { value = google_service_account.mlflow.email }
output "ci_gsa_email" { value = google_service_account.ci.email }
output "mlflow_bucket" { value = google_storage_bucket.mlflow_artifacts.name }
```

- [ ] **Step 4: `infra/live/prod/iam/terragrunt.hcl`**

```hcl
include "root" {
  path = find_in_parent_folders()
}

terraform {
  source = "../../../terraform/modules/iam"
}

dependency "registry" {
  config_path = "../registry"

  mock_outputs = {
    repository_id = "dealight"
  }
}

inputs = {
  registry_repository_id = dependency.registry.outputs.repository_id
}
```

- [ ] **Step 5: Validate + commit**

```bash
terraform -chdir=infra/terraform/modules/iam init -backend=false && \
terraform -chdir=infra/terraform/modules/iam validate
git add infra/terraform/modules/iam infra/live/prod/iam
git commit -m "feat(infra): iam module — GSAs, workload identity bindings, mlflow bucket"
```

---

### Task 7: Module `k8s-platform` (namespaces + Secret + ConfigMap) + stack

**Files:**
- Create: `infra/terraform/modules/k8s-platform/{main.tf,variables.tf}`
- Create: `infra/live/prod/k8s-platform/terragrunt.hcl`

**Interfaces:**
- Consumes: cloudsql (`private_ip`, `db_user`, `db_password`), memorystore (`redis_host`, `redis_port`), iam (`ci_gsa_email`, `mlflow_bucket`). Cluster qua `data.google_container_cluster` (generate block).
- Produces: ns `dealight` + `ci-builds`; KSA `ci-builds/kaniko-builder`; Secret `dealight/platform-secrets` (keys: DATABASE_URL, AIRFLOW__DATABASE__SQL_ALCHEMY_CONN, AIRFLOW_CONN_POSTGRES_DEFAULT, MLFLOW_BACKEND_STORE_URI, OPENROUTER_API_KEY, DISCORD_WEBHOOK_URL, AIRFLOW_ADMIN_PASSWORD, AIRFLOW_PASSWORD, AIRFLOW__WEBSERVER__SECRET_KEY, GF_SECURITY_ADMIN_PASSWORD); ConfigMap `dealight/platform-config` (keys liệt kê ở Step 2). Manifests Task 10–12 chỉ `envFrom` 2 object này.

- [ ] **Step 1: `infra/terraform/modules/k8s-platform/variables.tf`**

```hcl
variable "project_id" { type = string }
variable "region" { type = string }
variable "db_private_ip" { type = string }
variable "db_user" { type = string }
variable "db_password" {
  type      = string
  sensitive = true
}
variable "redis_host" { type = string }
variable "redis_port" { type = number }
variable "ci_gsa_email" { type = string }
variable "mlflow_bucket" { type = string }
variable "data_bucket" {
  type    = string
  default = "gen-lang-client-0222711301-dealight-data"
}
```

- [ ] **Step 2: `infra/terraform/modules/k8s-platform/main.tf`**

```hcl
data "google_secret_manager_secret_version" "openrouter" {
  secret = "openrouter-api-key"
}

data "google_secret_manager_secret_version" "discord" {
  secret = "discord-webhook-url"
}

resource "random_password" "airflow_admin" {
  length  = 16
  special = false
}

resource "random_password" "airflow_webserver_key" {
  length  = 32
  special = false
}

resource "random_password" "grafana_admin" {
  length  = 16
  special = false
}

resource "kubernetes_namespace" "dealight" {
  metadata {
    name = "dealight"
  }
}

resource "kubernetes_namespace" "ci_builds" {
  metadata {
    name = "ci-builds"
  }
}

resource "kubernetes_service_account" "kaniko" {
  metadata {
    name      = "kaniko-builder"
    namespace = kubernetes_namespace.ci_builds.metadata[0].name
    annotations = {
      "iam.gke.io/gcp-service-account" = var.ci_gsa_email
    }
  }
}

# Kaniko đọc credHelpers này để push lên Artifact Registry qua Workload Identity.
resource "kubernetes_config_map" "docker_config" {
  metadata {
    name      = "kaniko-docker-config"
    namespace = kubernetes_namespace.ci_builds.metadata[0].name
  }
  data = {
    "config.json" = jsonencode({
      credHelpers = { "${var.region}-docker.pkg.dev" = "gcr" }
    })
  }
}

locals {
  pg = "${var.db_user}:${var.db_password}@${var.db_private_ip}:5432"
}

resource "kubernetes_secret" "platform" {
  metadata {
    name      = "platform-secrets"
    namespace = kubernetes_namespace.dealight.metadata[0].name
  }
  data = {
    DATABASE_URL                        = "postgresql://${local.pg}/sku_forecasting"
    AIRFLOW__DATABASE__SQL_ALCHEMY_CONN = "postgresql+psycopg2://${local.pg}/airflow"
    AIRFLOW_CONN_POSTGRES_DEFAULT       = "postgresql://${local.pg}/sku_forecasting"
    MLFLOW_BACKEND_STORE_URI            = "postgresql+psycopg2://${local.pg}/mlflow"
    OPENROUTER_API_KEY                  = data.google_secret_manager_secret_version.openrouter.secret_data
    DISCORD_WEBHOOK_URL                 = data.google_secret_manager_secret_version.discord.secret_data
    AIRFLOW_ADMIN_PASSWORD              = random_password.airflow_admin.result
    AIRFLOW_PASSWORD                    = random_password.airflow_admin.result
    AIRFLOW__WEBSERVER__SECRET_KEY      = random_password.airflow_webserver_key.result
    GF_SECURITY_ADMIN_PASSWORD          = random_password.grafana_admin.result
  }
}

resource "kubernetes_config_map" "platform" {
  metadata {
    name      = "platform-config"
    namespace = kubernetes_namespace.dealight.metadata[0].name
  }
  data = {
    GCP_PROJECT_ID               = var.project_id
    GCS_BUCKET                   = var.data_bucket
    BQ_DATASET                   = "dealight"
    BQ_BIGLAKE_CONNECTION        = "dealight-biglake"
    BQ_LOCATION                  = var.region
    REDIS_URL                    = "redis://${var.redis_host}:${var.redis_port}/0"
    MLFLOW_TRACKING_URI          = "http://mlflow:5000"
    MLFLOW_DEFAULT_ARTIFACT_ROOT = "gs://${var.mlflow_bucket}/"
    AIRFLOW_BASE_URL             = "http://airflow-webserver:8080"
    AIRFLOW_USERNAME             = "admin"
    ENABLE_AGENTS                = "false"
    MAX_UPLOAD_BYTES             = "104857600"
    DQ_REJECT_ALERT_RATIO        = "0.1"
    SERVICE_NAME                 = "sku-forecast-api"
    SERVICE_VERSION              = "0.1.0"
    AIRFLOW__CORE__EXECUTOR      = "LocalExecutor"
    AIRFLOW__CORE__LOAD_EXAMPLES = "false"
    AIRFLOW__API__AUTH_BACKENDS  = "airflow.api.auth.backend.basic_auth,airflow.api.auth.backend.session"
    GIT_PYTHON_REFRESH           = "quiet"
    PYTHONPATH                   = "/opt/project/src:/opt/project"
  }
}
```

- [ ] **Step 3: `infra/live/prod/k8s-platform/terragrunt.hcl`** — generate block kết nối kubernetes provider vào cluster (LƯU Ý: `$${...}` để terragrunt không nội suy):

```hcl
include "root" {
  path = find_in_parent_folders()
}

terraform {
  source = "../../../terraform/modules/k8s-platform"
}

generate "k8s_provider" {
  path      = "k8s_provider.tf"
  if_exists = "overwrite"
  contents  = <<EOF
data "google_container_cluster" "gke" {
  name     = "dealight-prod"
  location = "asia-southeast1"
}

data "google_client_config" "default" {}

provider "kubernetes" {
  host                   = "https://$${data.google_container_cluster.gke.endpoint}"
  token                  = data.google_client_config.default.access_token
  cluster_ca_certificate = base64decode(data.google_container_cluster.gke.master_auth[0].cluster_ca_certificate)
}
EOF
}

dependency "gke" {
  config_path  = "../gke"
  skip_outputs = true
}

dependency "cloudsql" {
  config_path = "../cloudsql"

  mock_outputs = {
    private_ip  = "10.0.0.3"
    db_user     = "forecast"
    db_password = "mock"
  }
}

dependency "memorystore" {
  config_path = "../memorystore"

  mock_outputs = {
    redis_host = "10.0.0.4"
    redis_port = 6379
  }
}

dependency "iam" {
  config_path = "../iam"

  mock_outputs = {
    ci_gsa_email  = "mock@example.iam.gserviceaccount.com"
    mlflow_bucket = "mock-mlflow"
  }
}

inputs = {
  db_private_ip = dependency.cloudsql.outputs.private_ip
  db_user       = dependency.cloudsql.outputs.db_user
  db_password   = dependency.cloudsql.outputs.db_password
  redis_host    = dependency.memorystore.outputs.redis_host
  redis_port    = dependency.memorystore.outputs.redis_port
  ci_gsa_email  = dependency.iam.outputs.ci_gsa_email
  mlflow_bucket = dependency.iam.outputs.mlflow_bucket
}
```

- [ ] **Step 4: Validate + commit**

```bash
terraform -chdir=infra/terraform/modules/k8s-platform init -backend=false && \
terraform -chdir=infra/terraform/modules/k8s-platform validate
git add infra/terraform/modules/k8s-platform infra/live/prod/k8s-platform
git commit -m "feat(infra): k8s-platform module — namespaces, platform secret/config"
```

---

### Task 8: Module `argocd` + stack

**Files:**
- Create: `infra/terraform/modules/argocd/{main.tf,variables.tf}`
- Create: `infra/live/prod/argocd/terragrunt.hcl`

**Interfaces:**
- Consumes: `repo_url` (root input), secret `github-repo-pat`.
- Produces: ArgoCD ns `argocd`; repo credential; Application `root` trỏ `argocd/apps` (Task 13 tạo dir đó) với auto-sync prune+selfHeal.

- [ ] **Step 1: `infra/terraform/modules/argocd/variables.tf`**

```hcl
variable "project_id" { type = string }
variable "region" { type = string }
variable "repo_url" { type = string }
```

- [ ] **Step 2: `infra/terraform/modules/argocd/main.tf`**

```hcl
resource "helm_release" "argocd" {
  name             = "argocd"
  namespace        = "argocd"
  create_namespace = true
  repository       = "https://argoproj.github.io/argo-helm"
  chart            = "argo-cd"
  version          = "7.7.10"

  values = [yamlencode({
    configs = {
      params = {
        "server.insecure" = true
      }
    }
  })]
}

data "google_secret_manager_secret_version" "repo_pat" {
  secret = "github-repo-pat"
}

resource "kubernetes_secret" "repo" {
  metadata {
    name      = "repo-hbaac-dealight"
    namespace = "argocd"
    labels = {
      "argocd.argoproj.io/secret-type" = "repository"
    }
  }
  data = {
    type     = "git"
    url      = var.repo_url
    username = "x-access-token"
    password = data.google_secret_manager_secret_version.repo_pat.secret_data
  }

  depends_on = [helm_release.argocd]
}

# App-of-apps qua chart argocd-apps: tránh kubernetes_manifest cần CRD lúc plan.
resource "helm_release" "root_app" {
  name       = "root-apps"
  namespace  = "argocd"
  repository = "https://argoproj.github.io/argo-helm"
  chart      = "argocd-apps"
  version    = "2.0.2"

  values = [yamlencode({
    applications = {
      root = {
        namespace = "argocd"
        project   = "default"
        source = {
          repoURL        = var.repo_url
          targetRevision = "main"
          path           = "argocd/apps"
        }
        destination = {
          server    = "https://kubernetes.default.svc"
          namespace = "argocd"
        }
        syncPolicy = {
          automated = {
            prune    = true
            selfHeal = true
          }
        }
      }
    }
  })]

  depends_on = [helm_release.argocd, kubernetes_secret.repo]
}
```

- [ ] **Step 3: `infra/live/prod/argocd/terragrunt.hcl`** — giống Task 7 Step 3 nhưng generate thêm helm provider; copy nguyên block dưới:

```hcl
include "root" {
  path = find_in_parent_folders()
}

terraform {
  source = "../../../terraform/modules/argocd"
}

generate "k8s_provider" {
  path      = "k8s_provider.tf"
  if_exists = "overwrite"
  contents  = <<EOF
data "google_container_cluster" "gke" {
  name     = "dealight-prod"
  location = "asia-southeast1"
}

data "google_client_config" "default" {}

provider "kubernetes" {
  host                   = "https://$${data.google_container_cluster.gke.endpoint}"
  token                  = data.google_client_config.default.access_token
  cluster_ca_certificate = base64decode(data.google_container_cluster.gke.master_auth[0].cluster_ca_certificate)
}

provider "helm" {
  kubernetes {
    host                   = "https://$${data.google_container_cluster.gke.endpoint}"
    token                  = data.google_client_config.default.access_token
    cluster_ca_certificate = base64decode(data.google_container_cluster.gke.master_auth[0].cluster_ca_certificate)
  }
}
EOF
}

dependency "gke" {
  config_path  = "../gke"
  skip_outputs = true
}
```

- [ ] **Step 4: Validate + commit**

```bash
terraform -chdir=infra/terraform/modules/argocd init -backend=false && \
terraform -chdir=infra/terraform/modules/argocd validate
git add infra/terraform/modules/argocd infra/live/prod/argocd
git commit -m "feat(infra): argocd module — helm install + repo cred + app-of-apps"
```

---

### Task 9: Module `arc` (Actions Runner Controller) + stack

**Files:**
- Create: `infra/terraform/modules/arc/{main.tf,variables.tf}`
- Create: `infra/live/prod/arc/terragrunt.hcl`

**Interfaces:**
- Consumes: `ci_gsa_email` (iam), secrets `arc-github-app-*`; ns `ci-builds` (k8s-platform).
- Produces: runner scale set label **`dealight-gke`** (workflows dùng `runs-on: dealight-gke`); KSA `arc-runners/arc-runner`; RBAC cho runner tạo Job/pod trong `ci-builds`.

- [ ] **Step 1: `infra/terraform/modules/arc/variables.tf`**

```hcl
variable "project_id" { type = string }
variable "region" { type = string }
variable "ci_gsa_email" { type = string }
variable "github_config_url" {
  type    = string
  default = "https://github.com/KienDinhVan/HBAAC-Dealight"
}
```

- [ ] **Step 2: `infra/terraform/modules/arc/main.tf`**

```hcl
resource "helm_release" "arc_controller" {
  name             = "arc"
  namespace        = "arc-systems"
  create_namespace = true
  chart            = "oci://ghcr.io/actions/actions-runner-controller-charts/gha-runner-scale-set-controller"
  version          = "0.9.3"
}

resource "kubernetes_namespace" "arc_runners" {
  metadata {
    name = "arc-runners"
  }
}

resource "kubernetes_service_account" "runner" {
  metadata {
    name      = "arc-runner"
    namespace = kubernetes_namespace.arc_runners.metadata[0].name
    annotations = {
      "iam.gke.io/gcp-service-account" = var.ci_gsa_email
    }
  }
}

data "google_secret_manager_secret_version" "app_id" {
  secret = "arc-github-app-id"
}

data "google_secret_manager_secret_version" "installation_id" {
  secret = "arc-github-app-installation-id"
}

data "google_secret_manager_secret_version" "private_key" {
  secret = "arc-github-app-private-key"
}

resource "kubernetes_secret" "gha_app" {
  metadata {
    name      = "gha-app"
    namespace = kubernetes_namespace.arc_runners.metadata[0].name
  }
  data = {
    github_app_id              = data.google_secret_manager_secret_version.app_id.secret_data
    github_app_installation_id = data.google_secret_manager_secret_version.installation_id.secret_data
    github_app_private_key     = data.google_secret_manager_secret_version.private_key.secret_data
  }
}

resource "helm_release" "runner_set" {
  name      = "dealight-gke"
  namespace = kubernetes_namespace.arc_runners.metadata[0].name
  chart     = "oci://ghcr.io/actions/actions-runner-controller-charts/gha-runner-scale-set"
  version   = "0.9.3"

  values = [yamlencode({
    githubConfigUrl    = var.github_config_url
    githubConfigSecret = "gha-app"
    minRunners         = 0
    maxRunners         = 3
    template = {
      spec = {
        serviceAccountName = "arc-runner"
        containers = [{
          name    = "runner"
          image   = "ghcr.io/actions/actions-runner:latest"
          command = ["/home/runner/run.sh"]
        }]
      }
    }
  })]

  depends_on = [helm_release.arc_controller, kubernetes_secret.gha_app]
}

# Runner điều khiển Kaniko build Jobs (và pod smoke) trong ns ci-builds.
resource "kubernetes_role" "ci_jobs" {
  metadata {
    name      = "ci-jobs"
    namespace = "ci-builds"
  }
  rule {
    api_groups = ["batch"]
    resources  = ["jobs"]
    verbs      = ["create", "get", "list", "watch", "delete"]
  }
  rule {
    api_groups = [""]
    resources  = ["pods"]
    verbs      = ["create", "get", "list", "watch", "delete"]
  }
  rule {
    api_groups = [""]
    resources  = ["pods/log"]
    verbs      = ["get", "list", "watch"]
  }
}

resource "kubernetes_role_binding" "runner_ci" {
  metadata {
    name      = "arc-runner-ci"
    namespace = "ci-builds"
  }
  role_ref {
    api_group = "rbac.authorization.k8s.io"
    kind      = "Role"
    name      = kubernetes_role.ci_jobs.metadata[0].name
  }
  subject {
    kind      = "ServiceAccount"
    name      = kubernetes_service_account.runner.metadata[0].name
    namespace = kubernetes_namespace.arc_runners.metadata[0].name
  }
}
```

- [ ] **Step 3: `infra/live/prod/arc/terragrunt.hcl`** — copy nguyên generate block k8s+helm provider từ Task 8 Step 3, rồi:

```hcl
include "root" {
  path = find_in_parent_folders()
}

terraform {
  source = "../../../terraform/modules/arc"
}

# (dán generate "k8s_provider" y hệt Task 8 Step 3 vào đây)

dependency "gke" {
  config_path  = "../gke"
  skip_outputs = true
}

dependency "k8s_platform" {
  config_path  = "../k8s-platform"
  skip_outputs = true
}

dependency "iam" {
  config_path = "../iam"

  mock_outputs = {
    ci_gsa_email = "mock@example.iam.gserviceaccount.com"
  }
}

inputs = {
  ci_gsa_email = dependency.iam.outputs.ci_gsa_email
}
```

- [ ] **Step 4: Validate + commit**

```bash
terraform -chdir=infra/terraform/modules/arc init -backend=false && \
terraform -chdir=infra/terraform/modules/arc validate
git add infra/terraform/modules/arc infra/live/prod/arc
git commit -m "feat(infra): arc module — GH Actions runner scale set + CI RBAC"
```

---

### Task 10: Manifests `forecast-api` + `web` + Ingress

**Files:**
- Create: `k8s/base/forecast-api/{serviceaccount.yaml,deployment.yaml,service.yaml,kustomization.yaml}`
- Create: `k8s/base/web/{configmap-nginx.yaml,deployment.yaml,service.yaml,ingress.yaml,kustomization.yaml}`

**Interfaces:**
- Consumes: ConfigMap `platform-config` + Secret `platform-secrets` (Task 7); image `…/dealight/forecast-api` và `…/dealight/web` (tag do overlay Task 13 đặt).
- Produces: Service `forecast-api:8000`, `web:80`; KSA `forecast-api` (WI → pipeline GSA); Ingress GCE → web.

- [ ] **Step 1: `k8s/base/forecast-api/serviceaccount.yaml`**

```yaml
apiVersion: v1
kind: ServiceAccount
metadata:
  name: forecast-api
  annotations:
    iam.gke.io/gcp-service-account: dealight-pipeline@gen-lang-client-0222711301.iam.gserviceaccount.com
```

- [ ] **Step 2: `k8s/base/forecast-api/deployment.yaml`**

```yaml
apiVersion: apps/v1
kind: Deployment
metadata:
  name: forecast-api
spec:
  replicas: 1
  selector:
    matchLabels:
      app: forecast-api
  template:
    metadata:
      labels:
        app: forecast-api
    spec:
      serviceAccountName: forecast-api
      containers:
        - name: forecast-api
          image: asia-southeast1-docker.pkg.dev/gen-lang-client-0222711301/dealight/forecast-api:bootstrap
          ports:
            - containerPort: 8000
          envFrom:
            - configMapRef:
                name: platform-config
            - secretRef:
                name: platform-secrets
          readinessProbe:
            httpGet:
              path: /health
              port: 8000
            initialDelaySeconds: 10
            periodSeconds: 10
          livenessProbe:
            httpGet:
              path: /version
              port: 8000
            initialDelaySeconds: 30
            periodSeconds: 30
          resources:
            requests:
              cpu: 500m
              memory: 1Gi
```

- [ ] **Step 3: `k8s/base/forecast-api/service.yaml` + `kustomization.yaml`**

```yaml
# service.yaml
apiVersion: v1
kind: Service
metadata:
  name: forecast-api
spec:
  selector:
    app: forecast-api
  ports:
    - port: 8000
      targetPort: 8000
```

```yaml
# kustomization.yaml
apiVersion: kustomize.config.k8s.io/v1beta1
kind: Kustomization
resources:
  - serviceaccount.yaml
  - deployment.yaml
  - service.yaml
```

- [ ] **Step 4: `k8s/base/web/configmap-nginx.yaml`** — bản k8s của `frontend/nginx.conf`: BỎ dòng `resolver 127.0.0.11 ...` (DNS Docker không tồn tại trên k8s) và proxy thẳng tên Service:

```yaml
apiVersion: v1
kind: ConfigMap
metadata:
  name: web-nginx
data:
  default.conf: |
    server {
        listen 80;
        server_name _;

        root /usr/share/nginx/html;
        index index.html;
        client_max_body_size 110m;

        location /api/ {
            rewrite ^/api/(.*)$ /$1 break;
            proxy_pass http://forecast-api:8000;
            proxy_set_header Host              $host;
            proxy_set_header X-Real-IP         $remote_addr;
            proxy_set_header X-Forwarded-For   $proxy_add_x_forwarded_for;
            proxy_set_header X-Forwarded-Proto $scheme;

            proxy_buffering    off;
            proxy_cache        off;
            proxy_http_version 1.1;
            proxy_set_header   Connection "";
            proxy_read_timeout 600s;
        }

        location / {
            try_files $uri $uri/ /index.html;
        }
    }
```

- [ ] **Step 5: `k8s/base/web/deployment.yaml` + `service.yaml` + `ingress.yaml` + `kustomization.yaml`**

```yaml
# deployment.yaml
apiVersion: apps/v1
kind: Deployment
metadata:
  name: web
spec:
  replicas: 1
  selector:
    matchLabels:
      app: web
  template:
    metadata:
      labels:
        app: web
    spec:
      containers:
        - name: web
          image: asia-southeast1-docker.pkg.dev/gen-lang-client-0222711301/dealight/web:bootstrap
          ports:
            - containerPort: 80
          volumeMounts:
            - name: nginx-conf
              mountPath: /etc/nginx/conf.d/default.conf
              subPath: default.conf
          readinessProbe:
            httpGet:
              path: /
              port: 80
            initialDelaySeconds: 5
            periodSeconds: 10
          resources:
            requests:
              cpu: 100m
              memory: 128Mi
      volumes:
        - name: nginx-conf
          configMap:
            name: web-nginx
```

```yaml
# service.yaml — NEG annotation cho GCE Ingress trên Autopilot
apiVersion: v1
kind: Service
metadata:
  name: web
  annotations:
    cloud.google.com/neg: '{"ingress": true}'
spec:
  selector:
    app: web
  ports:
    - port: 80
      targetPort: 80
```

```yaml
# ingress.yaml
apiVersion: networking.k8s.io/v1
kind: Ingress
metadata:
  name: web
  annotations:
    kubernetes.io/ingress.class: gce
spec:
  defaultBackend:
    service:
      name: web
      port:
        number: 80
```

```yaml
# kustomization.yaml
apiVersion: kustomize.config.k8s.io/v1beta1
kind: Kustomization
resources:
  - configmap-nginx.yaml
  - deployment.yaml
  - service.yaml
  - ingress.yaml
```

- [ ] **Step 6: `kustomize build k8s/base/forecast-api && kustomize build k8s/base/web` → cả 2 render sạch. Commit**

```bash
git add k8s/base/forecast-api k8s/base/web
git commit -m "feat(k8s): forecast-api + web manifests with GCE ingress"
```

---

### Task 11: Airflow — `Dockerfile.gke` + manifests

**Files:**
- Create: `infra/airflow/Dockerfile.gke`
- Create: `k8s/base/airflow/{serviceaccount.yaml,init-job.yaml,webserver-deployment.yaml,scheduler-deployment.yaml,service.yaml,kustomization.yaml}`

**Interfaces:**
- Consumes: image `…/dealight/airflow-base` (build từ `infra/airflow/Dockerfile` sẵn có) làm BASE_IMAGE; `platform-config`/`platform-secrets`.
- Produces: Service `airflow-webserver:8080`; KSA `airflow` (WI → pipeline GSA); Job `airflow-init` chạy như ArgoCD Sync hook. DAGs/src/scripts **bake vào image** (k8s không mount host dir như compose).

- [ ] **Step 1: `infra/airflow/Dockerfile.gke`** (build context = repo root; CD truyền `--build-arg BASE_IMAGE=<AR>/airflow-base:<sha>`)

```dockerfile
ARG BASE_IMAGE
FROM ${BASE_IMAGE}

COPY --chown=airflow:root dags /opt/airflow/dags
COPY --chown=airflow:root src /opt/project/src
COPY --chown=airflow:root scripts /opt/project/scripts
COPY --chown=airflow:root feature_registry.yaml /opt/project/feature_registry.yaml

ENV PYTHONPATH=/opt/project/src:/opt/project
WORKDIR /opt/project
```

- [ ] **Step 2: `k8s/base/airflow/serviceaccount.yaml`**

```yaml
apiVersion: v1
kind: ServiceAccount
metadata:
  name: airflow
  annotations:
    iam.gke.io/gcp-service-account: dealight-pipeline@gen-lang-client-0222711301.iam.gserviceaccount.com
```

- [ ] **Step 3: `k8s/base/airflow/init-job.yaml`** — ArgoCD Sync hook, chạy lại mỗi lần sync:

```yaml
apiVersion: batch/v1
kind: Job
metadata:
  name: airflow-init
  annotations:
    argocd.argoproj.io/hook: Sync
    argocd.argoproj.io/hook-delete-policy: BeforeHookCreation
spec:
  backoffLimit: 2
  template:
    spec:
      serviceAccountName: airflow
      restartPolicy: Never
      containers:
        - name: init
          image: asia-southeast1-docker.pkg.dev/gen-lang-client-0222711301/dealight/airflow:bootstrap
          command:
            - bash
            - -c
            - >
              airflow db migrate &&
              (airflow users create
              --username admin --password "$AIRFLOW_ADMIN_PASSWORD"
              --firstname GKE --lastname Admin --role Admin
              --email admin@example.local || true) &&
              airflow users reset-password --username admin --password "$AIRFLOW_ADMIN_PASSWORD"
          envFrom:
            - configMapRef:
                name: platform-config
            - secretRef:
                name: platform-secrets
          resources:
            requests:
              cpu: 500m
              memory: 1Gi
```

- [ ] **Step 4: `k8s/base/airflow/webserver-deployment.yaml` + `scheduler-deployment.yaml`**

```yaml
# webserver-deployment.yaml
apiVersion: apps/v1
kind: Deployment
metadata:
  name: airflow-webserver
spec:
  replicas: 1
  selector:
    matchLabels:
      app: airflow-webserver
  template:
    metadata:
      labels:
        app: airflow-webserver
    spec:
      serviceAccountName: airflow
      containers:
        - name: webserver
          image: asia-southeast1-docker.pkg.dev/gen-lang-client-0222711301/dealight/airflow:bootstrap
          args: ["webserver"]
          ports:
            - containerPort: 8080
          envFrom:
            - configMapRef:
                name: platform-config
            - secretRef:
                name: platform-secrets
          readinessProbe:
            httpGet:
              path: /health
              port: 8080
            initialDelaySeconds: 30
            periodSeconds: 15
          resources:
            requests:
              cpu: "1"
              memory: 2Gi
```

```yaml
# scheduler-deployment.yaml
apiVersion: apps/v1
kind: Deployment
metadata:
  name: airflow-scheduler
spec:
  replicas: 1
  selector:
    matchLabels:
      app: airflow-scheduler
  template:
    metadata:
      labels:
        app: airflow-scheduler
    spec:
      serviceAccountName: airflow
      containers:
        - name: scheduler
          image: asia-southeast1-docker.pkg.dev/gen-lang-client-0222711301/dealight/airflow:bootstrap
          args: ["scheduler"]
          envFrom:
            - configMapRef:
                name: platform-config
            - secretRef:
                name: platform-secrets
          livenessProbe:
            exec:
              command: ["airflow", "jobs", "check", "--job-type", "SchedulerJob", "--local"]
            initialDelaySeconds: 60
            periodSeconds: 60
          resources:
            requests:
              cpu: "1"
              memory: 2Gi
```

- [ ] **Step 5: `k8s/base/airflow/service.yaml` + `kustomization.yaml`**

```yaml
# service.yaml
apiVersion: v1
kind: Service
metadata:
  name: airflow-webserver
spec:
  selector:
    app: airflow-webserver
  ports:
    - port: 8080
      targetPort: 8080
```

```yaml
# kustomization.yaml
apiVersion: kustomize.config.k8s.io/v1beta1
kind: Kustomization
resources:
  - serviceaccount.yaml
  - init-job.yaml
  - webserver-deployment.yaml
  - scheduler-deployment.yaml
  - service.yaml
```

- [ ] **Step 6: `kustomize build k8s/base/airflow` sạch. Commit**

```bash
git add infra/airflow/Dockerfile.gke k8s/base/airflow
git commit -m "feat(k8s): airflow manifests + GKE image with baked DAGs"
```

---

### Task 12: Manifests `mlflow` + `prometheus` + `grafana`

**Files:**
- Create: `k8s/base/mlflow/{serviceaccount.yaml,deployment.yaml,service.yaml,kustomization.yaml}`
- Create: `k8s/base/prometheus/{deployment.yaml,service.yaml,pvc.yaml,kustomization.yaml}` + copy config
- Create: `k8s/base/grafana/{deployment.yaml,service.yaml,pvc.yaml,kustomization.yaml}` + copy provisioning

**Interfaces:**
- Consumes: `platform-config`/`platform-secrets`; image `…/dealight/mlflow`.
- Produces: Services `mlflow:5000`, `prometheus:9090`, `grafana:3000`.

- [ ] **Step 1: `k8s/base/mlflow/serviceaccount.yaml` + `deployment.yaml` + `service.yaml` + `kustomization.yaml`**

```yaml
# serviceaccount.yaml
apiVersion: v1
kind: ServiceAccount
metadata:
  name: mlflow
  annotations:
    iam.gke.io/gcp-service-account: dealight-mlflow@gen-lang-client-0222711301.iam.gserviceaccount.com
```

```yaml
# deployment.yaml
apiVersion: apps/v1
kind: Deployment
metadata:
  name: mlflow
spec:
  replicas: 1
  selector:
    matchLabels:
      app: mlflow
  template:
    metadata:
      labels:
        app: mlflow
    spec:
      serviceAccountName: mlflow
      containers:
        - name: mlflow
          image: asia-southeast1-docker.pkg.dev/gen-lang-client-0222711301/dealight/mlflow:bootstrap
          command:
            - mlflow
            - server
            - --host
            - 0.0.0.0
            - --port
            - "5000"
            - --allowed-hosts
            - mlflow,mlflow:5000,localhost,localhost:5000,127.0.0.1,127.0.0.1:5000
            - --backend-store-uri
            - $(MLFLOW_BACKEND_STORE_URI)
            - --default-artifact-root
            - $(MLFLOW_DEFAULT_ARTIFACT_ROOT)
          ports:
            - containerPort: 5000
          envFrom:
            - configMapRef:
                name: platform-config
            - secretRef:
                name: platform-secrets
          readinessProbe:
            httpGet:
              path: /health
              port: 5000
            initialDelaySeconds: 15
            periodSeconds: 15
          resources:
            requests:
              cpu: 500m
              memory: 1Gi
```

```yaml
# service.yaml
apiVersion: v1
kind: Service
metadata:
  name: mlflow
spec:
  selector:
    app: mlflow
  ports:
    - port: 5000
      targetPort: 5000
```

```yaml
# kustomization.yaml
apiVersion: kustomize.config.k8s.io/v1beta1
kind: Kustomization
resources:
  - serviceaccount.yaml
  - deployment.yaml
  - service.yaml
```

- [ ] **Step 2: Copy config Prometheus vào base (kustomize không cho file ngoài root)**

```bash
cp infra/prometheus/prometheus.yml infra/prometheus/alerts.yml k8s/base/prometheus/
```

- [ ] **Step 3: `k8s/base/prometheus/*.yaml`**

```yaml
# pvc.yaml
apiVersion: v1
kind: PersistentVolumeClaim
metadata:
  name: prometheus-data
spec:
  accessModes: ["ReadWriteOnce"]
  resources:
    requests:
      storage: 10Gi
```

```yaml
# deployment.yaml
apiVersion: apps/v1
kind: Deployment
metadata:
  name: prometheus
spec:
  replicas: 1
  strategy:
    type: Recreate
  selector:
    matchLabels:
      app: prometheus
  template:
    metadata:
      labels:
        app: prometheus
    spec:
      containers:
        - name: prometheus
          image: prom/prometheus:v3.4.1
          args:
            - --config.file=/etc/prometheus/prometheus.yml
            - --storage.tsdb.path=/prometheus
          ports:
            - containerPort: 9090
          volumeMounts:
            - name: config
              mountPath: /etc/prometheus
            - name: data
              mountPath: /prometheus
          resources:
            requests:
              cpu: 250m
              memory: 512Mi
      volumes:
        - name: config
          configMap:
            name: prometheus-config
        - name: data
          persistentVolumeClaim:
            claimName: prometheus-data
```

```yaml
# service.yaml
apiVersion: v1
kind: Service
metadata:
  name: prometheus
spec:
  selector:
    app: prometheus
  ports:
    - port: 9090
      targetPort: 9090
```

```yaml
# kustomization.yaml
apiVersion: kustomize.config.k8s.io/v1beta1
kind: Kustomization
resources:
  - pvc.yaml
  - deployment.yaml
  - service.yaml
configMapGenerator:
  - name: prometheus-config
    files:
      - prometheus.yml
      - alerts.yml
generatorOptions:
  disableNameSuffixHash: true
```

- [ ] **Step 4: Copy provisioning Grafana + manifests**

```bash
mkdir -p k8s/base/grafana
cp -r infra/grafana/provisioning k8s/base/grafana/provisioning
cp -r infra/grafana/dashboards k8s/base/grafana/dashboards
```

```yaml
# pvc.yaml
apiVersion: v1
kind: PersistentVolumeClaim
metadata:
  name: grafana-data
spec:
  accessModes: ["ReadWriteOnce"]
  resources:
    requests:
      storage: 2Gi
```

```yaml
# deployment.yaml
apiVersion: apps/v1
kind: Deployment
metadata:
  name: grafana
spec:
  replicas: 1
  strategy:
    type: Recreate
  selector:
    matchLabels:
      app: grafana
  template:
    metadata:
      labels:
        app: grafana
    spec:
      containers:
        - name: grafana
          image: grafana/grafana:12.0.2
          ports:
            - containerPort: 3000
          env:
            - name: GF_SECURITY_ADMIN_USER
              value: admin
            - name: GF_SECURITY_ADMIN_PASSWORD
              valueFrom:
                secretKeyRef:
                  name: platform-secrets
                  key: GF_SECURITY_ADMIN_PASSWORD
          volumeMounts:
            - name: datasources
              mountPath: /etc/grafana/provisioning/datasources
            - name: dashboard-providers
              mountPath: /etc/grafana/provisioning/dashboards
            - name: dashboards
              mountPath: /var/lib/grafana/dashboards
            - name: data
              mountPath: /var/lib/grafana
          resources:
            requests:
              cpu: 250m
              memory: 512Mi
      volumes:
        - name: datasources
          configMap:
            name: grafana-datasources
        - name: dashboard-providers
          configMap:
            name: grafana-dashboard-providers
        - name: dashboards
          configMap:
            name: grafana-dashboards
        - name: data
          persistentVolumeClaim:
            claimName: grafana-data
```

```yaml
# service.yaml
apiVersion: v1
kind: Service
metadata:
  name: grafana
spec:
  selector:
    app: grafana
  ports:
    - port: 3000
      targetPort: 3000
```

```yaml
# kustomization.yaml — điều chỉnh danh sách files theo nội dung thực tế
# của provisioning/ (ls trước khi viết; mẫu dưới theo layout compose hiện có)
apiVersion: kustomize.config.k8s.io/v1beta1
kind: Kustomization
resources:
  - pvc.yaml
  - deployment.yaml
  - service.yaml
configMapGenerator:
  - name: grafana-datasources
    files:
      - provisioning/datasources/datasource.yml
  - name: grafana-dashboard-providers
    files:
      - provisioning/dashboards/provider.yml
  - name: grafana-dashboards
    files:
      - dashboards/api-overview.json
generatorOptions:
  disableNameSuffixHash: true
```

- [ ] **Step 5: `kustomize build` cả 3 base sạch (sửa tên file trong configMapGenerator nếu `ls` khác mẫu). Commit**

```bash
git add k8s/base/mlflow k8s/base/prometheus k8s/base/grafana
git commit -m "feat(k8s): mlflow + prometheus + grafana manifests"
```

---

### Task 13: Overlay `prod` + ArgoCD Application

**Files:**
- Create: `k8s/overlays/prod/kustomization.yaml`
- Create: `argocd/apps/platform.yaml`

**Interfaces:**
- Consumes: mọi base Task 10–12; Application root (Task 8) trỏ `argocd/apps`.
- Produces: một Application `platform` sync `k8s/overlays/prod` vào ns `dealight`. CD (Task 15) bump tag bằng `kustomize edit set image` TẠI `k8s/overlays/prod`.

- [ ] **Step 1: `k8s/overlays/prod/kustomization.yaml`**

```yaml
apiVersion: kustomize.config.k8s.io/v1beta1
kind: Kustomization
namespace: dealight
resources:
  - ../../base/forecast-api
  - ../../base/web
  - ../../base/airflow
  - ../../base/mlflow
  - ../../base/prometheus
  - ../../base/grafana
images:
  - name: asia-southeast1-docker.pkg.dev/gen-lang-client-0222711301/dealight/forecast-api
    newTag: bootstrap
  - name: asia-southeast1-docker.pkg.dev/gen-lang-client-0222711301/dealight/web
    newTag: bootstrap
  - name: asia-southeast1-docker.pkg.dev/gen-lang-client-0222711301/dealight/airflow
    newTag: bootstrap
  - name: asia-southeast1-docker.pkg.dev/gen-lang-client-0222711301/dealight/mlflow
    newTag: bootstrap
```

- [ ] **Step 2: `argocd/apps/platform.yaml`**

```yaml
apiVersion: argoproj.io/v1alpha1
kind: Application
metadata:
  name: platform
  namespace: argocd
spec:
  project: default
  source:
    repoURL: https://github.com/KienDinhVan/HBAAC-Dealight.git
    targetRevision: main
    path: k8s/overlays/prod
  destination:
    server: https://kubernetes.default.svc
    namespace: dealight
  syncPolicy:
    automated:
      prune: true
      selfHeal: true
```

- [ ] **Step 3: `kustomize build k8s/overlays/prod | head -50` render sạch, đúng namespace dealight. Commit**

```bash
git add k8s/overlays/prod argocd
git commit -m "feat(k8s): prod overlay + argocd platform application"
```

<!-- PLAN-PART-4 -->

---

### Task 14: CI workflow trên ARC runners

**Files:**
- Modify: `.github/workflows/ci.yml`

**Interfaces:**
- Consumes: runner scale set label `dealight-gke` (Task 9).
- Produces: CI = ruff + pytest + static IaC checks (`terraform fmt`, `terragrunt hclfmt --check`, `kustomize build`). KHÔNG còn job docker build/trivy — Autopilot cấm DinD; build image thuộc về cd.yml (Kaniko).

- [ ] **Step 1: Ghi đè `.github/workflows/ci.yml`**

```yaml
name: CI

on:
  push:
    branches: [main]
  pull_request:

jobs:
  lint-and-unit-test:
    runs-on: dealight-gke
    steps:
      - name: Checkout
        uses: actions/checkout@v4

      - name: Install uv
        uses: astral-sh/setup-uv@v6
        with:
          enable-cache: true

      - name: Set up Python
        run: uv python install 3.13

      - name: Install dependencies
        run: uv sync --frozen

      - name: Lint
        run: uv run ruff check api scripts tests src dags

      - name: Unit and contract tests
        run: uv run pytest -q

  static-validate:
    runs-on: dealight-gke
    steps:
      - name: Checkout
        uses: actions/checkout@v4

      - name: Install terraform
        uses: hashicorp/setup-terraform@v3
        with:
          terraform_version: "1.9.8"

      - name: Install terragrunt + kustomize
        run: |
          mkdir -p "$HOME/.local/bin"
          curl -fsSLo "$HOME/.local/bin/terragrunt" \
            https://github.com/gruntwork-io/terragrunt/releases/download/v0.69.10/terragrunt_linux_amd64
          chmod +x "$HOME/.local/bin/terragrunt"
          curl -fsSL https://github.com/kubernetes-sigs/kustomize/releases/download/kustomize%2Fv5.5.0/kustomize_v5.5.0_linux_amd64.tar.gz \
            | tar xz -C "$HOME/.local/bin" kustomize
          echo "$HOME/.local/bin" >> "$GITHUB_PATH"

      - name: terraform fmt check
        run: terraform fmt -check -recursive infra/terraform

      - name: terragrunt hclfmt check
        run: terragrunt hclfmt --check --working-dir infra/live

      - name: kustomize build prod overlay
        run: kustomize build k8s/overlays/prod > /dev/null
```

- [ ] **Step 2: Commit**

```bash
git add .github/workflows/ci.yml
git commit -m "ci: move CI to ARC self-hosted runners + IaC static checks"
```

---

### Task 15: CD workflow — Kaniko build/push + bump kustomize + smoke

**Files:**
- Create: `.github/workflows/cd.yml`
- Create: `.github/scripts/kaniko_build.sh`
- Modify: `infra/terraform/modules/k8s-platform/main.tf` (thêm Secret `ci-builds/git-credentials` cho Kaniko clone repo private)

**Interfaces:**
- Consumes: KSA `kaniko-builder` + ConfigMap `kaniko-docker-config` (Task 7), RBAC runner→Jobs ns `ci-builds` (Task 9), AR repo (Task 5), overlay `k8s/overlays/prod` (Task 13).
- Produces: images `{forecast-api,web,airflow-base,airflow,mlflow}:<git-sha>` trong AR; commit `ci(cd): deploy <sha> [skip ci]` bump tag overlay; smoke in-cluster `http://forecast-api.dealight.svc.cluster.local:8000/health`.

- [ ] **Step 1: Thêm vào cuối `infra/terraform/modules/k8s-platform/main.tf`**

```hcl
data "google_secret_manager_secret_version" "repo_pat" {
  secret = "github-repo-pat"
}

# Kaniko clone repo private qua env GIT_USERNAME/GIT_PASSWORD (git context).
resource "kubernetes_secret" "git_credentials" {
  metadata {
    name      = "git-credentials"
    namespace = kubernetes_namespace.ci_builds.metadata[0].name
  }
  data = {
    GIT_USERNAME = "x-access-token"
    GIT_PASSWORD = data.google_secret_manager_secret_version.repo_pat.secret_data
  }
}
```

- [ ] **Step 2: Viết `.github/workflows/cd.yml`** — runner in-cluster tạo Kaniko Jobs qua kubectl (in-cluster config), đợi xong, bump tag, push, smoke:

```yaml
name: CD

on:
  push:
    branches: [main]
    paths:
      - "api/**"
      - "src/**"
      - "dags/**"
      - "scripts/**"
      - "frontend/**"
      - "infra/airflow/**"
      - "infra/mlflow/**"
      - "feature_registry.yaml"
      - "pyproject.toml"
      - "uv.lock"
      - ".github/workflows/cd.yml"

concurrency:
  group: cd-main
  cancel-in-progress: false

permissions:
  contents: write

env:
  AR: asia-southeast1-docker.pkg.dev/gen-lang-client-0222711301/dealight
  GIT_CONTEXT: git://github.com/KienDinhVan/HBAAC-Dealight.git

jobs:
  build-and-deploy:
    runs-on: dealight-gke
    steps:
      - name: Checkout
        uses: actions/checkout@v4

      - name: Install kubectl + kustomize
        run: |
          mkdir -p "$HOME/.local/bin"
          curl -fsSLo "$HOME/.local/bin/kubectl" \
            "https://dl.k8s.io/release/v1.31.0/bin/linux/amd64/kubectl"
          chmod +x "$HOME/.local/bin/kubectl"
          curl -fsSL https://github.com/kubernetes-sigs/kustomize/releases/download/kustomize%2Fv5.5.0/kustomize_v5.5.0_linux_amd64.tar.gz \
            | tar xz -C "$HOME/.local/bin" kustomize
          echo "$HOME/.local/bin" >> "$GITHUB_PATH"

      - name: Launch Kaniko builds (api, web, mlflow, airflow-base)
        run: |
          .github/scripts/kaniko_build.sh launch forecast-api api/Dockerfile ""
          .github/scripts/kaniko_build.sh launch web Dockerfile frontend
          .github/scripts/kaniko_build.sh launch mlflow Dockerfile infra/mlflow
          .github/scripts/kaniko_build.sh launch airflow-base Dockerfile infra/airflow

      - name: Wait for first wave
        run: |
          .github/scripts/kaniko_build.sh wait forecast-api
          .github/scripts/kaniko_build.sh wait web
          .github/scripts/kaniko_build.sh wait mlflow
          .github/scripts/kaniko_build.sh wait airflow-base

      - name: Build airflow (DAGs baked, FROM airflow-base)
        run: |
          .github/scripts/kaniko_build.sh launch airflow infra/airflow/Dockerfile.gke "" \
            "--build-arg=BASE_IMAGE=${AR}/airflow-base:${GITHUB_SHA}"
          .github/scripts/kaniko_build.sh wait airflow

      - name: Bump image tags in prod overlay
        run: |
          cd k8s/overlays/prod
          for img in forecast-api web airflow mlflow; do
            kustomize edit set image "${AR}/${img}=${AR}/${img}:${GITHUB_SHA}"
          done
          git config user.name  "github-actions[bot]"
          git config user.email "41898282+github-actions[bot]@users.noreply.github.com"
          git add kustomization.yaml
          git commit -m "ci(cd): deploy ${GITHUB_SHA:0:7} [skip ci]"
          git pull --rebase origin main
          git push origin HEAD:main

      - name: Smoke — wait for ArgoCD sync + healthy API
        run: |
          for i in $(seq 1 60); do
            if curl -fsS --max-time 5 \
              http://forecast-api.dealight.svc.cluster.local:8000/health; then
              echo "healthy"; exit 0
            fi
            echo "waiting ($i/60)"; sleep 10
          done
          echo "::error::forecast-api not healthy after 10m"; exit 1
```

- [ ] **Step 3: Viết `.github/scripts/kaniko_build.sh`** (chmod +x)

```bash
#!/usr/bin/env bash
# Launch/wait Kaniko build Jobs in ns ci-builds (runner has RBAC from Task 9).
# Usage: kaniko_build.sh launch <image> <dockerfile> <context-sub-path> [extra-arg...]
#        kaniko_build.sh wait <image>
set -euo pipefail

MODE="${1:?launch|wait}" IMAGE="${2:?image name}"
NS=ci-builds
JOB="kaniko-${IMAGE}-${GITHUB_SHA:0:7}"

if [ "$MODE" = launch ]; then
  DOCKERFILE="${3:?dockerfile}" SUBPATH="${4:-}"
  shift 4 2>/dev/null || shift 3
  ARGS="            - --context=${GIT_CONTEXT}#refs/heads/main#${GITHUB_SHA}
            - --dockerfile=${DOCKERFILE}
            - --destination=${AR}/${IMAGE}:${GITHUB_SHA}
            - --cache=true"
  if [ -n "$SUBPATH" ]; then
    ARGS="${ARGS}
            - --context-sub-path=${SUBPATH}"
  fi
  for extra in "$@"; do
    ARGS="${ARGS}
            - ${extra}"
  done

  kubectl -n "$NS" delete job "$JOB" --ignore-not-found
  kubectl apply -f - <<EOF
apiVersion: batch/v1
kind: Job
metadata:
  name: ${JOB}
  namespace: ${NS}
spec:
  backoffLimit: 0
  ttlSecondsAfterFinished: 7200
  template:
    spec:
      serviceAccountName: kaniko-builder
      restartPolicy: Never
      containers:
        - name: kaniko
          image: gcr.io/kaniko-project/executor:v1.23.2
          args:
${ARGS}
          envFrom:
            - secretRef:
                name: git-credentials
          volumeMounts:
            - name: docker-config
              mountPath: /kaniko/.docker
          resources:
            requests:
              cpu: "1"
              memory: 4Gi
              ephemeral-storage: 10Gi
      volumes:
        - name: docker-config
          configMap:
            name: kaniko-docker-config
EOF
  echo "launched ${JOB}"
  exit 0
fi

# wait mode
t=0
while true; do
  s="$(kubectl -n "$NS" get job "$JOB" -o jsonpath='{.status.succeeded}' 2>/dev/null || true)"
  f="$(kubectl -n "$NS" get job "$JOB" -o jsonpath='{.status.failed}' 2>/dev/null || true)"
  if [ "${s:-0}" -ge 1 ]; then echo "${JOB} OK"; exit 0; fi
  if [ "${f:-0}" -ge 1 ]; then
    echo "::error::${JOB} failed"
    kubectl -n "$NS" logs "job/${JOB}" --tail=200 || true
    exit 1
  fi
  t=$((t + 15))
  if [ "$t" -gt 2400 ]; then echo "::error::${JOB} timeout 40m"; exit 1; fi
  sleep 15
done
```

- [ ] **Step 4: Validate module + syntax, commit**

```bash
terraform -chdir=infra/terraform/modules/k8s-platform init -backend=false && \
terraform -chdir=infra/terraform/modules/k8s-platform validate
bash -n .github/scripts/kaniko_build.sh && chmod +x .github/scripts/kaniko_build.sh
git add .github/workflows/cd.yml .github/scripts/kaniko_build.sh infra/terraform/modules/k8s-platform
git commit -m "feat(cicd): CD pipeline — Kaniko builds + kustomize bump + in-cluster smoke"
```

**Lưu ý:** smoke chỉ chứng minh service healthy sau sync (ArgoCD poll ≤3 phút) — `/version` không chứa git sha nên không phân biệt được image cũ/mới; chấp nhận cho demo (ghi trong runbook).

---

### Task 16: Runbook deploy/destroy + verify toàn trình

**Files:**
- Create: `docs/GKE_DEPLOY_RUNBOOK.md`

**Interfaces:**
- Consumes: mọi task trước.
- Produces: tài liệu vận hành duy nhất: prerequisites → bootstrap → secret values → `terragrunt run-all apply` → first deploy qua CD → access/port-forward → rollback → destroy → chi phí.

- [ ] **Step 1: Viết `docs/GKE_DEPLOY_RUNBOOK.md`** với các mục:
  1. **Prerequisites** (GitHub App ARC + PAT — theo phần Prerequisites của plan này).
  2. **Bootstrap**: `scripts/bootstrap_gke_platform.sh gen-lang-client-0222711301` + nạp 6 secret values.
  3. **Provision**: `cd infra/live/prod && terragrunt run-all apply` (dependency tự resolve: network → gke/cloudsql/memorystore/registry → iam → k8s-platform → argocd/arc). Thời gian dự kiến ~30–40 phút (GKE + Cloud SQL).
  4. **First deploy**: pods `dealight` sẽ ImagePullBackOff với tag `bootstrap` cho tới khi CD chạy lần đầu — push commit chạm paths của cd.yml lên `main` → Kaniko build → bump tag → ArgoCD sync → healthy.
  5. **Access**: Ingress IP (`kubectl -n dealight get ingress web`), port-forward Airflow/Grafana/MLflow/Prometheus/ArgoCD + lệnh lấy password (ArgoCD initial secret, `platform-secrets` keys).
  6. **Rollback**: `git revert` commit bump tag → ArgoCD tự đưa image về bản cũ.
  7. **Destroy**: `terragrunt run-all destroy` + caveat: data lake GCS/BigQuery nằm ngoài cluster, không mất; secrets Secret Manager giữ.
  8. **Chi phí**: ~$200–290/tháng khi bật liên tục.

- [ ] **Step 2: Commit**

```bash
git add docs/GKE_DEPLOY_RUNBOOK.md
git commit -m "docs: GKE deploy/destroy runbook"
```

<!-- PLAN-PART-5 -->
