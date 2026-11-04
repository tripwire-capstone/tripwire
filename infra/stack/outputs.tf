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