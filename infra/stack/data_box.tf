locals {
  # The chip decides the machine image and the Docker Compose download
  data_arch = contains(data.aws_ec2_instance_type.data.supported_architectures, "arm64") ? "arm64" : "x86_64"
}

data "aws_ec2_instance_type" "data" {
  instance_type = var.data_instance_type
}

data "aws_ssm_parameter" "al2023_ami" {
  name = "/aws/service/ami-amazon-linux-latest/al2023-ami-kernel-default-${local.data_arch}"
}

data "aws_iam_policy_document" "ec2_assume" {
  statement {
    actions = ["sts:AssumeRole"]

    principals {
      type        = "Service"
      identifiers = ["ec2.amazonaws.com"]
    }
  }
}

data "aws_iam_policy_document" "data_box" {
  statement {
    sid       = "ReadClickhousePassword"
    actions   = ["ssm:GetParameter"]
    resources = [aws_ssm_parameter.clickhouse_password.arn]
  }
}

resource "aws_iam_role" "data_box" {
  name               = "${var.name}-data-box"
  assume_role_policy = data.aws_iam_policy_document.ec2_assume.json
}

# Session Manager access instead of SSH: no key pair, no port 22
resource "aws_iam_role_policy_attachment" "data_box_ssm" {
  role       = aws_iam_role.data_box.name
  policy_arn = "arn:aws:iam::aws:policy/AmazonSSMManagedInstanceCore"
}

resource "aws_iam_role_policy" "data_box" {
  name   = "read-clickhouse-password"
  role   = aws_iam_role.data_box.id
  policy = data.aws_iam_policy_document.data_box.json
}

resource "aws_iam_instance_profile" "data_box" {
  name = "${var.name}-data-box"
  role = aws_iam_role.data_box.name
}

resource "aws_ebs_volume" "data" {
  availability_zone = local.azs[0]
  size              = var.data_volume_size
  type              = "gp3"
  encrypted         = true
  snapshot_id       = var.data_volume_snapshot_id
  final_snapshot    = var.keep_final_snapshots

  tags = {
    Name = "${var.name}-data"
  }

  # Changing the snapshot later would replace live data with the old copy
  lifecycle {
    ignore_changes = [snapshot_id]
  }
}

resource "aws_instance" "data" {
  ami                    = data.aws_ssm_parameter.al2023_ami.value
  instance_type          = var.data_instance_type
  subnet_id              = aws_subnet.private[local.azs[0]].id
  vpc_security_group_ids = [aws_security_group.data.id]
  iam_instance_profile   = aws_iam_instance_profile.data_box.name

  metadata_options {
    http_tokens = "required"
  }

  root_block_device {
    volume_size = 20
    volume_type = "gp3"
    encrypted   = true
  }

  user_data = templatefile("${path.module}/files/data_box_user_data.sh.tftpl", {
    region                        = var.region
    compose_version               = "v2.29.7"
    compose_arch                  = local.data_arch == "arm64" ? "aarch64" : "x86_64"
    volume_id                     = replace(aws_ebs_volume.data.id, "-", "")
    clickhouse_password_parameter = aws_ssm_parameter.clickhouse_password.name
    clickhouse_server_config      = file("${path.module}/files/clickhouse-server-config.xml")
    clickhouse_profiles_config    = file("${path.module}/files/clickhouse-profiles-config.xml")
    compose_file = templatefile("${path.module}/files/docker-compose.yml.tftpl", {
      clickhouse_version = "26.5"
      clickhouse_user    = "ch_user"
      quickwit_version   = "v0.9.1"
    })
  })

  # A changed script (e.g. a new ClickHouse version) only runs on a new instance; data stays on the volume
  user_data_replace_on_change = true

  tags = {
    Name = "${var.name}-data"
  }

  # A newer Amazon Linux image must not replace the running box on its own
  lifecycle {
    ignore_changes = [ami]
  }
}

resource "aws_volume_attachment" "data" {
  device_name = "/dev/sdf"
  volume_id   = aws_ebs_volume.data.id
  instance_id = aws_instance.data.id

  # Clean shutdown before detaching, so the final snapshot holds ClickHouse data at rest
  stop_instance_before_detaching = true
}