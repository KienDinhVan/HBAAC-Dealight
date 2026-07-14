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
      cm = {
        "accounts.kien" = "login"
        "exec.enabled"  = "true"
      }
      rbac = {
        "policy.csv" = "p, role:admin, exec, create, */*, allow\ng, kien, role:admin"
      }
    }
    controller = {
      resources = {
        requests = { cpu = "250m", memory = "512Mi" }
        limits   = { cpu = "1", memory = "1Gi" }
      }
    }
    repoServer = {
      resources = {
        requests = { cpu = "100m", memory = "256Mi" }
        limits   = { cpu = "500m", memory = "512Mi" }
      }
    }
    server = {
      resources = {
        requests = { cpu = "50m", memory = "128Mi" }
        limits   = { cpu = "250m", memory = "256Mi" }
      }
      ingress = {
        enabled  = true
        hostname = "argocd.35.190.18.31.nip.io"
        annotations = {
          "kubernetes.io/ingress.class" = "gce"
        }
      }
    }
    applicationSet = {
      resources = {
        requests = { cpu = "50m", memory = "128Mi" }
        limits   = { cpu = "250m", memory = "256Mi" }
      }
    }
    notifications = {
      resources = {
        requests = { cpu = "50m", memory = "128Mi" }
        limits   = { cpu = "250m", memory = "256Mi" }
      }
    }
    dex = {
      resources = {
        requests = { cpu = "50m", memory = "128Mi" }
        limits   = { cpu = "250m", memory = "256Mi" }
      }
    }
    redis = {
      resources = {
        requests = { cpu = "50m", memory = "128Mi" }
        limits   = { cpu = "250m", memory = "256Mi" }
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
