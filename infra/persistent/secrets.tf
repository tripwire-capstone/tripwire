# Laminar's own secrets live here, not in the stack, so a teardown
# and restore keeps the values the restored data was encrypted with

# Each is 32 random bytes as 64 hex characters
resource "random_id" "shared_secret_token" {
  byte_length = 32
}

# Encrypts stored provider keys; a new value makes them unreadable
resource "random_id" "aead_secret_key" {
  byte_length = 32

  lifecycle {
    prevent_destroy = true
  }
}

# Encrypts Better Auth's signing key; a new value breaks every login
resource "random_id" "auth_secret" {
  byte_length = 32

  lifecycle {
    prevent_destroy = true
  }
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