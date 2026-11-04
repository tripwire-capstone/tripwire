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

# Each is 32 random bytes as 64 hex characters, the same as `openssl rand -hex 32`
resource "random_id" "shared_secret_token" {
  byte_length = 32
}

# Never regenerate once Laminar holds data: it encrypts stored provider keys,
# and a new value makes them unreadable
resource "random_id" "aead_secret_key" {
  byte_length = 32
}

resource "random_id" "auth_secret" {
  byte_length = 32
}

resource "aws_ssm_parameter" "shared_secret_token" {
  name        = "/${var.name}/laminar/shared_secret_token"
  description = "Shared between Laminar's frontend and app-server; both must hold the same value."
  type        = "SecureString"
  value       = random_id.shared_secret_token.hex
}

resource "aws_ssm_parameter" "aead_secret_key" {
  name        = "/${var.name}/laminar/aead_secret_key"
  description = "Laminar's encryption key for stored secrets; both containers must hold the same value."
  type        = "SecureString"
  value       = random_id.aead_secret_key.hex
}

resource "aws_ssm_parameter" "auth_secret" {
  name        = "/${var.name}/laminar/auth_secret"
  description = "Signs Laminar's login sessions; used as both NEXTAUTH_SECRET and BETTER_AUTH_SECRET."
  type        = "SecureString"
  value       = random_id.auth_secret.hex
}