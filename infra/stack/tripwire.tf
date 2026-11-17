# Tripwire: the Signals backend (poller, runner, clusterer, API + UI), next to Laminar.
# Reasoning: infra/docs/BACKEND_INFRA_NOTES.md (requirements) and TRIPWIRE_ON_AWS_RULES.md.
#
#   - One image, four services with different start commands.
#   - Served under Laminar's address at /tripwire/ (an ALB rule ahead of Laminar's catch-all).
#   - Its own `tripwire` database and user on Laminar's RDS instance.
#   - A migration task definition (not a service) that the CLI runs once per deploy, before
#     the services get a new image (tripwire_services_image).
#   - No OpenAI or Laminar keys here: the user adds them in Tripwire → Settings, which stores them
#     encrypted in the database with TRIPWIRE_SECRET_KEY (infra/persistent/secrets.tf).

locals {
  tripwire_services_enabled = var.tripwire_services_image != ""
  tripwire_url              = "${local.laminar_url}/tripwire/"

  tripwire_common_env = {
    PGSSLMODE           = "require" # RDS Postgres 16 only accepts SSL connections
    JUDGE_DEFAULT_MODEL = var.judge_default_model
  }

  # What each service needs, beyond the common env: start command, secrets, deploy limits
  tripwire_services = {
    poller = {
      command = ["python", "-m", "tripwire.poller"]
      secrets = ["database_url", "secret_key"]
      env     = {}
      # Exactly one copy, even during a deploy: two pollers would read the same traces
      maximum_percent         = 100
      minimum_healthy_percent = 0
    }
    runner = {
      command                 = ["python", "-m", "tripwire.runner"]
      secrets                 = ["database_url", "secret_key"]
      env                     = {}
      maximum_percent         = 200
      minimum_healthy_percent = 100
    }
    clusterer = {
      command = ["python", "-m", "tripwire.clusterer"]
      secrets = ["database_url", "secret_key"]
      env     = {}
      # Exactly one copy: two clusterers would make clusters twice (CONTRACT.md)
      maximum_percent         = 100
      minimum_healthy_percent = 0
    }
    api = {
      command = ["uvicorn", "tripwire.api.serve:app", "--host", "0.0.0.0", "--port", "8080"]
      secrets = ["database_url", "secret_key"]
      env = {
        TRIPWIRE_BASE_PATH = "/tripwire"
      }
      maximum_percent         = 200
      minimum_healthy_percent = 100
    }
  }

  # Secret name inside the container => where ECS reads it
  tripwire_secret_sources = {
    database_url = { name = "DATABASE_URL", arn = aws_ssm_parameter.tripwire_database_url.arn }
    secret_key   = { name = "TRIPWIRE_SECRET_KEY", arn = local.tripwire_secret_key_arn }
  }

  # Only the clusterer gets a container health check (BACKEND_INFRA_NOTES.md §1): --alive fails
  # only when the loop is frozen, which a restart fixes. Same timing as compose.yaml.
  tripwire_container_extras = {
    healthCheck = {
      command     = ["CMD", "python", "-m", "tripwire.clusterer.health", "--alive"]
      interval    = 30
      timeout     = 10
      retries     = 3
      startPeriod = 60
    }
  }

  tripwire_log_options = {
    awslogs-group  = aws_cloudwatch_log_group.tripwire.name
    awslogs-region = var.region
  }
}

# ---- Database credentials (the database itself is created by the migration task)

# Letters and digits only: it ends up inside a connection URL
resource "random_password" "tripwire_db" {
  length  = 32
  special = false
}

resource "aws_ssm_parameter" "tripwire_db_password" {
  name        = "/${var.name}/tripwire/db_password"
  description = "Password of the tripwire Postgres user. The migration task sets it on the user."
  type        = "SecureString"
  value       = random_password.tripwire_db.result
}

resource "aws_ssm_parameter" "tripwire_database_url" {
  name        = "/${var.name}/tripwire/database_url"
  description = "Tripwire's own database on Laminar's RDS instance, as the tripwire user (not the master user)."
  type        = "SecureString"
  value       = "postgresql://tripwire:${random_password.tripwire_db.result}@${aws_db_instance.main.address}:${aws_db_instance.main.port}/tripwire?sslmode=require"
}

resource "aws_cloudwatch_log_group" "tripwire" {
  name              = "/ecs/${var.name}/tripwire"
  retention_in_days = 7
}

# ---- The migration task: run once per deploy by the CLI (`aws ecs run-task`), then it stops

resource "aws_ecs_task_definition" "tripwire_migrate" {
  family                   = "${var.name}-tripwire-migrate"
  requires_compatibilities = ["FARGATE"]
  network_mode             = "awsvpc"
  cpu                      = 256
  memory                   = 512
  execution_role_arn       = aws_iam_role.ecs_execution.arn
  task_role_arn            = aws_iam_role.laminar_task.arn

  runtime_platform {
    operating_system_family = "LINUX"
    cpu_architecture        = "ARM64"
  }

  container_definitions = jsonencode([
    {
      name      = "migrate"
      image     = var.tripwire_image
      essential = true
      command   = ["python", "-m", "tripwire.migrate"]
      environment = [for name, value in merge(local.tripwire_common_env, {
        TRIPWIRE_DB_NAME = "tripwire"
        # The seed stores these on the project row: where the poller and judge reach Laminar
        # (inside the VPC), and the base of "Open in Laminar" links. The project ID comes from the
        # Laminar key saved in Settings.
        LMNR_API_URL = "http://${local.app_server_host}:8000"
        LMNR_UI_URL  = local.laminar_url
      }) : { name = name, value = value }]
      secrets = [
        # The master user, only here: it creates the tripwire user and database
        { name = "MASTER_DATABASE_URL", valueFrom = aws_ssm_parameter.database_url.arn },
        { name = "TRIPWIRE_DB_PASSWORD", valueFrom = aws_ssm_parameter.tripwire_db_password.arn },
      ]
      logConfiguration = {
        logDriver = "awslogs"
        options   = merge(local.tripwire_log_options, { awslogs-stream-prefix = "migrate" })
      }
    },
  ])
}

# ---- The four services

resource "aws_ecs_task_definition" "tripwire" {
  for_each = local.tripwire_services_enabled ? local.tripwire_services : {}

  family                   = "${var.name}-tripwire-${each.key}"
  requires_compatibilities = ["FARGATE"]
  network_mode             = "awsvpc"
  cpu                      = var.tripwire_task_sizes[each.key].cpu
  memory                   = var.tripwire_task_sizes[each.key].memory
  execution_role_arn       = aws_iam_role.ecs_execution.arn
  task_role_arn            = aws_iam_role.laminar_task.arn

  runtime_platform {
    operating_system_family = "LINUX"
    cpu_architecture        = "ARM64"
  }

  container_definitions = jsonencode([
    merge(
      {
        name         = each.key
        image        = var.tripwire_services_image
        essential    = true
        command      = each.value.command
        portMappings = each.key == "api" ? [{ containerPort = 8080, protocol = "tcp" }] : []
        environment  = [for name, value in merge(local.tripwire_common_env, each.value.env) : { name = name, value = value }]
        secrets = [
          for key in each.value.secrets :
          { name = local.tripwire_secret_sources[key].name, valueFrom = local.tripwire_secret_sources[key].arn }
        ]
        logConfiguration = {
          logDriver = "awslogs"
          options   = merge(local.tripwire_log_options, { awslogs-stream-prefix = each.key })
        }
      },
      { for key, value in local.tripwire_container_extras : key => value if each.key == "clusterer" },
    ),
  ])
}

resource "aws_ecs_service" "tripwire" {
  for_each = aws_ecs_task_definition.tripwire

  name                   = "tripwire-${each.key}"
  cluster                = aws_ecs_cluster.main.id
  task_definition        = each.value.arn
  desired_count          = 1
  launch_type            = "FARGATE"
  enable_execute_command = true

  health_check_grace_period_seconds = each.key == "api" ? 60 : null

  deployment_minimum_healthy_percent = local.tripwire_services[each.key].minimum_healthy_percent
  deployment_maximum_percent         = local.tripwire_services[each.key].maximum_percent

  deployment_circuit_breaker {
    enable   = true
    rollback = true
  }

  # The app group already reaches RDS (5432), the app-server (8000) and the internet through the NAT
  network_configuration {
    subnets          = values(aws_subnet.private)[*].id
    security_groups  = [aws_security_group.app.id]
    assign_public_ip = false
  }

  dynamic "load_balancer" {
    for_each = each.key == "api" ? [1] : []
    content {
      target_group_arn = aws_lb_target_group.tripwire.arn
      container_name   = "api"
      container_port   = 8080
    }
  }

  # The target group must be attached to the ALB first; the roles must outlive the service on
  # teardown, or its tasks can't shut down cleanly
  depends_on = [
    aws_lb_listener_rule.tripwire,
    aws_iam_role_policy.ecs_execution_secrets,
    aws_iam_role_policy_attachment.ecs_execution,
  ]
}

# ---- Load balancer: /tripwire/* on Laminar's address, behind the same Cognito login

resource "aws_lb_target_group" "tripwire" {
  name        = "${var.name}-tripwire"
  port        = 8080
  protocol    = "HTTP"
  target_type = "ip"
  vpc_id      = aws_vpc.main.id

  # Same as Laminar's target groups (alb.tf). The default 300 s made every deploy and destroy wait
  # five minutes for an API that only serves a few users' short requests.
  deregistration_delay = 30

  # Also checks the database (the endpoint runs SELECT 1)
  health_check {
    path    = "/tripwire/api/health"
    matcher = "200"
  }
}

resource "aws_lb_listener_rule" "tripwire" {
  listener_arn = aws_lb_listener.https.arn
  priority     = 25 # before Laminar's catch-all (30), which would otherwise take every path

  action {
    type  = "authenticate-cognito"
    order = 1

    authenticate_cognito {
      user_pool_arn       = aws_cognito_user_pool.main.arn
      user_pool_client_id = aws_cognito_user_pool_client.alb.id
      user_pool_domain    = aws_cognito_user_pool_domain.main.domain
    }
  }

  action {
    type             = "forward"
    order            = 2
    target_group_arn = aws_lb_target_group.tripwire.arn
  }

  condition {
    host_header {
      values = [local.laminar_domain]
    }
  }

  condition {
    path_pattern {
      values = ["/tripwire", "/tripwire/*"]
    }
  }
}

# Laminar's "Signals" sidebar item (turned on by SIGNALS_ENABLED in ecs.tf) leads to Tripwire: its page
# is an enterprise-only stub in the open-source build. Covers clicks and typed URLs; Laminar's image is
# unchanged. Laminar switches pages in the browser, so a click first fetches this path in the
# background; Next.js treats the redirected, non-Next response as a full page load of /tripwire/signals.
resource "aws_lb_listener_rule" "tripwire_from_laminar_signals" {
  listener_arn = aws_lb_listener.https.arn
  priority     = 24 # before Tripwire's rule (25) and Laminar's catch-all (30)

  action {
    type = "redirect"

    redirect {
      path        = "/tripwire/signals" # the Signals list (signals/mock-ui/src/App.jsx route /signals)
      status_code = "HTTP_302"          # temporary: the route belongs to Laminar and may change upstream
    }
  }

  condition {
    host_header {
      values = [local.laminar_domain]
    }
  }

  condition {
    path_pattern {
      values = ["/project/*/signals", "/project/*/signals/*"]
    }
  }
}

resource "aws_vpc_security_group_ingress_rule" "tripwire_from_alb" {
  security_group_id            = aws_security_group.app.id
  description                  = "Tripwire API and UI from the load balancer"
  referenced_security_group_id = aws_security_group.alb.id
  ip_protocol                  = "tcp"
  from_port                    = 8080
  to_port                      = 8080
}

# ---- Alerts (to the same email topic as Laminar's)

# "Is clustering working?" is reported in the logs, not acted on (BACKEND_INFRA_NOTES.md §2): every
# such warning starts with the clusterer's UNHEALTHY_PREFIX and repeats every 5 minutes while it lasts
resource "aws_cloudwatch_log_metric_filter" "clustering_unhealthy" {
  name           = "${var.name}-clustering-unhealthy"
  log_group_name = aws_cloudwatch_log_group.tripwire.name
  pattern        = "\"clustering unhealthy:\""

  metric_transformation {
    name      = "ClusteringUnhealthy"
    namespace = "${var.name}/Tripwire"
    value     = "1"
  }
}

resource "aws_cloudwatch_metric_alarm" "clustering_unhealthy" {
  alarm_name        = "${var.name}-clustering-unhealthy"
  alarm_description = "Tripwire's clusterer reports that clustering has stalled or keeps failing; see its logs"

  namespace   = "${var.name}/Tripwire"
  metric_name = "ClusteringUnhealthy"

  statistic           = "Sum"
  period              = 600
  evaluation_periods  = 1
  comparison_operator = "GreaterThanOrEqualToThreshold"
  threshold           = 1

  # No warnings logged means healthy
  treat_missing_data = "notBreaching"

  alarm_actions = [aws_sns_topic.alerts.arn]
  ok_actions    = [aws_sns_topic.alerts.arn]
}

# Tripwire's UI and API are down: no healthy API task for 3 minutes (only once the services exist)
resource "aws_cloudwatch_metric_alarm" "tripwire_no_healthy_targets" {
  for_each = local.tripwire_services_enabled ? { api = aws_lb_target_group.tripwire } : {}

  alarm_name        = "${var.name}-tripwire-no-healthy-targets"
  alarm_description = "No healthy Tripwire API targets behind the ${var.name} load balancer"

  namespace   = "AWS/ApplicationELB"
  metric_name = "HealthyHostCount"
  dimensions = {
    TargetGroup  = each.value.arn_suffix
    LoadBalancer = aws_lb.main.arn_suffix
  }

  statistic           = "Minimum"
  period              = 60
  evaluation_periods  = 3
  comparison_operator = "LessThanThreshold"
  threshold           = 1
  treat_missing_data  = "breaching"

  alarm_actions = [aws_sns_topic.alerts.arn]
  ok_actions    = [aws_sns_topic.alerts.arn]
}
