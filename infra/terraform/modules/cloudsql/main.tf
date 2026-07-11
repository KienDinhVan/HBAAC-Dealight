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
    # PG16 mặc định ENTERPRISE_PLUS — edition đó không cho shared-core tier.
    edition           = "ENTERPRISE"
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
