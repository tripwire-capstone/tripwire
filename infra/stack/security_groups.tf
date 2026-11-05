# Ports each layer accepts from the layer in front of it
locals {
  app_ports = {
    frontend = 5667
    api      = 8000
    grpc     = 8001
  }

  data_ports = {
    clickhouse      = 8123
    quickwit_search = 7280
    quickwit_ingest = 7281
  }

  # The frontend calls the app-server directly; both services share the app group
  app_internal_ports = {
    api      = 8000
    realtime = 8002
  }
}

resource "aws_security_group" "alb" {
  name        = "${var.name}-alb"
  description = "Laminar load balancer: HTTPS and HTTP from the internet"
  vpc_id      = aws_vpc.main.id

  tags = {
    Name = "${var.name}-alb"
  }
}

resource "aws_security_group" "app" {
  name        = "${var.name}-app"
  description = "Laminar ECS tasks: only from the load balancer"
  vpc_id      = aws_vpc.main.id

  tags = {
    Name = "${var.name}-app"
  }
}

resource "aws_security_group" "rds" {
  name        = "${var.name}-rds"
  description = "Laminar Postgres: only from the ECS tasks"
  vpc_id      = aws_vpc.main.id

  tags = {
    Name = "${var.name}-rds"
  }
}

resource "aws_security_group" "data" {
  name        = "${var.name}-data"
  description = "Laminar ClickHouse and Quickwit: only from the ECS tasks"
  vpc_id      = aws_vpc.main.id

  tags = {
    Name = "${var.name}-data"
  }
}

resource "aws_vpc_security_group_ingress_rule" "alb_https" {
  security_group_id = aws_security_group.alb.id
  description       = "HTTPS and gRPC from anywhere"
  cidr_ipv4         = "0.0.0.0/0"
  ip_protocol       = "tcp"
  from_port         = 443
  to_port           = 443
}

resource "aws_vpc_security_group_ingress_rule" "alb_http" {
  security_group_id = aws_security_group.alb.id
  description       = "HTTP from anywhere, only redirected to HTTPS"
  cidr_ipv4         = "0.0.0.0/0"
  ip_protocol       = "tcp"
  from_port         = 80
  to_port           = 80
}

resource "aws_vpc_security_group_ingress_rule" "alb_grpc" {
  security_group_id = aws_security_group.alb.id
  description       = "gRPC from anywhere, on the SDK default port"
  cidr_ipv4         = "0.0.0.0/0"
  ip_protocol       = "tcp"
  from_port         = 8443
  to_port           = 8443
}

resource "aws_vpc_security_group_ingress_rule" "app_from_alb" {
  for_each = local.app_ports

  security_group_id            = aws_security_group.app.id
  description                  = "Laminar ${each.key} from the load balancer"
  referenced_security_group_id = aws_security_group.alb.id
  ip_protocol                  = "tcp"
  from_port                    = each.value
  to_port                      = each.value
}

# With frontend and app-server in separate tasks, the frontend's internal API and
# realtime calls cross the network, so the app group must accept them from itself
resource "aws_vpc_security_group_ingress_rule" "app_internal" {
  for_each = local.app_internal_ports

  security_group_id            = aws_security_group.app.id
  description                  = "Laminar ${each.key} from the frontend"
  referenced_security_group_id = aws_security_group.app.id
  ip_protocol                  = "tcp"
  from_port                    = each.value
  to_port                      = each.value
}

resource "aws_vpc_security_group_ingress_rule" "rds_from_app" {
  security_group_id            = aws_security_group.rds.id
  description                  = "Postgres from the ECS tasks"
  referenced_security_group_id = aws_security_group.app.id
  ip_protocol                  = "tcp"
  from_port                    = 5432
  to_port                      = 5432
}

resource "aws_vpc_security_group_ingress_rule" "data_from_app" {
  for_each = local.data_ports

  security_group_id            = aws_security_group.data.id
  description                  = "${each.key} from the ECS tasks"
  referenced_security_group_id = aws_security_group.app.id
  ip_protocol                  = "tcp"
  from_port                    = each.value
  to_port                      = each.value
}

# Terraform deletes AWS's default allow-all outbound rule, so outbound is explicit.
# RDS gets none: it only answers connections, never starts them.
resource "aws_vpc_security_group_egress_rule" "all_outbound" {
  for_each = {
    alb  = aws_security_group.alb.id
    app  = aws_security_group.app.id
    data = aws_security_group.data.id
  }

  security_group_id = each.value
  description       = "All outbound"
  cidr_ipv4         = "0.0.0.0/0"
  ip_protocol       = "-1"
}