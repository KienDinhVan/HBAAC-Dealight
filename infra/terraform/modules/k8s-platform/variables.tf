variable "project_id" { type = string }
variable "region" { type = string }
variable "db_private_ip" { type = string }
variable "db_user" { type = string }
variable "db_password" {
  type      = string
  sensitive = true
}
variable "redis_host" { type = string }
variable "redis_port" { type = number }
variable "ci_gsa_email" { type = string }
variable "mlflow_bucket" { type = string }
variable "data_bucket" {
  type    = string
  default = "gen-lang-client-0222711301-dealight-data"
}
