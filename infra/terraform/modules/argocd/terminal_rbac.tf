resource "kubernetes_cluster_role_v1" "argocd_terminal" {
  metadata {
    name = "argocd-server-terminal"
  }

  rule {
    api_groups = [""]
    resources  = ["pods/exec"]
    verbs      = ["create"]
  }
}

resource "kubernetes_cluster_role_binding_v1" "argocd_terminal" {
  metadata {
    name = "argocd-server-terminal"
  }

  role_ref {
    api_group = "rbac.authorization.k8s.io"
    kind      = "ClusterRole"
    name      = kubernetes_cluster_role_v1.argocd_terminal.metadata[0].name
  }

  subject {
    kind      = "ServiceAccount"
    name      = "argocd-server"
    namespace = "argocd"
  }

  depends_on = [helm_release.argocd]
}
