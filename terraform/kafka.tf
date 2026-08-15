resource "aws_instance" "kafka" {
  ami                    = data.aws_ami.al2023.id
  instance_type          = "t3.small"
  subnet_id              = aws_subnet.public[1].id
  vpc_security_group_ids = [aws_security_group.kafka.id]

  instance_market_options {
    market_type = "spot"
  }

  user_data = <<-EOF
              #!/bin/bash
              set -e

              dnf update -y
              dnf install -y docker
              systemctl enable --now docker

              TOKEN=$(curl -s -X PUT "http://169.254.169.254/latest/api/token" -H "X-aws-ec2-metadata-token-ttl-seconds: 60")
              PRIVATE_IP=$(curl -s -H "X-aws-ec2-metadata-token: $TOKEN" http://169.254.169.254/latest/meta-data/local-ipv4)

              docker run -d --name kafka --restart unless-stopped \
                -p 9092:9092 \
                -e KAFKA_NODE_ID=1 \
                -e KAFKA_PROCESS_ROLES=broker,controller \
                -e KAFKA_CONTROLLER_QUORUM_VOTERS=1@127.0.0.1:9093 \
                -e KAFKA_LISTENERS=PLAINTEXT://:9092,CONTROLLER://:9093 \
                -e KAFKA_ADVERTISED_LISTENERS=PLAINTEXT://$PRIVATE_IP:9092 \
                -e KAFKA_CONTROLLER_LISTENER_NAMES=CONTROLLER \
                -e KAFKA_LISTENER_SECURITY_PROTOCOL_MAP=CONTROLLER:PLAINTEXT,PLAINTEXT:PLAINTEXT \
                -e KAFKA_AUTO_CREATE_TOPICS_ENABLE=true \
                -e KAFKA_NUM_PARTITIONS=3 \
                apache/kafka:3.8.0

              for i in $(seq 1 60); do
                if docker exec kafka /opt/kafka/bin/kafka-topics.sh --bootstrap-server localhost:9092 --list >/dev/null 2>&1; then
                  break
                fi
                sleep 5
              done

              docker exec kafka /opt/kafka/bin/kafka-topics.sh --bootstrap-server localhost:9092 --create --if-not-exists --topic request-logs --partitions 3 --replication-factor 1 || true
              docker exec kafka /opt/kafka/bin/kafka-topics.sh --bootstrap-server localhost:9092 --create --if-not-exists --topic url-events --partitions 3 --replication-factor 1 || true
              docker exec kafka /opt/kafka/bin/kafka-topics.sh --bootstrap-server localhost:9092 --create --if-not-exists --topic url-creates --partitions 3 --replication-factor 1 || true
              EOF

  tags = { Name = "${var.project_name}-kafka" }
}
