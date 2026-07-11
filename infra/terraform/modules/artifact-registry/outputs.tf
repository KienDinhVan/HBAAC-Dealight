output "repository_id" { value = google_artifact_registry_repository.dealight.repository_id }
output "registry_url" {
  value = "${var.region}-docker.pkg.dev/${var.project_id}/${google_artifact_registry_repository.dealight.repository_id}"
}
