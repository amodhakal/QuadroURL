locals {
  consumer_types = {
    request-log = "logs"
    url-event   = "events"
    url-create  = "creates"
  }
}

resource "aws_ecs_task_definition" "consumer" {
  for_each                 = local.consumer_types
  family                   = "${var.project_name}-${each.key}"
  network_mode             = "awsvpc"
  requires_compatibilities = ["FARGATE"]
  cpu                      = "512"
  memory                   = "1024"
  execution_role_arn       = aws_iam_role.ecs_execution.arn

  container_definitions = jsonencode([{
    name      = "consumer"
    image     = "${aws_ecr_repository.consumer.repository_url}:latest"
    essential = true

    environment = [
      { name = "CONSUMER_TYPE", value = each.value },
      { name = "KAFKA_BROKER", value = "PLAINTEXT://${aws_instance.kafka.private_ip}:9092" },
      { name = "DATABASE_HOST", value = aws_db_instance.postgres.address },
      { name = "DATABASE_PORT", value = "5432" },
      { name = "DATABASE_NAME", value = "appdb" },
      { name = "DATABASE_USER", value = "appuser" },
      { name = "DATABASE_PASSWORD", value = var.db_password },
      { name = "REDIS_URL", value = "redis://${aws_elasticache_cluster.redis.cache_nodes[0].address}:6379/0" }
    ]

    logConfiguration = {
      logDriver = "awslogs"
      options = {
        "awslogs-group"         = aws_cloudwatch_log_group.ecs.name
        "awslogs-region"        = var.aws_region
        "awslogs-stream-prefix" = each.key
      }
    }
  }])
}

resource "aws_ecs_service" "consumer" {
  for_each        = local.consumer_types
  name            = each.key
  cluster         = aws_ecs_cluster.main.id
  task_definition = aws_ecs_task_definition.consumer[each.key].arn
  desired_count   = 3

  capacity_provider_strategy {
    capacity_provider = "FARGATE_SPOT"
    weight            = 1
  }

  network_configuration {
    subnets          = aws_subnet.public[*].id
    security_groups  = [aws_security_group.ecs.id]
    assign_public_ip = true
  }
}
