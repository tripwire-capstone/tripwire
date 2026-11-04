locals {
  create_zone = var.zone_id == null
  zone_id     = local.create_zone ? aws_route53_zone.main[0].zone_id : var.zone_id
}

# Kept even when the stack is torn down: a new zone gets new nameservers,
# which would mean editing the DNS provider's NS records again
resource "aws_route53_zone" "main" {
  count = local.create_zone ? 1 : 0

  name = var.domain

  lifecycle {
    prevent_destroy = true
  }
}

resource "aws_acm_certificate" "main" {
  domain_name       = var.domain
  validation_method = "DNS"

  # The ALB listener keeps using the old certificate until the new one exists
  lifecycle {
    create_before_destroy = true
  }
}

resource "aws_route53_record" "validation" {
  for_each = {
    for option in aws_acm_certificate.main.domain_validation_options : option.domain_name => {
      name   = option.resource_record_name
      type   = option.resource_record_type
      record = option.resource_record_value
    }
  }

  # ACM reuses the same record for the same name, so a recreated certificate
  # finds it already there
  allow_overwrite = true

  zone_id = local.zone_id
  name    = each.value.name
  type    = each.value.type
  ttl     = 60
  records = [each.value.record]
}

resource "aws_acm_certificate_validation" "main" {
  certificate_arn         = aws_acm_certificate.main.arn
  validation_record_fqdns = [for record in aws_route53_record.validation : record.fqdn]
}