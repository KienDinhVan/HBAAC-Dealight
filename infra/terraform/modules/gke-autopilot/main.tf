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
