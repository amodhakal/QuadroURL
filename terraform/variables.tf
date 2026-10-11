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

variable "consumer_task_count" {
  description = "Desired number of tasks for each consumer service (request-log, url-event, url-create)."
  type        = number
  default     = 3
}

variable "enable_nat_gateway" {
  description = <<-EOT
    Create a NAT gateway so tasks in private subnets have outbound internet
    access (image pulls, AWS APIs, third-party calls). A NAT gateway costs per
    hour plus per GB, so ephemeral environments can set this to false when no
    external egress is needed. Note this only provisions a single-AZ NAT
    gateway in the first public subnet; see terraform/vpc.tf for the
    availability trade-off.
  EOT
  type        = bool
  default     = true
}

variable "base_capacity_weight" {
  description = "Weight of the on-demand FARGATE base capacity provider. Must be > 0 to guarantee baseline capacity."
  type        = number
  default     = 1
}

variable "base_capacity_count" {
  description = "Minimum number of tasks that run on on-demand FARGATE, unaffected by spot interruptions."
  type        = number
  default     = 2
}

variable "spot_capacity_weight" {
  description = "Relative weight of FARGATE_SPOT used as burst capacity on top of the on-demand base."
  type        = number
  default     = 3
}

variable "autoscaling_min_capacity" {
  description = "Minimum number of app tasks the autoscaling target may scale down to. Must be >= base_capacity_count."
  type        = number
  default     = 2
}

variable "autoscaling_max_capacity" {
  description = "Maximum number of app tasks the autoscaling target may scale out to."
  type        = number
  default     = 20
}

variable "autoscaling_cpu_target" {
  description = "Target average CPU utilisation (%) for the app service CPU tracking policy."
  type        = number
  default     = 60
}

variable "autoscaling_memory_target" {
  description = "Target average memory utilisation (%) for the app service memory tracking policy."
  type        = number
  default     = 70
}

variable "autoscaling_alb_requests_target" {
  description = "Target number of requests per target for the ALBRequestCountPerTarget tracking policy. Set to 0 to disable request-count scaling."
  type        = number
  default     = 100
}

variable "autoscaling_scale_out_cooldown" {
  description = "Seconds to wait after a scale-out activity before scaling out again."
  type        = number
  default     = 60
}

variable "autoscaling_scale_in_cooldown" {
  description = "Seconds to wait after a scale-in activity before scaling in again."
  type        = number
  default     = 300
}

variable "deployment_maximum_percent" {
  description = "Maximum percentage of tasks to run during a deployment, relative to desired_count."
  type        = number
  default     = 200
}

variable "deployment_minimum_healthy_percent" {
  description = "Minimum percentage of tasks that must stay healthy during a deployment."
  type        = number
  default     = 100
}

variable "log_retention_days" {
  description = "CloudWatch Logs retention for the ECS log group. Valid values are the documented aws_cloudwatch_log_group values (1, 3, 5, 7, 14, 30, 60, 90, 120, 150, 180, 365, ...)."
  type        = number
  default     = 30
}
