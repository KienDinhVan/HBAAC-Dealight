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
