resource "aws_db_subnet_group" "main" {
  name       = var.project_name
  subnet_ids = aws_subnet.private[*].id
}

resource "aws_db_instance" "postgres" {
  identifier             = var.project_name
  engine                 = "postgres"
  engine_version         = "15"
  instance_class         = "db.t4g.medium"
  allocated_storage      = 20
  db_name                = "appdb"
  username               = "appuser"
  password               = var.db_password
  db_subnet_group_name   = aws_db_subnet_group.main.name
  vpc_security_group_ids = [aws_security_group.db.id]
  skip_final_snapshot    = true
}
