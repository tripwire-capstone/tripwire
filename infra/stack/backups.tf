# Scheduled snapshots of the data volume while the stack runs. RDS has its own automated
# backups; ClickHouse and Quickwit only live on this volume. keep_final_snapshots covers
# teardown; this covers a failed or wiped volume in between.
# The snapshots are made by AWS, not Terraform, so they outlive `terraform destroy`:
# delete them by hand (tag Backup = <name>-data) when they're no longer wanted.

data "aws_iam_policy_document" "dlm_assume" {
  statement {
    actions = ["sts:AssumeRole"]

    principals {
      type        = "Service"
      identifiers = ["dlm.amazonaws.com"]
    }
  }
}

resource "aws_iam_role" "dlm" {
  name               = "${var.name}-dlm"
  assume_role_policy = data.aws_iam_policy_document.dlm_assume.json
}

resource "aws_iam_role_policy_attachment" "dlm" {
  role       = aws_iam_role.dlm.name
  policy_arn = "arn:aws:iam::aws:policy/service-role/AWSDataLifecycleManagerServiceRole"
}

resource "aws_dlm_lifecycle_policy" "data" {
  description        = "Scheduled snapshots of the ${var.name} data volume"
  execution_role_arn = aws_iam_role.dlm.arn
  state              = "ENABLED"

  policy_details {
    resource_types = ["VOLUME"]
    target_tags    = { Backup = "${var.name}-data" }

    schedule {
      name      = "every-${var.data_snapshot_interval_hours}h"
      copy_tags = true

      create_rule {
        interval      = var.data_snapshot_interval_hours
        interval_unit = "HOURS"
      }

      retain_rule {
        count = var.data_snapshot_retention_count
      }
    }

    # One snapshot a day, kept a week, for problems that are noticed late
    schedule {
      name      = "daily"
      copy_tags = true

      tags_to_add = {
        Schedule = "daily"
      }

      create_rule {
        interval      = 24
        interval_unit = "HOURS"
        times         = ["08:00"]
      }

      retain_rule {
        count = var.data_snapshot_daily_retention_count
      }
    }
  }

  tags = {
    Name = "${var.name}-data"
  }
}
