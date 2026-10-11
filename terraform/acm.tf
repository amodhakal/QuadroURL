# TLS certificate for the public application domain.
#
# PREREQUISITE (DNS): the domain must already exist as a public Route 53 hosted
# zone in this AWS account, in the same region as the load balancer, before
# `terraform apply` will succeed. aws_acm_certificate_validation waits for the
# _acme-challenge CNAME ACM publishes, so the apply hangs/fails until that
# record is resolvable.
#
# The single provider in main.tf means the certificate is issued in the ALB's
# own region, so no separate us-east-1 validation zone/provider is required.

locals {
  # Registered domain, e.g. "example.com" for app.example.com
  domain_zone_name = join(".", slice(split(var.domain_name, "."), 1, length(split(var.domain_name, "."))))
}

data "aws_route53_zone" "app" {
  name         = local.domain_zone_name
  private_zone = false
}

resource "aws_acm_certificate" "app" {
  domain_name       = var.domain_name
  validation_method = "DNS"

  tags = {
    Name = "${var.project_name}-${var.domain_name}"
  }

  lifecycle {
    create_before_destroy = true
  }
}

resource "aws_acm_certificate_validation" "app" {
  certificate_arn = aws_acm_certificate.app.arn

  validation_record_fqdns = [
    for record in aws_acm_certificate.app.domain_validation_options :
    record.resource_record_name
  ]

  timeouts {
    create = "10m"
  }
}

# Route 53 alias pointing the domain at the load balancer's HTTPS listener.
# This is the provider's supported equivalent of an "ACM certificate alias".
resource "aws_route53_record" "app" {
  zone_id = data.aws_route53_zone.app.zone_id
  name    = var.domain_name
  type    = "A"

  alias {
    name                   = aws_lb.main.dns_name
    zone_id                = aws_lb.main.zone_id
    evaluate_target_health = false
  }

  depends_on = [aws_lb_listener.https]
}