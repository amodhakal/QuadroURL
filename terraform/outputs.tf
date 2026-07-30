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
