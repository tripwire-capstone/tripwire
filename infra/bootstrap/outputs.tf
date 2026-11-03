output "region" {
  description = "Region of the state bucket; the other stacks' backends need it."
  value       = var.region
}

output "state_bucket" {
  description = "S3 bucket that holds the persistent and stack state files."
  value       = aws_s3_bucket.state.bucket
}