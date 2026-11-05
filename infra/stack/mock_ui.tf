# The Tripwire mock UI at https://<domain>/tripwire/, behind the same login
# as Laminar. A throwaway: delete this file to remove it.

variable "mock_ui_image" {
  description = "Mock UI image pinned by digest, from the Mock UI image workflow's run summary."
  type        = string
  default     = ""

  validation {
    condition     = can(regex("^ghcr\\.io/[a-z0-9-]+/mock-ui@sha256:[a-f0-9]{64}$", var.mock_ui_image))
    error_message = "Set mock_ui_image to ghcr.io/<owner>/mock-ui@sha256:<digest> from the workflow run."
  }
}

resource "aws_cloudwatch_log_group" "mock_ui" {
  name              = "/ecs/${var.name}/mock-ui"
  retention_in_days = 7
}

resource "aws_security_group" "mock_ui" {
  name        = "${var.name}-mock-ui"
  description = "Mock UI tasks: only from the load balancer"
  vpc_id      = aws_vpc.main.id

  tags = {
    Name = "${var.name}-mock-ui"
  }
}

resource "aws_vpc_security_group_ingress_rule" "mock_ui_from_alb" {
  security_group_id            = aws_security_group.mock_ui.id
  description                  = "nginx from the load balancer"
  referenced_security_group_id = aws_security_group.alb.id
  ip_protocol                  = "tcp"
  from_port                    = 80
  to_port                      = 80
}

# Pulls the image from GHCR through the NAT
resource "aws_vpc_security_group_egress_rule" "mock_ui_outbound" {
  security_group_id = aws_security_group.mock_ui.id
  description       = "All outbound"
  cidr_ipv4         = "0.0.0.0/0"
  ip_protocol       = "-1"
}

resource "aws_lb_target_group" "mock_ui" {
  name                 = "${var.name}-mock-ui"
  port                 = 80
  protocol             = "HTTP"
  target_type          = "ip"
  vpc_id               = aws_vpc.main.id
  deregistration_delay = 10

  health_check {
    path    = "/tripwire/"
    matcher = "200"
  }
}

# Ahead of Laminar's catch-all (priority 30) on the same host
resource "aws_lb_listener_rule" "mock_ui" {
  listener_arn = aws_lb_listener.https.arn
  priority     = 25

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
    target_group_arn = aws_lb_target_group.mock_ui.arn
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

resource "aws_ecs_task_definition" "mock_ui" {
  family                   = "${var.name}-mock-ui"
  requires_compatibilities = ["FARGATE"]
  network_mode             = "awsvpc"
  cpu                      = 256
  memory                   = 512
  execution_role_arn       = aws_iam_role.ecs_execution.arn

  runtime_platform {
    operating_system_family = "LINUX"
    cpu_architecture        = "ARM64"
  }

  container_definitions = jsonencode([
    {
      name      = "mock-ui"
      image     = var.mock_ui_image
      essential = true
      portMappings = [
        { containerPort = 80, protocol = "tcp" },
      ]
      logConfiguration = {
        logDriver = "awslogs"
        options = {
          awslogs-group         = aws_cloudwatch_log_group.mock_ui.name
          awslogs-region        = var.region
          awslogs-stream-prefix = "mock-ui"
        }
      }
    },
  ])
}

resource "aws_ecs_service" "mock_ui" {
  name            = "mock-ui"
  cluster         = aws_ecs_cluster.main.id
  task_definition = aws_ecs_task_definition.mock_ui.arn
  desired_count   = 1
  launch_type     = "FARGATE"

  health_check_grace_period_seconds = 30

  deployment_minimum_healthy_percent = 100
  deployment_maximum_percent         = 200

  deployment_circuit_breaker {
    enable   = true
    rollback = true
  }

  network_configuration {
    subnets          = values(aws_subnet.private)[*].id
    security_groups  = [aws_security_group.mock_ui.id]
    assign_public_ip = false
  }

  load_balancer {
    target_group_arn = aws_lb_target_group.mock_ui.arn
    container_name   = "mock-ui"
    container_port   = 80
  }

  # The rule attaches the target group; the role must outlive the service
  depends_on = [
    aws_lb_listener_rule.mock_ui,
    aws_iam_role_policy_attachment.ecs_execution,
  ]
}

output "tripwire_url" {
  description = "Where the Tripwire mock UI is served."
  value       = "https://${local.laminar_domain}/tripwire/"
}
