# Letters and digits only: these end up inside connection URLs
resource "random_password" "db" {
  length  = 32
  special = false
}

resource "random_password" "clickhouse" {
  length  = 32
  special = false
}

resource "aws_ssm_parameter" "database_url" {
  name        = "/${var.name}/laminar/database_url"
  description = "Laminar's Postgres connection string. No query string: app-server reads ?sslmode as part of the database name."
  type        = "SecureString"
  value       = "postgres://${aws_db_instance.main.username}:${random_password.db.result}@${aws_db_instance.main.address}:${aws_db_instance.main.port}/${aws_db_instance.main.db_name}"
}

resource "aws_ssm_parameter" "clickhouse_password" {
  name        = "/${var.name}/clickhouse/password"
  description = "ClickHouse password for Laminar's user. The data box reads it at boot; the ECS tasks read it at start."
  type        = "SecureString"
  value       = random_password.clickhouse.result
}