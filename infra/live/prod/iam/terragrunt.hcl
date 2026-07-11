include "root" {
  path = find_in_parent_folders()
}

terraform {
  source = "../../../terraform/modules/iam"
}

dependency "registry" {
  config_path = "../registry"

  mock_outputs = {
    repository_id = "dealight"
  }
}

inputs = {
  registry_repository_id = dependency.registry.outputs.repository_id
}
