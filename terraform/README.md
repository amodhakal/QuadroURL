# Terraform

Infrastructure for the QuadroURL load-test environment: VPC, ALB, ECS/Fargate,
RDS, ElastiCache, Kafka and the load tester.

## TLS / ACM prerequisite

The ALB terminates TLS using an ACM certificate issued for `var.domain_name`
(default `app.example.com`).

**Before `terraform apply` succeeds, the parent domain must already exist as a
public Route 53 hosted zone in the same AWS account and region as the load
balancer.** The certificate is issued in the ALB's own region, so no separate
`us-east-1` validation zone or aliased provider is needed.

If the zone does not exist, `aws_acm_certificate_validation` cannot complete:

- ACM publishes a `_acme-challenge.<domain>` CNAME record when the certificate is
  requested.
- Validation waits for that record to resolve, so `terraform apply` blocks until
  the `10m` create timeout and then fails.

`aws_route53_record.app` additionally creates the Route 53 A alias pointing the
domain at the ALB's HTTPS listener.

To use a real domain:

```hcl
# terraform.tfvars
domain_name = "app.yourdomain.com"
```

## Listeners

| Port | Protocol | Action                                   |
| ---- | -------- | ---------------------------------------- |
| 443  | HTTPS    | Forward to the app target group          |
| 80   | HTTP     | `HTTP_301` redirect to port 443          |

Port 80 exists solely for the redirect; no plaintext traffic reaches the
application. The HTTPS listener uses the `ELBSecurityPolicy-TLS13-1-2-2021-06`
security policy.

## Commands

```bash
terraform fmt -recursive .
terraform init
terraform validate
terraform plan
```