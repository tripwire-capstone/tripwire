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

variable "laminar_image_digests" {
  description = "Image digests for laminar_version (multi-arch index digests from ghcr.io). The digest decides which image runs, so update both together with laminar_version."
  type = object({
    app_server = string
    frontend   = string
  })
  default = {
    app_server = "sha256:f98dade091979db2c2d24ce21dd30f9140aab9c4404afdff07a21f810dc30adf"
    frontend   = "sha256:4b2b1b1b708b365a7c57cd4b22d6d3a6b6bb31db47c5b8bdb8a39cfbfc55374f"
  }

  validation {
    condition     = alltrue([for digest in values(var.laminar_image_digests) : can(regex("^sha256:[a-f0-9]{64}$", digest))])
    error_message = "Each digest must look like sha256: followed by 64 hex characters."
  }
}
variable "task_sizes" {
  description = "Fargate CPU units and memory (MB) for each Laminar service. Must be a valid Fargate pair, e.g. cpu 512 with memory 1024-4096. Raise memory if a task stops with OutOfMemoryError."
  type = object({
    app_server = object({ cpu = number, memory = number })
    frontend   = object({ cpu = number, memory = number })
  })
  default = {
    app_server = { cpu = 512, memory = 1024 }
    frontend   = { cpu = 512, memory = 1024 }
  }
}

variable "data_snapshot_daily_retention_count" {
  description = "How many daily snapshots of the data volume to keep, alongside the ones every data_snapshot_interval_hours. 7 keeps a week."
  type        = number
  default     = 7

  validation {
    condition     = var.data_snapshot_daily_retention_count >= 1 && var.data_snapshot_daily_retention_count <= 1000
    error_message = "data_snapshot_daily_retention_count must be between 1 and 1000."
  }
}

variable "alert_email" {
  description = "Email that gets alerts from this stack: failed or missing data volume snapshots, Laminar down, load balancer errors, data box failures and AWS confirmation links."
  type        = string

  validation {
    condition     = can(regex("^[^@\\s]+@[^@\\s]+\\.[^@\\s]+$", var.alert_email))
    error_message = "alert_email must be an email address."
  }
}

# ---- Tripwire (the Signals backend, tripwire.tf)

variable "tripwire_image" {
  description = "The published Tripwire image, pinned by digest (ghcr.io/<owner>/tripwire-backend@sha256:...). The migration task always uses it; the services get it through tripwire_services_image."
  type        = string
  # Built from commit 3ef3b78 (tag :3ef3b78): Settings, MCP, the Slack notifier, signal templates,
  # summarizer v5 + threshold 0.24, summaries 4 at a time. When publishing a new image, replace the digest.
  default = "ghcr.io/tripwire-capstone/tripwire-backend@sha256:3801d75c8641ad7da79c2739c622de331634b79953437e8f357467a9d028bb84"

  validation {
    condition     = var.tripwire_image != null && can(regex("^[a-z0-9.-]+(/[a-z0-9._-]+)+@sha256:[a-f0-9]{64}$", var.tripwire_image))
    error_message = "Set tripwire_image to a published image pinned by digest, like ghcr.io/tripwire-capstone/tripwire-backend@sha256:<64 hex characters>."
  }
}

variable "tripwire_services_image" {
  description = "Image the four Tripwire services run. Empty = the services aren't created yet (first deploy, before the migration). The CLI sets it to tripwire_image once the migration task has succeeded, so services never run against an old schema."
  type        = string
  default     = ""

  validation {
    condition     = var.tripwire_services_image == "" || can(regex("^[a-z0-9.-]+(/[a-z0-9._-]+)+@sha256:[a-f0-9]{64}$", var.tripwire_services_image))
    error_message = "tripwire_services_image must be empty or an image pinned by digest (...@sha256:<64 hex characters>)."
  }
}

variable "judge_default_model" {
  description = "OpenAI model new Signals use by default (JUDGE_DEFAULT_MODEL)."
  type        = string
  default     = "gpt-6-luna"

  validation {
    condition     = length(trimspace(var.judge_default_model)) > 0
    error_message = "judge_default_model can't be empty."
  }
}

variable "tripwire_task_sizes" {
  description = "Fargate CPU units and memory (MB) for each Tripwire service. Must be valid Fargate pairs, e.g. cpu 256 with memory 512-2048."
  type = object({
    poller    = object({ cpu = number, memory = number })
    runner    = object({ cpu = number, memory = number })
    clusterer = object({ cpu = number, memory = number })
    notifier  = object({ cpu = number, memory = number })
    api       = object({ cpu = number, memory = number })
  })
  default = {
    poller    = { cpu = 256, memory = 512 }
    runner    = { cpu = 256, memory = 1024 }
    clusterer = { cpu = 512, memory = 1024 }
    notifier  = { cpu = 256, memory = 512 }
    api       = { cpu = 256, memory = 512 }
  }
}
