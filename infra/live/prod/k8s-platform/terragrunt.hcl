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
