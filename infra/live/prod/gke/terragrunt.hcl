include "root" {
  path = find_in_parent_folders()
}

terraform {
  source = "../../../terraform/modules/gke-autopilot"
}

dependency "network" {
  config_path = "../network"

  mock_outputs = {
    network_id  = "mock-network"
    subnet_name = "mock-subnet"
  }
}

inputs = {
  network_id  = dependency.network.outputs.network_id
  subnet_name = dependency.network.outputs.subnet_name
}
