variable "project_id" { type = string }
variable "region" { type = string }
variable "network_id" { type = string }
variable "instance_name" {
  type    = string
  default = "dealight-pg"
}
