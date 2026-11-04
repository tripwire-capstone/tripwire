terraform {
  required_version = ">= 1.10"

  required_providers {
    aws = {
      source  = "hashicorp/aws"
      version = "~> 6.0"
    }
  }

  # Bucket and region come from backend.hcl at init, since each deployer has their own
  backend "s3" {
    key          = "persistent/terraform.tfstate"
    use_lockfile = true
    encrypt      = true
  }
}