output "nat_public_ip" {
  description = "Address all outbound traffic from the private subnets comes from; give it to anyone who needs to allowlist this stack."
  value       = aws_eip.nat.public_ip
}

output "private_subnet_ids" {
  description = "Private subnets, one per AZ, keyed by AZ name. ECS tasks, RDS and the data box live here."
  value       = { for az, subnet in aws_subnet.private : az => subnet.id }
}

output "public_subnet_ids" {
  description = "Public subnets, one per AZ, keyed by AZ name. Only the ALB and NAT live here."
  value       = { for az, subnet in aws_subnet.public : az => subnet.id }
}

output "vpc_id" {
  description = "VPC that holds the whole stack."
  value       = aws_vpc.main.id
}

output "db_endpoint" {
  description = "Postgres host name, reachable only from inside the VPC."
  value       = aws_db_instance.main.address
}

output "data_box_instance_id" {
  description = "ClickHouse + Quickwit box. Open a shell with: aws ssm start-session --target <id>"
  value       = aws_instance.data.id
}

output "data_box_private_ip" {
  description = "Private address the ECS tasks use for ClickHouse (8123) and Quickwit (7280, 7281)."
  value       = aws_instance.data.private_ip
}

output "alb_dns_name" {
  description = "Load balancer's own AWS name. Only laminar_url gets past its 404, which is the point."
  value       = aws_lb.main.dns_name
}

output "laminar_url" {
  description = "Where Laminar is served."
  value       = "https://${local.laminar_domain}"
}

output "laminar_logs_command" {
  description = "Follow both Laminar containers' logs."
  value       = "aws logs tail ${aws_cloudwatch_log_group.laminar.name} --follow"
}

output "laminar_grpc_endpoint" {
  description = "Where SDKs send traces over gRPC. Port 8443 is their default, so setting the base URL is enough."
  value       = "https://${local.laminar_domain}:8443"
}
# ---- Tripwire (read by the CLI to run the migration task and show the URL)

output "tripwire_url" {
  description = "Where Tripwire's UI is served (under Laminar's address)."
  value       = local.tripwire_url
}

output "tripwire_image" {
  description = "The image the migration task uses; the CLI gives it to the services once the migration succeeds."
  value       = var.tripwire_image
}

output "tripwire_migrate_task_definition" {
  description = "Task definition (family:revision ARN) of the one-off migration task."
  value       = aws_ecs_task_definition.tripwire_migrate.arn
}

output "ecs_cluster_name" {
  description = "ECS cluster that runs Laminar's and Tripwire's services."
  value       = aws_ecs_cluster.main.name
}

output "app_security_group_id" {
  description = "Security group of the ECS tasks; the migration task runs in it."
  value       = aws_security_group.app.id
}

output "tripwire_logs_command" {
  description = "Follow all Tripwire containers' logs (migrate, poller, runner, clusterer, api)."
  value       = "aws logs tail ${aws_cloudwatch_log_group.tripwire.name} --follow"
}
