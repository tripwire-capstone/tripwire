variable "name" {
  description = "Prefix for resource names, shared by every stack in one deployment."
  type        = string
  default     = "tripwire"

  validation {
    condition     = can(regex("^[a-z][a-z0-9-]{1,19}$", var.name))
    error_message = "name must be 2-20 characters: lowercase letters, digits and hyphens, starting with a letter."
  }
}

variable "region" {
  description = "AWS region; must match the region of the state bucket and the persistent stack."
  type        = string
  default     = "us-east-1"
}

variable "vpc_cidr" {
  description = "Address range for the VPC. Must not overlap any network you might connect to it later."
  type        = string
  default     = "10.20.0.0/16"

  validation {
    condition     = can(cidrnetmask(var.vpc_cidr))
    error_message = "vpc_cidr must be an IPv4 CIDR block like 10.20.0.0/16."
  }
}

variable "db_snapshot_identifier" {
  description = "RDS snapshot to create the database from, e.g. the final snapshot of an earlier teardown. Leave null for an empty database. Only read when the database is first created."
  type        = string
  default     = null
}

variable "keep_final_snapshots" {
  description = "Take a final snapshot of the database and the data volume on teardown, so the stack can be restored later."
  type        = bool
  default     = true
}

variable "db_backup_retention_days" {
  description = "Days of automated RDS backups (point-in-time restore). AWS free-plan accounts are capped, so set 1 there."
  type        = number
  default     = 7

  validation {
    condition     = var.db_backup_retention_days >= 1 && var.db_backup_retention_days <= 35
    error_message = "db_backup_retention_days must be between 1 and 35."
  }
}