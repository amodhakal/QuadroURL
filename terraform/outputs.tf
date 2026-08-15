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
