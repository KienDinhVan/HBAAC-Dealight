resource "google_redis_instance" "redis" {
  name               = "dealight-redis"
  tier               = "BASIC"
  memory_size_gb     = 1
  region             = var.region
  redis_version      = "REDIS_7_0"
  authorized_network = var.network_id
  connect_mode       = "PRIVATE_SERVICE_ACCESS"
}
