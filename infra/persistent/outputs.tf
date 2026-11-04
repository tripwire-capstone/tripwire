output "certificate_arn" {
  description = "ACM certificate for domain, already validated. The stack's HTTPS listener uses it."
  value       = aws_acm_certificate_validation.main.certificate_arn
}

output "domain" {
  description = "Host name Laminar is served at."
  value       = var.domain
}

output "name_servers" {
  description = "Add these as NS records for domain at your DNS provider. Null when an existing zone was used."
  value       = local.create_zone ? aws_route53_zone.main[0].name_servers : null
}

output "zone_id" {
  description = "Route 53 zone holding domain. The stack creates its alias record to the ALB here."
  value       = local.zone_id
}