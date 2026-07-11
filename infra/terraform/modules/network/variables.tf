variable "project_id" { type = string }
variable "region" { type = string }
variable "network_name" {
  type    = string
  default = "dealight-vpc"
}
variable "subnet_cidr" {
  type    = string
  default = "10.10.0.0/20"
}
