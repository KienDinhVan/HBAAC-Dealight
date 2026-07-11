resource "helm_release" "argocd" {
  name             = "argocd"
  namespace        = "argocd"
  create_namespace = true
  repository       = "https://argoproj.github.io/argo-helm"
  chart            = "argo-cd"
  version          = "7.7.10"

  values = [yamlencode({
    configs = {
      params = {
        "server.insecure" = true
      }
    }
  })]
}

data "google_secret_manager_secret_version" "repo_pat" {
  secret = "github-repo-pat"
}

resource "kubernetes_secret" "repo" {
  metadata {
    name      = "repo-hbaac-dealight"
    namespace = "argocd"
    labels = {
      "argocd.argoproj.io/secret-type" = "repository"
    }
  }
  data = {
    type     = "git"
    url      = var.repo_url
    username = "x-access-token"
    password = data.google_secret_manager_secret_version.repo_pat.secret_data
  }

  depends_on = [helm_release.argocd]
}

# App-of-apps qua chart argocd-apps: tránh kubernetes_manifest cần CRD lúc plan.
resource "helm_release" "root_app" {
  name       = "root-apps"
  namespace  = "argocd"
  repository = "https://argoproj.github.io/argo-helm"
  chart      = "argocd-apps"
  version    = "2.0.2"

  values = [yamlencode({
    applications = {
      root = {
        namespace = "argocd"
        project   = "default"
        source = {
          repoURL        = var.repo_url
          targetRevision = "main"
          path           = "argocd/apps"
        }
        destination = {
          server    = "https://kubernetes.default.svc"
          namespace = "argocd"
        }
        syncPolicy = {
          automated = {
            prune    = true
            selfHeal = true
          }
        }
      }
    }
  })]

  depends_on = [helm_release.argocd, kubernetes_secret.repo]
}
