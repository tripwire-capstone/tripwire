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