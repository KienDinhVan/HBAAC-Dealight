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
