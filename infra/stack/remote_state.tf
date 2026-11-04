data "aws_caller_identity" "current" {}

# Same naming rule as infra/bootstrap, so the stack finds the bucket without another input
data "terraform_remote_state" "persistent" {
  backend = "s3"

  config = {
    bucket = "${var.name}-tfstate-${data.aws_caller_identity.current.account_id}-${var.region}"
    key    = "persistent/terraform.tfstate"
    region = var.region
  }
}

locals {
  laminar_domain  = data.terraform_remote_state.persistent.outputs.domain
  certificate_arn = data.terraform_remote_state.persistent.outputs.certificate_arn
  zone_id         = data.terraform_remote_state.persistent.outputs.zone_id
}