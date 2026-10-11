resource "aws_ecs_cluster" "main" {
  name = var.project_name
}

resource "aws_iam_role" "ecs_execution" {
  name = "${var.project_name}-ecs-execution"

  assume_role_policy = jsonencode({
    Version = "2012-10-17"
    Statement = [{
      Action    = "sts:AssumeRole"
      Effect    = "Allow"
      Principal = { Service = "ecs-tasks.amazonaws.com" }
    }]
  })
}

resource "aws_iam_role_policy_attachment" "ecs_execution" {
  role       = aws_iam_role.ecs_execution.name
  policy_arn = "arn:aws:iam::aws:policy/service-role/AmazonECSTaskExecutionRolePolicy"
}

resource "aws_iam_role" "appautoscaling" {
  name = "${var.project_name}-appautoscaling"

  assume_role_policy = jsonencode({
    Version = "2012-10-17"
    Statement = [{
      Action    = "sts:AssumeRole"
      Effect    = "Allow"
      Principal = { Service = "application-autoscaling.amazonaws.com" }
    }]
  })
}

resource "aws_iam_role_policy_attachment" "appautoscaling" {
  role       = aws_iam_role.appautoscaling.name
  policy_arn = "arn:aws:iam::aws:policy/service-role/ApplicationAutoScalingServiceRolePolicy"
}

resource "aws_cloudwatch_log_group" "ecs" {
  name              = "/ecs/${var.project_name}"
  retention_in_days = var.log_retention_days
}

resource "aws_ecs_task_definition" "app" {
  family                   = var.project_name
  network_mode             = "awsvpc"
  requires_compatibilities = ["FARGATE"]
  cpu                      = "4096"
  memory                   = "8192"
  execution_role_arn       = aws_iam_role.ecs_execution.arn

  container_definitions = jsonencode([{
    name      = "app"
    image     = "${aws_ecr_repository.app.repository_url}:latest"
    essential = true

    portMappings = [{
      containerPort = 8000
      hostPort      = 8000
    }]

    environment = [
      { name = "DATABASE_HOST", value = aws_db_instance.postgres.address },
      { name = "DATABASE_PORT", value = "5432" },
      { name = "DATABASE_NAME", value = "appdb" },
      { name = "DATABASE_USER", value = "appuser" },
      { name = "DATABASE_PASSWORD", value = var.db_password },
      { name = "REDIS_URL", value = "redis://${aws_elasticache_cluster.redis.cache_nodes[0].address}:6379/0" },
      { name = "GUNICORN_WORKERS", value = "8" },
      { name = "GUNICORN_THREADS", value = "8" },
      { name = "KAFKA_BROKER", value = "PLAINTEXT://${aws_instance.kafka.private_ip}:9092" }
    ]

    logConfiguration = {
      logDriver = "awslogs"
      options = {
        "awslogs-group"         = aws_cloudwatch_log_group.ecs.name
        "awslogs-region"        = var.aws_region
        "awslogs-stream-prefix" = "app"
      }
    }
  }])
}

resource "aws_ecs_service" "app" {
  name            = var.project_name
  cluster         = aws_ecs_cluster.main.id
  task_definition = aws_ecs_task_definition.app.arn
  desired_count   = var.fargate_task_count

  # FARGATE on-demand forms the baseline so capacity is never interrupted;
  # FARGATE_SPOT adds cheap burst capacity on top of it.
  capacity_provider_strategy {
    capacity_provider = "FARGATE"
    weight            = var.base_capacity_weight
    base              = var.base_capacity_count
  }

  capacity_provider_strategy {
    capacity_provider = "FARGATE_SPOT"
    weight            = var.spot_capacity_weight
  }

  # Roll back a deployment automatically if tasks fail to reach steady state,
  # instead of leaving the service in a degraded state indefinitely.
  deployment_circuit_breaker {
    enable   = true
    rollback = true
  }

  deployment_maximum_percent         = var.deployment_maximum_percent
  deployment_minimum_healthy_percent = var.deployment_minimum_healthy_percent

  network_configuration {
    subnets          = aws_subnet.private[*].id
    security_groups  = [aws_security_group.ecs.id]
    assign_public_ip = false
  }

  load_balancer {
    target_group_arn = aws_lb_target_group.app.arn
    container_name   = "app"
    container_port   = 8000
  }

  lifecycle {
    # Scaling policies are managed outside of desired_count; ignore drift so
    # autoscaling can raise/lower the count freely.
    ignore_changes = [desired_count]
  }
}

resource "aws_appautoscaling_target" "app" {
  service_namespace  = "ecs"
  resource_id        = "service/${aws_ecs_cluster.main.name}/${aws_ecs_service.app.name}"
  scalable_dimension = "ecs:service:DesiredCount"
  min_capacity       = var.autoscaling_min_capacity
  max_capacity       = var.autoscaling_max_capacity
  role_arn           = aws_iam_role.appautoscaling.arn
}

resource "aws_appautoscaling_policy" "app_cpu" {
  name               = "${var.project_name}-app-cpu"
  policy_type        = "TargetTrackingScaling"
  service_namespace  = aws_appautoscaling_target.app.service_namespace
  resource_id        = aws_appautoscaling_target.app.resource_id
  scalable_dimension = aws_appautoscaling_target.app.scalable_dimension

  target_tracking_scaling_policy_configuration {
    target_value       = var.autoscaling_cpu_target
    scale_in_cooldown  = var.autoscaling_scale_in_cooldown
    scale_out_cooldown = var.autoscaling_scale_out_cooldown

    predefined_metric_specification {
      predefined_metric_type = "ECSServiceAverageCPUUtilization"
    }
  }
}

resource "aws_appautoscaling_policy" "app_memory" {
  name               = "${var.project_name}-app-memory"
  policy_type        = "TargetTrackingScaling"
  service_namespace  = aws_appautoscaling_target.app.service_namespace
  resource_id        = aws_appautoscaling_target.app.resource_id
  scalable_dimension = aws_appautoscaling_target.app.scalable_dimension

  target_tracking_scaling_policy_configuration {
    target_value       = var.autoscaling_memory_target
    scale_in_cooldown  = var.autoscaling_scale_in_cooldown
    scale_out_cooldown = var.autoscaling_scale_out_cooldown

    predefined_metric_specification {
      predefined_metric_type = "ECSServiceAverageMemoryUtilization"
    }
  }
}

# Request-rate based scaling complements the resource utilisation policies:
# the service can stay within CPU/memory targets while still being saturated
# with in-flight requests.
resource "aws_appautoscaling_policy" "app_alb_requests" {
  name               = "${var.project_name}-app-alb-requests"
  policy_type        = "TargetTrackingScaling"
  service_namespace  = aws_appautoscaling_target.app.service_namespace
  resource_id        = aws_appautoscaling_target.app.resource_id
  scalable_dimension = aws_appautoscaling_target.app.scalable_dimension

  target_tracking_scaling_policy_configuration {
    target_value       = var.autoscaling_alb_requests_target
    scale_in_cooldown  = var.autoscaling_scale_in_cooldown
    scale_out_cooldown = var.autoscaling_scale_out_cooldown

    predefined_metric_specification {
      predefined_metric_type = "ALBRequestCountPerTarget"
      resource_label         = "${aws_lb.main.arn_suffix}/${aws_lb_target_group.app.arn_suffix}"
    }
  }
}
