variable "project_id" { type = string }
variable "region" { type = string }
variable "network_id" { type = string }
variable "subnet_name" { type = string }
variable "cluster_name" {
  type    = string
  default = "dealight-prod"
}
