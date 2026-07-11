output "pipeline_gsa_email" { value = data.google_service_account.pipeline.email }
output "mlflow_gsa_email" { value = google_service_account.mlflow.email }
output "ci_gsa_email" { value = google_service_account.ci.email }
output "mlflow_bucket" { value = google_storage_bucket.mlflow_artifacts.name }
