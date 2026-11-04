locals {
  # The chip decides the machine image and the Docker Compose download
  data_arch    = contains(data.aws_ec2_instance_type.data.supported_architectures, "arm64") ? "arm64" : "x86_64"
  compose_arch = local.data_arch == "arm64" ? "aarch64" : "x86_64"

  # Tag and digest: the tag says which release, the digest guarantees the exact image even if
  # the tag is re-published. Both digests are multi-arch indexes (amd64 and arm64).
  data_box_images = {
    # 26.10.3.7 has the fix for CVE-2026-116366 (unauthenticated RCE); Laminar's own 26.5 does not
    clickhouse = "clickhouse/clickhouse-server:26.10.3.7@sha256:739e099abd1157390960a59e4532d21781d8fde57e6e4151cb920ea02c30b1ce"
    quickwit   = "quickwit/quickwit:v0.9.1@sha256:3e0f079eb57dd5563f36a457e9a7a2963ff882316d6c77e3180ac3c59767a68f"
  }

  # From checksums.txt of the Docker Compose v2.29.7 release
  compose_sha256 = {
    aarch64 = "6e9fbd5daa20dca5d7d89145081ae8155d68ef2928b497d9f85b54fe0f9dbb2c"
    x86_64  = "383ce6698cd5d5bbf958d2c8489ed75094e34a77d340404d9f32c4ae9e12baf0"
  }

  # Laminar's ClickHouse config files, unchanged from v0.3.2 through v0.3.3
  laminar_config_sha256 = {
    "clickhouse-server-config.xml"   = "ab8ae9e3707323e846b75a8b3881ea8f37850ddba4c6675a6cbdf05c0fc72969"
    "clickhouse-profiles-config.xml" = "7cac53a693010ff5cf6f3258952f39afba7857f1858eec83684ca703a8bfe006"
  }
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

    # The scheduled snapshots in backups.tf find the volume by this tag
    Backup = "${var.name}-data"
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
  private_ip             = cidrhost(local.private_subnets[local.azs[0]], 10)
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
    compose_arch                  = local.compose_arch
    compose_sha256                = local.compose_sha256[local.compose_arch]
    volume_id                     = replace(aws_ebs_volume.data.id, "-", "")
    clickhouse_password_parameter = aws_ssm_parameter.clickhouse_password.name
    clickhouse_server_config      = file("${path.module}/files/clickhouse-server-config.xml")
    clickhouse_profiles_config    = file("${path.module}/files/clickhouse-profiles-config.xml")
    clickhouse_compat_config      = file("${path.module}/files/clickhouse-compat-config.xml")
    clickhouse_merge_tree_config  = file("${path.module}/files/clickhouse-merge-tree-config.xml")
    compose_file = templatefile("${path.module}/files/docker-compose.yml.tftpl", {
      clickhouse_image = local.data_box_images.clickhouse
      clickhouse_user  = "ch_user"
      quickwit_image   = local.data_box_images.quickwit
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

    # Laminar's ClickHouse config files must be the released ones, not edited copies
    precondition {
      condition = alltrue([
        for file_name, expected_sha256 in local.laminar_config_sha256 :
        filesha256("${path.module}/files/${file_name}") == expected_sha256
      ])
      error_message = "files/clickhouse-server-config.xml or files/clickhouse-profiles-config.xml differs from Laminar's released copy."
    }
  }

  # The first-boot script downloads Docker Compose and the images over the internet, which the
  # private subnet only reaches through the NAT. Nothing else in this resource refers to the NAT,
  # so without this the box can boot while the NAT (about 2 minutes to create) isn't routing yet.
  depends_on = [
    aws_route.private_nat,
    aws_route_table_association.private,
  ]
}

resource "aws_volume_attachment" "data" {
  device_name = "/dev/sdf"
  volume_id   = aws_ebs_volume.data.id
  instance_id = aws_instance.data.id

  # Clean shutdown before detaching, so the final snapshot holds ClickHouse data at rest
  stop_instance_before_detaching = true
}