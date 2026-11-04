# RDS's CA bundle for this region; the frontend needs it to verify Postgres over SSL
data "http" "rds_ca" {
  url = "https://truststore.pki.rds.amazonaws.com/${var.region}/${var.region}-bundle.pem"

  lifecycle {
    postcondition {
      condition     = self.status_code == 200
      error_message = "Could not download the RDS certificate bundle for ${var.region}."
    }
  }
}

locals {
  laminar_url = "https://${local.laminar_domain}"

  laminar_common_env = {
    ENVIRONMENT         = "LITE"
    CLICKHOUSE_URL      = "http://${aws_instance.data.private_ip}:8123"
    CLICKHOUSE_USER     = "ch_user"
    CLICKHOUSE_RO_USER  = "ch_user"
    QUICKWIT_SEARCH_URL = "http://${aws_instance.data.private_ip}:7280"
  }

  app_server_env = merge(local.laminar_common_env, {
    PORT                    = "8000"
    GRPC_PORT               = "8001"
    PGSSLMODE               = "require"
    QUICKWIT_INGEST_URL     = "http://${aws_instance.data.private_ip}:7281"
    QUICKWIT_SPANS_INDEX_ID = "spans_v2"
  })

  frontend_env = merge(local.laminar_common_env, {
    PORT                       = "5667"
    BACKEND_URL                = "http://localhost:8000"
    BACKEND_RT_URL             = "http://localhost:8002"
    NEXTAUTH_URL               = local.laminar_url
    BETTER_AUTH_URL            = local.laminar_url
    NEXT_PUBLIC_URL            = local.laminar_url
    LAMINAR_TELEMETRY_DISABLED = "true"
    DATABASE_SSL_ROOT_CERT     = data.http.rds_ca.response_body

    # The frontend runs the ClickHouse migrations, and its migration tool leaks one pooled
    # connection per statement; with the default pool of 10 every tenth statement waits 30 s.
    # A bigger pool takes first boot from about 14 minutes to seconds. Frontend only: the
    # app-server uses a different (Rust) client that doesn't read this parameter.
    CLICKHOUSE_URL = "http://${aws_instance.data.private_ip}:8123?max_open_connections=1000"
  })

  # Name inside the container => SSM parameter ARN; ECS reads them at task start
  app_server_secrets = {
    DATABASE_URL           = aws_ssm_parameter.database_url.arn
    CLICKHOUSE_PASSWORD    = aws_ssm_parameter.clickhouse_password.arn
    CLICKHOUSE_RO_PASSWORD = aws_ssm_parameter.clickhouse_password.arn
    SHARED_SECRET_TOKEN    = aws_ssm_parameter.shared_secret_token.arn
    AEAD_SECRET_KEY        = aws_ssm_parameter.aead_secret_key.arn
  }

  frontend_secrets = merge(local.app_server_secrets, {
    NEXTAUTH_SECRET    = aws_ssm_parameter.auth_secret.arn
    BETTER_AUTH_SECRET = aws_ssm_parameter.auth_secret.arn
  })
}

resource "aws_ecs_cluster" "main" {
  name = var.name

  setting {
    name  = "containerInsights"
    value = "disabled"
  }
}

resource "aws_cloudwatch_log_group" "laminar" {
  name              = "/ecs/${var.name}/laminar"
  retention_in_days = 7
}

data "aws_iam_policy_document" "ecs_tasks_assume" {
  statement {
    actions = ["sts:AssumeRole"]

    principals {
      type        = "Service"
      identifiers = ["ecs-tasks.amazonaws.com"]
    }
  }
}

# Used by ECS itself, before the containers start: pull images, write logs, read secrets
resource "aws_iam_role" "ecs_execution" {
  name               = "${var.name}-ecs-execution"
  assume_role_policy = data.aws_iam_policy_document.ecs_tasks_assume.json
}

resource "aws_iam_role_policy_attachment" "ecs_execution" {
  role       = aws_iam_role.ecs_execution.name
  policy_arn = "arn:aws:iam::aws:policy/service-role/AmazonECSTaskExecutionRolePolicy"
}

data "aws_iam_policy_document" "ecs_execution_secrets" {
  statement {
    sid       = "ReadLaminarSecrets"
    actions   = ["ssm:GetParameters"]
    resources = distinct(values(local.frontend_secrets))
  }
}

resource "aws_iam_role_policy" "ecs_execution_secrets" {
  name   = "read-laminar-secrets"
  role   = aws_iam_role.ecs_execution.id
  policy = data.aws_iam_policy_document.ecs_execution_secrets.json
}

# Used by the running containers; only ECS Exec's shell channel needs anything
resource "aws_iam_role" "laminar_task" {
  name               = "${var.name}-laminar-task"
  assume_role_policy = data.aws_iam_policy_document.ecs_tasks_assume.json
}

data "aws_iam_policy_document" "ecs_exec" {
  statement {
    sid = "EcsExec"
    actions = [
      "ssmmessages:CreateControlChannel",
      "ssmmessages:CreateDataChannel",
      "ssmmessages:OpenControlChannel",
      "ssmmessages:OpenDataChannel",
    ]
    resources = ["*"]
  }
}

resource "aws_iam_role_policy" "laminar_task_exec" {
  name   = "ecs-exec"
  role   = aws_iam_role.laminar_task.id
  policy = data.aws_iam_policy_document.ecs_exec.json
}

resource "aws_ecs_task_definition" "laminar" {
  family                   = "${var.name}-laminar"
  requires_compatibilities = ["FARGATE"]
  network_mode             = "awsvpc"
  cpu                      = 1024
  memory                   = 2048
  execution_role_arn       = aws_iam_role.ecs_execution.arn
  task_role_arn            = aws_iam_role.laminar_task.arn

  runtime_platform {
    operating_system_family = "LINUX"
    cpu_architecture        = "ARM64"
  }

  container_definitions = jsonencode([
    {
      name      = "app-server"
      image     = "ghcr.io/lmnr-ai/app-server:${var.laminar_version}"
      essential = true
      portMappings = [
        { containerPort = 8000, protocol = "tcp" },
        { containerPort = 8001, protocol = "tcp" },
        { containerPort = 8002, protocol = "tcp" },
      ]
      environment = [for name, value in local.app_server_env : { name = name, value = value }]
      secrets     = [for name, arn in local.app_server_secrets : { name = name, valueFrom = arn }]
      logConfiguration = {
        logDriver = "awslogs"
        options = {
          awslogs-group         = aws_cloudwatch_log_group.laminar.name
          awslogs-region        = var.region
          awslogs-stream-prefix = "app-server"
        }
      }
    },
    {
      name      = "frontend"
      image     = "ghcr.io/lmnr-ai/frontend:${var.laminar_version}"
      essential = true
      portMappings = [
        { containerPort = 5667, protocol = "tcp" },
      ]
      environment = [for name, value in local.frontend_env : { name = name, value = value }]
      secrets     = [for name, arn in local.frontend_secrets : { name = name, valueFrom = arn }]
      logConfiguration = {
        logDriver = "awslogs"
        options = {
          awslogs-group         = aws_cloudwatch_log_group.laminar.name
          awslogs-region        = var.region
          awslogs-stream-prefix = "frontend"
        }
      }
    },
  ])
}

resource "aws_ecs_service" "laminar" {
  name                   = "laminar"
  cluster                = aws_ecs_cluster.main.id
  task_definition        = aws_ecs_task_definition.laminar.arn
  desired_count          = 1
  launch_type            = "FARGATE"
  enable_execute_command = true

  # The first start runs ClickHouse migrations; earlier health checks would kill it mid-way
  health_check_grace_period_seconds = 600

  deployment_minimum_healthy_percent = 100
  deployment_maximum_percent         = 200

  deployment_circuit_breaker {
    enable   = true
    rollback = true
  }

  network_configuration {
    subnets          = values(aws_subnet.private)[*].id
    security_groups  = [aws_security_group.app.id]
    assign_public_ip = false
  }

  load_balancer {
    target_group_arn = aws_lb_target_group.frontend.arn
    container_name   = "frontend"
    container_port   = 5667
  }

  load_balancer {
    target_group_arn = aws_lb_target_group.api.arn
    container_name   = "app-server"
    container_port   = 8000
  }

  load_balancer {
    target_group_arn = aws_lb_target_group.grpc.arn
    container_name   = "app-server"
    container_port   = 8001
  }

  # Target groups must be attached to the ALB first; the roles must outlive the
  # service on teardown, or its tasks can't shut down cleanly
  depends_on = [
    aws_lb_listener_rule.frontend,
    aws_lb_listener_rule.api,
    aws_lb_listener_rule.grpc,
    aws_iam_role_policy.ecs_execution_secrets,
    aws_iam_role_policy_attachment.ecs_execution,
  ]
}