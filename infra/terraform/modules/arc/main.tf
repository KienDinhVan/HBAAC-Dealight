resource "helm_release" "arc_controller" {
  name             = "arc"
  namespace        = "arc-systems"
  create_namespace = true
  chart            = "oci://ghcr.io/actions/actions-runner-controller-charts/gha-runner-scale-set-controller"
  version          = "0.9.3"
}

resource "kubernetes_namespace" "arc_runners" {
  metadata {
    name = "arc-runners"
  }
}

resource "kubernetes_service_account" "runner" {
  metadata {
    name      = "arc-runner"
    namespace = kubernetes_namespace.arc_runners.metadata[0].name
    annotations = {
      "iam.gke.io/gcp-service-account" = var.ci_gsa_email
    }
  }
}

data "google_secret_manager_secret_version" "app_id" {
  secret = "arc-github-app-id"
}

data "google_secret_manager_secret_version" "installation_id" {
  secret = "arc-github-app-installation-id"
}

data "google_secret_manager_secret_version" "private_key" {
  secret = "arc-github-app-private-key"
}

resource "kubernetes_secret" "gha_app" {
  metadata {
    name      = "gha-app"
    namespace = kubernetes_namespace.arc_runners.metadata[0].name
  }
  data = {
    github_app_id              = data.google_secret_manager_secret_version.app_id.secret_data
    github_app_installation_id = data.google_secret_manager_secret_version.installation_id.secret_data
    github_app_private_key     = data.google_secret_manager_secret_version.private_key.secret_data
  }
}

resource "helm_release" "runner_set" {
  name      = "dealight-gke"
  namespace = kubernetes_namespace.arc_runners.metadata[0].name
  chart     = "oci://ghcr.io/actions/actions-runner-controller-charts/gha-runner-scale-set"
  version   = "0.9.3"

  values = [yamlencode({
    githubConfigUrl    = var.github_config_url
    githubConfigSecret = "gha-app"
    minRunners         = 0
    maxRunners         = 3
    template = {
      spec = {
        serviceAccountName = "arc-runner"
        containers = [{
          name    = "runner"
          image   = "ghcr.io/actions/actions-runner:latest"
          command = ["/home/runner/run.sh"]
        }]
      }
    }
  })]

  depends_on = [helm_release.arc_controller, kubernetes_secret.gha_app]
}

# Runner điều khiển Kaniko build Jobs (và pod smoke) trong ns ci-builds.
resource "kubernetes_role" "ci_jobs" {
  metadata {
    name      = "ci-jobs"
    namespace = "ci-builds"
  }
  rule {
    api_groups = ["batch"]
    resources  = ["jobs"]
    verbs      = ["create", "get", "list", "watch", "delete"]
  }
  rule {
    api_groups = [""]
    resources  = ["pods"]
    verbs      = ["create", "get", "list", "watch", "delete"]
  }
  rule {
    api_groups = [""]
    resources  = ["pods/log"]
    verbs      = ["get", "list", "watch"]
  }
}

resource "kubernetes_role_binding" "runner_ci" {
  metadata {
    name      = "arc-runner-ci"
    namespace = "ci-builds"
  }
  role_ref {
    api_group = "rbac.authorization.k8s.io"
    kind      = "Role"
    name      = kubernetes_role.ci_jobs.metadata[0].name
  }
  subject {
    kind      = "ServiceAccount"
    name      = kubernetes_service_account.runner.metadata[0].name
    namespace = kubernetes_namespace.arc_runners.metadata[0].name
  }
}
