variable "domain" {
  description = "Host name Laminar is served at, e.g. laminar.example.com. The certificate covers exactly this name."
  type        = string

  validation {
    condition     = can(regex("^([a-z0-9-]+\\.)+[a-z]{2,}$", var.domain))
    error_message = "domain must be a lowercase host name like laminar.example.com, with no scheme or path."
  }
}

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
  description = "AWS region; must match the region of the state bucket and the stack."
  type        = string
  default     = "us-east-1"
}

variable "zone_id" {
  description = "Existing Route 53 hosted zone that contains domain. Leave null to create a zone for domain and delegate it from your DNS provider."
  type        = string
  default     = null
}