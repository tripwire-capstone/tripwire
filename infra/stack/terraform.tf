terraform {
  required_version = ">= 1.10"

  required_providers {
    aws = {
      source  = "hashicorp/aws"
      version = "~> 6.0"
    }
    http = {
      source  = "hashicorp/http"
      version = "~> 3.0"
    }
    random = {
      source  = "hashicorp/random"
      version = "~> 3.0"
    }
  }

  # Bucket and region come from backend.hcl at init, since each deployer has their own
  backend "s3" {
    key          = "stack/terraform.tfstate"
    use_lockfile = true
    encrypt      = true
  }
}
