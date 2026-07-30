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
  default   = "LoadTestPass123!"
}

variable "fargate_task_count" {
  type    = number
  default = 4
}
