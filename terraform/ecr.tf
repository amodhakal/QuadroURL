resource "aws_ecr_repository" "app" {
  name                 = var.project_name
  force_destroy        = true
  image_tag_mutability = "MUTABLE"
}
