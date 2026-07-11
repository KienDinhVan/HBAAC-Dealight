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
