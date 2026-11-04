output "public_subnet_ids" {
  description = "Public subnets, one per AZ, keyed by AZ name."
  value       = { for az, subnet in aws_subnet.public : az => subnet.id }
}

output "vpc_id" {
  description = "VPC that holds the whole stack."
  value       = aws_vpc.main.id
}