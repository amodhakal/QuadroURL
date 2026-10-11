resource "aws_instance" "kafka" {
  ami                    = data.aws_ami.al2023.id
  instance_type          = "t3.small"
  subnet_id              = aws_subnet.public[1].id
  vpc_security_group_ids = [aws_security_group.kafka.id]

  instance_market_options {
    market_type = "spot"
  }

  # Kafka's data directory lives here instead of the container's writable
  # layer. The instance is spot: when it is reclaimed the broker comes back on
  # a fresh instance (possibly a different AZ), and a root-device-only layout
  # takes every unconsumed message with it. A separate gp3 volume detaches and
  # reattaches across that swap instead.
  ebs_block_device {
    device_name = "/dev/xvdf"
    volume_size = var.kafka_data_volume_size_gb
    volume_type = "gp3"

    # Operational tradeoff, deliberate: the volume outlives the instance, so
    # `terraform destroy` (and any manual instance replacement) no longer
    # reclaims the disk. That is the point — a replacement must be able to
    # pick the log back up — but it also means the volume accrues cost once
    # this stack is torn down and it will block a clean destroy until it is
    # released by hand:
    #
    #   aws ec2 delete-volume --region us-east-1 --volume-id <kafka_data_volume_id>
    #   terraform state rm 'aws_instance.kafka'   # after detaching
    #
    # `terraform output kafka_data_volume_id` prints the id. Keep in mind a
    # reclaimed spot instance can outlive the stack; delete the volume when
    # the load-test environment is retired, not when an instance cycles.
    delete_on_termination = false
  }

  user_data = <<-EOF
              #!/bin/bash
              set -e

              KAFKA_DATA_MOUNT=/var/lib/kafka

              dnf update -y
              dnf install -y docker xfsprogs
              systemctl enable --now docker

              # Locate the attached data volume. /dev/xvdf is the name the
              # block device mapping asks for, but on Nitro instances AL2023
              # exposes EBS volumes as /dev/nvme*n1 and may not create the
              # compatibility node, so fall back to "the only block device
              # that is not the root disk".
              KAFKA_DATA_DEVICE=/dev/xvdf
              if [ ! -b "$KAFKA_DATA_DEVICE" ]; then
                ROOT_PART=$(findmnt -no SOURCE /)
                ROOT_DISK=$(lsblk -no PKNAME "$ROOT_PART" | head -n1)
                KAFKA_DATA_DEVICE=$(lsblk -nrpo NAME,TYPE | awk -v root="/dev/$ROOT_DISK" '$2 == "disk" && $1 != root { print $1; exit }')
              fi

              if [ -z "$KAFKA_DATA_DEVICE" ]; then
                echo "ERROR: no Kafka data volume found; refusing to start the broker on the root disk" >&2
                exit 1
              fi

              # Only format when the device is blank. Re-running this on an
              # existing volume must never truncate the log.
              if ! blkid "$KAFKA_DATA_DEVICE" >/dev/null 2>&1; then
                mkfs.xfs -f "$KAFKA_DATA_DEVICE"
              fi

              mkdir -p "$KAFKA_DATA_MOUNT"
              VOLUME_UUID=$(blkid -s UUID -o value "$KAFKA_DATA_DEVICE")
              # nofail so a missing/detached volume does not wedge boot; the
              # data check above already refuses to start Kafka without it.
              grep -qs "$KAFKA_DATA_MOUNT" /etc/fstab || \
                echo "UUID=$VOLUME_UUID $KAFKA_DATA_MOUNT xfs defaults,nofail 0 2" >> /etc/fstab

              if ! mountpoint -q "$KAFKA_DATA_MOUNT"; then
                mount "$KAFKA_DATA_MOUNT"
              fi

              # apache/kafka runs as uid/gid 1000 (appuser).
              chown 1000:1000 "$KAFKA_DATA_MOUNT"
              chmod 0755 "$KAFKA_DATA_MOUNT"

              TOKEN=$(curl -s -X PUT "http://169.254.169.254/latest/api/token" -H "X-aws-ec2-metadata-token-ttl-seconds: 60")
              PRIVATE_IP=$(curl -s -H "X-aws-ec2-metadata-token: $TOKEN" http://169.254.169.254/latest/meta-data/local-ipv4)

              docker run -d --name kafka --restart unless-stopped \
                -p 9092:9092 \
                -v "$KAFKA_DATA_MOUNT:/var/lib/kafka:z" \
                -e KAFKA_LOG_DIRS=/var/lib/kafka \
                -e KAFKA_NODE_ID=1 \
                -e KAFKA_PROCESS_ROLES=broker,controller \
                -e KAFKA_CONTROLLER_QUORUM_VOTERS=1@127.0.0.1:9093 \
                -e KAFKA_LISTENERS=PLAINTEXT://:9092,CONTROLLER://:9093 \
                -e KAFKA_ADVERTISED_LISTENERS=PLAINTEXT://$PRIVATE_IP:9092 \
                -e KAFKA_CONTROLLER_LISTENER_NAMES=CONTROLLER \
                -e KAFKA_LISTENER_SECURITY_PROTOCOL_MAP=CONTROLLER:PLAINTEXT,PLAINTEXT:PLAINTEXT \
                -e KAFKA_AUTO_CREATE_TOPICS_ENABLE=true \
                -e KAFKA_NUM_PARTITIONS=3 \
                -e KAFKA_LOG_RETENTION_HOURS=24 \
                -e KAFKA_LOG_SEGMENT_BYTES=104857600 \
                -e KAFKA_MIN_INSYNC_REPLICAS=1 \
                -e KAFKA_DEFAULT_REPLICATION_FACTOR=1 \
                -e KAFKA_OFFSETS_TOPIC_REPLICATION_FACTOR=1 \
                -e KAFKA_TRANSACTION_STATE_LOG_REPLICATION_FACTOR=1 \
                -e KAFKA_TRANSACTION_STATE_LOG_MIN_ISR=1 \
                apache/kafka:3.8.0

              # Single-broker caveats, stated once so nobody reads the topic
              # creation below as a durability guarantee:
              #   * replication-factor 1 (above, and on every topic below) means
              #     there is no broker-level redundancy. The volume protects
              #     against instance loss, not against loss or corruption of the
              #     disk itself.
              #   * KAFKA_OFFSETS_TOPIC_REPLICATION_FACTOR /
              #     KAFKA_TRANSACTION_STATE_LOG_* must stay at 1: their Kafka
              #     defaults are 3 and a one-broker cluster cannot elect a
              #     leader for those internal topics without them.
              #   * 24h retention (docker-compose uses 1h for local dev) gives
              #     consumers a full day of catch-up after a reclamation. Size
              #     kafka_data_volume_size_gb against that window, or the disk
              #     will fill and the broker will stall.
              #   * Anything that still matters for longer than the retention
              #     window needs durable storage (MSK, or a database), not a
              #     spot broker.

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
