output "alb_dns_name" {
  value       = aws_lb.main.dns_name
  description = "Application Load Balancer URL"
}

output "loadtester_public_ip" {
  value       = aws_instance.loadtester.public_ip
  description = "Load tester EC2 public IP"
}

output "ecr_repository_url" {
  value       = aws_ecr_repository.app.repository_url
  description = "ECR Image Push URL"
}

output "ecr_consumer_repository_url" {
  value       = aws_ecr_repository.consumer.repository_url
  description = "ECR Consumer Image Push URL"
}

output "cluster_name" {
  value       = aws_ecs_cluster.main.name
  description = "ECS cluster name"
}

output "app_service_name" {
  value       = aws_ecs_service.app.name
  description = "ECS app service name"
}

output "kafka_private_ip" {
  value       = aws_instance.kafka.private_ip
  description = "Kafka broker private IP"
}

output "db_identifier" {
  value       = aws_db_instance.postgres.identifier
  description = "RDS instance identifier"
}

output "db_endpoint" {
  value       = aws_db_instance.postgres.endpoint
  description = "RDS PostgreSQL endpoint (host:port), usable from the private subnets"
}

output "db_address" {
  value       = aws_db_instance.postgres.address
  description = "RDS PostgreSQL hostname"
}

output "db_port" {
  value       = aws_db_instance.postgres.port
  description = "RDS PostgreSQL port"
}

output "db_multi_az" {
  value       = aws_db_instance.postgres.multi_az
  description = "Whether the RDS instance has a standby in a second Availability Zone"
}

output "db_backup_retention_period" {
  value       = aws_db_instance.postgres.backup_retention_period
  description = "Days automated RDS snapshots are retained"
}

output "db_final_snapshot_identifier" {
  value       = local.db_final_snapshot_identifier
  description = "Identifier of the snapshot taken when the RDS instance is destroyed"
}
