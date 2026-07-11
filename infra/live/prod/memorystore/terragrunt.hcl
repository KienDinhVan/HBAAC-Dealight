include "root" {
  path = find_in_parent_folders()
}

terraform {
  source = "../../../terraform/modules/memorystore"
}

dependency "network" {
  config_path = "../network"

  mock_outputs = {
    network_id = "mock-network"
  }
}

inputs = {
  network_id = dependency.network.outputs.network_id
}
