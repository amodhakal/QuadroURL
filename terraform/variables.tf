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

variable "domain_name" {
  type        = string
  description = <<-EOT
    Fully qualified application domain served by the ALB (e.g. app.example.com).
    The parent domain must already exist as a public Route 53 hosted zone in this
    account and region before `terraform apply`, otherwise ACM DNS validation
    cannot complete.
  EOT
  default     = "app.example.com"
}
