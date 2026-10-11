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

variable "kafka_data_volume_size_gb" {
  description = "Size of the gp3 volume backing the Kafka broker's KAFKA_LOG_DIRS. Must be large enough to hold the retained log across the retention window in kafka.tf."
  type        = number
  default     = 50
}
