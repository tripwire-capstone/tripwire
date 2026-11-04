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

variable "data_instance_type" {
  description = "EC2 type for the ClickHouse + Quickwit box; needs at least 4 GB of memory. Free-plan accounts: use c7i-flex.large."
  type        = string
  default     = "t4g.medium"
}

variable "data_volume_size" {
  description = "Size in GB of the encrypted volume that holds ClickHouse and Quickwit data."
  type        = number
  default     = 30
}

variable "data_volume_snapshot_id" {
  description = "EBS snapshot to create the data volume from, e.g. the final snapshot of an earlier teardown. Leave null for an empty volume. Only read when the volume is first created."
  type        = string
  default     = null
}

variable "data_snapshot_interval_hours" {
  description = "Hours between scheduled snapshots of the data volume while the stack runs. At most this much ClickHouse and Quickwit data is lost if the volume fails."
  type        = number
  default     = 1

  validation {
    condition     = contains([1, 2, 3, 4, 6, 8, 12, 24], var.data_snapshot_interval_hours)
    error_message = "data_snapshot_interval_hours must be one of 1, 2, 3, 4, 6, 8, 12 or 24 (the intervals Data Lifecycle Manager supports)."
  }
}

variable "data_snapshot_retention_count" {
  description = "How many scheduled snapshots of the data volume to keep; older ones are deleted. With hourly snapshots, 24 keeps one day."
  type        = number
  default     = 24

  validation {
    condition     = var.data_snapshot_retention_count >= 1 && var.data_snapshot_retention_count <= 1000
    error_message = "data_snapshot_retention_count must be between 1 and 1000."
  }
}

variable "laminar_users" {
  description = "Emails allowed through the login in front of the Laminar UI. Each gets an email with a temporary password. Nobody else can sign up."
  type        = list(string)
  default     = []

  validation {
    condition     = alltrue([for email in var.laminar_users : can(regex("^[^@\\s]+@[^@\\s]+\\.[^@\\s]+$", email))])
    error_message = "Every entry in laminar_users must be an email address."
  }
}

variable "laminar_version" {
  description = "Laminar release for both the app-server and frontend images. They must always match."
  type        = string
  default     = "v0.3.3"
}