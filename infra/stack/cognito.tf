resource "aws_cognito_user_pool" "main" {
  name = var.name

  # Invite-only: users exist only when an admin (this config) creates them
  admin_create_user_config {
    allow_admin_create_user_only = true
  }

  username_attributes      = ["email"]
  auto_verified_attributes = ["email"]

  password_policy {
    minimum_length    = 12
    require_lowercase = true
    require_uppercase = true
    require_numbers   = true
    require_symbols   = false
  }

  account_recovery_setting {
    recovery_mechanism {
      name     = "verified_email"
      priority = 1
    }
  }

  tags = {
    Name = var.name
  }
}

# Cognito's hosted login page lives at https://<domain>.auth.<region>.amazoncognito.com;
# the prefix must be unique in the region, so it includes the account ID
resource "aws_cognito_user_pool_domain" "main" {
  domain       = "${var.name}-${data.aws_caller_identity.current.account_id}"
  user_pool_id = aws_cognito_user_pool.main.id
}

resource "aws_cognito_user_pool_client" "alb" {
  name         = "${var.name}-alb"
  user_pool_id = aws_cognito_user_pool.main.id

  # The ALB needs a client secret to exchange the login code for tokens
  generate_secret = true

  allowed_oauth_flows_user_pool_client = true
  allowed_oauth_flows                  = ["code"]
  allowed_oauth_scopes                 = ["openid", "email"]
  supported_identity_providers         = ["COGNITO"]

  # The ALB's fixed path for finishing a login
  callback_urls = ["https://${local.laminar_domain}/oauth2/idpresponse"]
}

resource "aws_cognito_user" "laminar" {
  for_each = toset(var.laminar_users)

  user_pool_id             = aws_cognito_user_pool.main.id
  username                 = each.value
  desired_delivery_mediums = ["EMAIL"]

  attributes = {
    email          = each.value
    email_verified = true
  }
}
# MCP: coding agents (Claude Code and others) log users in to the Tripwire MCP server with this
# pool. The scope marks a token as meant for the MCP server; a Laminar UI login doesn't carry it,
# so it can't be used there.
resource "aws_cognito_resource_server" "mcp" {
  identifier   = "tripwire-mcp"
  name         = "${var.name}-mcp"
  user_pool_id = aws_cognito_user_pool.main.id

  scope {
    scope_name        = "read"
    scope_description = "Read signals, clusters and events through the MCP server"
  }
}

resource "aws_cognito_user_pool_client" "mcp" {
  name         = "${var.name}-mcp"
  user_pool_id = aws_cognito_user_pool.main.id

  # Public client: it runs on each user's machine, where a secret can't be kept, so the login uses
  # PKCE instead
  generate_secret = false

  allowed_oauth_flows_user_pool_client = true
  allowed_oauth_flows                  = ["code"]
  allowed_oauth_scopes                 = ["openid", "email", "${aws_cognito_resource_server.mcp.identifier}/read"]
  supported_identity_providers         = ["COGNITO"]

  # The agent listens on this fixed local port for the end of the login
  # (claude mcp add ... --callback-port 53682)
  callback_urls = ["http://localhost:53682/callback"]

  # Access tokens last an hour; the agent renews them with the refresh token, so users log in
  # again only after 30 days
  access_token_validity  = 1
  id_token_validity      = 1
  refresh_token_validity = 30
  token_validity_units {
    access_token  = "hours"
    id_token      = "hours"
    refresh_token = "days"
  }
}
