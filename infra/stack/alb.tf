resource "aws_lb" "main" {
  name               = var.name
  load_balancer_type = "application"
  internal           = false
  security_groups    = [aws_security_group.alb.id]
  subnets            = values(aws_subnet.public)[*].id

  drop_invalid_header_fields = true

  tags = {
    Name = var.name
  }
}

resource "aws_lb_target_group" "frontend" {
  name                 = "${var.name}-frontend"
  port                 = 5667
  protocol             = "HTTP"
  target_type          = "ip"
  vpc_id               = aws_vpc.main.id
  deregistration_delay = 30

  health_check {
    path    = "/"
    matcher = "200-399"
  }
}

resource "aws_lb_target_group" "api" {
  name                 = "${var.name}-api"
  port                 = 8000
  protocol             = "HTTP"
  target_type          = "ip"
  vpc_id               = aws_vpc.main.id
  deregistration_delay = 30

  health_check {
    path    = "/health"
    matcher = "200"
  }
}

resource "aws_lb_target_group" "grpc" {
  name                 = "${var.name}-grpc"
  port                 = 8001
  protocol             = "HTTP"
  protocol_version     = "GRPC"
  target_type          = "ip"
  vpc_id               = aws_vpc.main.id
  deregistration_delay = 30

  # Calls a method app-server doesn't have; code 12 (unimplemented) proves gRPC answers
  health_check {
    path    = "/AWS.ALB/healthcheck"
    matcher = "12"
  }
}

resource "aws_lb_listener" "http" {
  load_balancer_arn = aws_lb.main.arn
  port              = 80
  protocol          = "HTTP"

  default_action {
    type = "redirect"

    redirect {
      port        = "443"
      protocol    = "HTTPS"
      status_code = "HTTP_301"
    }
  }
}

resource "aws_lb_listener" "https" {
  load_balancer_arn = aws_lb.main.arn
  port              = 443
  protocol          = "HTTPS"
  ssl_policy        = "ELBSecurityPolicy-TLS13-1-2-PQ-2025-09"
  certificate_arn   = local.certificate_arn

  # Unknown host names get nothing
  default_action {
    type = "fixed-response"

    fixed_response {
      content_type = "text/plain"
      message_body = "Not found"
      status_code  = "404"
    }
  }
}

resource "aws_lb_listener_rule" "grpc" {
  listener_arn = aws_lb_listener.https.arn
  priority     = 10

  action {
    type             = "forward"
    target_group_arn = aws_lb_target_group.grpc.arn
  }

  condition {
    host_header {
      values = [local.laminar_domain]
    }
  }

  condition {
    http_header {
      http_header_name = "content-type"
      values           = ["application/grpc*"]
    }
  }
}

resource "aws_lb_listener_rule" "api" {
  listener_arn = aws_lb_listener.https.arn
  priority     = 20

  action {
    type             = "forward"
    target_group_arn = aws_lb_target_group.api.arn
  }

  condition {
    host_header {
      values = [local.laminar_domain]
    }
  }

  condition {
    path_pattern {
      values = ["/v1/*"]
    }
  }
}

resource "aws_lb_listener_rule" "frontend" {
  listener_arn = aws_lb_listener.https.arn
  priority     = 30

  # Invite-only: the UI is reachable only after logging in to the Cognito pool
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
    target_group_arn = aws_lb_target_group.frontend.arn
  }

  condition {
    host_header {
      values = [local.laminar_domain]
    }
  }
}

resource "aws_route53_record" "laminar" {
  zone_id = local.zone_id
  name    = local.laminar_domain
  type    = "A"

  alias {
    name                   = aws_lb.main.dns_name
    zone_id                = aws_lb.main.zone_id
    evaluate_target_health = false
  }
}