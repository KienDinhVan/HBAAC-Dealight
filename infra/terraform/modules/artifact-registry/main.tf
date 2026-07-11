resource "google_artifact_registry_repository" "dealight" {
  repository_id = "dealight"
  format        = "DOCKER"
  location      = var.region
  description   = "Dealight platform images (api/web/airflow/mlflow)"
}
