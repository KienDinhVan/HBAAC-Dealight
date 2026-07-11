output "private_ip" { value = google_sql_database_instance.pg.private_ip_address }
output "db_user" { value = google_sql_user.forecast.name }
output "db_password" {
  value     = random_password.db.result
  sensitive = true
}
