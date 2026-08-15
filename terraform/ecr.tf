resource "aws_ecr_repository" "app" {
  name                 = var.project_name
  force_delete         = true
  image_tag_mutability = "MUTABLE"
}

resource "aws_ecr_repository" "consumer" {
  name                 = "${var.project_name}-consumer"
  force_delete         = true
  image_tag_mutability = "MUTABLE"
}
