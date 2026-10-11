variable "aws_region" {
  type    = string
  default = "us-east-1"
}

variable "project_name" {
  type    = string
  default = "url-shortener-loadtest"
}

variable "db_password" {
  type      = string
  sensitive = true
}

variable "fargate_task_count" {
  type    = number
  default = 4
}

variable "db_instance_class" {
  description = "RDS instance class for the PostgreSQL database. Gravitational (db.t4g) does not support Performance Insights; use db.t3.medium or larger if Performance Insights is required."
  type        = string
  default     = "db.t4g.medium"
}

variable "db_allocated_storage" {
  description = "Initial allocated storage (GiB) for the PostgreSQL database. RDS storage auto-enables storage autoscaling once this is reached."
  type        = number
  default     = 20
}

variable "db_multi_az" {
  description = "Deploy a synchronous standby in a second Availability Zone for automatic failover. Doubles instance cost and requires the db subnet group to span at least two AZs."
  type        = bool
  default     = true
}

variable "db_backup_retention_period" {
  description = "Number of days automated snapshots are retained. 0 disables automated backups entirely and is not recommended for anything but throwaway environments."
  type        = number
  default     = 7

  validation {
    condition     = var.db_backup_retention_period >= 0 && var.db_backup_retention_period <= 35
    error_message = "db_backup_retention_period must be between 0 and 35 days."
  }
}

variable "db_backup_window" {
  description = "Daily UTC window for automated snapshots, in HH:MM-HH:MM format. Kept outside peak hours so backups do not compete with application traffic."
  type        = string
  default     = "03:00-04:00"

  validation {
    condition     = can(regex("^([01][0-9]|2[0-3]):[0-5][0-9]-([01][0-9]|2[0-3]):[0-5][0-9]$", var.db_backup_window))
    error_message = "db_backup_window must be in HH:MM-HH:MM format."
  }
}

variable "db_final_snapshot_identifier" {
  description = "Identifier for the snapshot taken when the instance is destroyed. Defaults to \"<project_name>-final\"; deterministic and timestamp-free, so clear or rename any existing snapshot with this identifier before destroying or the destroy will fail."
  type        = string
  default     = null
}

variable "db_monitoring_interval" {
  description = "Enhanced Monitoring granularity in seconds. 0 disables Enhanced Monitoring. Costs more for longer retention (default CloudWatch Logs retention applies)."
  type        = number
  default     = 60

  validation {
    condition     = contains([0, 1, 5, 10, 15, 30, 60], var.db_monitoring_interval)
    error_message = "db_monitoring_interval must be one of 0, 1, 5, 10, 15, 30 or 60 seconds."
  }
}

variable "db_monitoring_role_arn" {
  description = "IAM role ARN for RDS Enhanced Monitoring to publish metrics to CloudWatch. When null, AWS creates and uses a service role automatically; supply one explicitly if your account restricts that behaviour."
  type        = string
  default     = null
}
