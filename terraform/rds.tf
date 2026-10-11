resource "aws_db_subnet_group" "main" {
  name       = var.project_name
  subnet_ids = aws_subnet.private[*].id
}

locals {
  # Terraform cannot interpolate var.project_name into another variable's
  # default, so the deterministic fallback is computed here instead.
  db_final_snapshot_identifier = coalesce(var.db_final_snapshot_identifier, "${var.project_name}-final")
}

# NOTE on replacement semantics (plan before applying):
#   - storage_encrypted: flipping false -> true is applied in place on recent
#     PostgreSQL versions, but AWS still forces a replacement for several
#     engine/instance combinations. Always read the plan.
#   - multi_az: applied in place but requires a stop/start of the instance,
#     i.e. a deliberate write outage.
#   - deletion_protection / skip_final_snapshot / backup settings are
#     in-place updates that cause no downtime.
resource "aws_db_instance" "postgres" {
  identifier             = var.project_name
  engine                 = "postgres"
  engine_version         = "15"
  instance_class         = var.db_instance_class
  allocated_storage      = var.db_allocated_storage
  db_name                = "appdb"
  username               = "appuser"
  password               = var.db_password
  db_subnet_group_name   = aws_db_subnet_group.main.name
  vpc_security_group_ids = [aws_security_group.db.id]

  # Availability: synchronous standby in a second AZ for automatic failover.
  # The db subnet group above spans two AZs, which this requires.
  multi_az = var.db_multi_az

  # Encryption at rest with the AWS-managed KMS key.
  storage_encrypted = true

  # Backups: automated daily snapshots retained for a week, taken in a window
  # outside peak hours, plus a final snapshot on destroy.
  backup_retention_period = var.db_backup_retention_period
  backup_window           = var.db_backup_window
  copy_tags_to_snapshot   = true

  skip_final_snapshot       = false
  final_snapshot_identifier = local.db_final_snapshot_identifier

  # Refuses DeleteDatabase calls that would discard the instance.
  deletion_protection = true

  # Enhanced Monitoring: OS-level metrics at 60s granularity in CloudWatch.
  # Gravitational (db.t4g) families do not support RDS Performance Insights,
  # so performance_insights is intentionally not enabled here. Move to a
  # db.t3.medium+ / db.r5.large+ class if Performance Insights is required.
  monitoring_interval = var.db_monitoring_interval
  monitoring_role_arn = var.db_monitoring_role_arn

  tags = { Name = "${var.project_name}-postgres" }

  lifecycle {
    # Teardown guard: while this is set, `terraform destroy` errors out instead
    # of deleting the database, no matter what the other attributes say.
    # It must be removed manually for teardown, ephemeral/throwaway
    # environments (CI, load-test sandboxes) and for any environment that is
    # genuinely disposable. Do not automate around it.
    prevent_destroy = true
  }
}
