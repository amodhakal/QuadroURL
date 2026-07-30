data "aws_ami" "al2023" {
  most_recent = true
  owners      = ["amazon"]

  filter {
    name   = "name"
    values = ["al2023-ami-2023.*-x86_64"]
  }
}

resource "aws_instance" "loadtester" {
  ami                    = data.aws_ami.al2023.id
  instance_type          = "c6i.xlarge"
  subnet_id              = aws_subnet.public[0].id
  vpc_security_group_ids = [aws_security_group.loadtester.id]

  instance_market_options {
    market_type = "spot"
  }

  user_data = <<-EOF
              #!/bin/bash
              dnf update -y
              dnf install -y python3.11 python3.11-pip git
              pip3.11 install locust
              EOF

  tags = { Name = "${var.project_name}-loadtester" }
}
