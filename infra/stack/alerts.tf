# Email alerts. AWS sends a confirmation link to alert_email first; nothing arrives until it's clicked.
resource "aws_sns_topic" "alerts" {
  name = "${var.name}-alerts"
}

resource "aws_sns_topic_subscription" "alerts_email" {
  topic_arn = aws_sns_topic.alerts.arn
  protocol  = "email"
  endpoint  = var.alert_email
}

# Data Lifecycle Manager reports each policy's results to CloudWatch; any failed snapshot fires this
resource "aws_cloudwatch_metric_alarm" "data_snapshot_failed" {
  alarm_name        = "${var.name}-data-snapshot-failed"
  alarm_description = "A scheduled snapshot of the ${var.name} data volume failed"

  namespace   = "AWS/EBS"
  metric_name = "SnapshotsCreateFailed"
  dimensions = {
    DLMPolicyId = aws_dlm_lifecycle_policy.data.id
  }

  statistic           = "Sum"
  period              = 3600
  evaluation_periods  = 1
  comparison_operator = "GreaterThanThreshold"
  threshold           = 0

  # No data means no failures
  treat_missing_data = "notBreaching"

  alarm_actions = [aws_sns_topic.alerts.arn]
}

# Laminar is down: a target group has had no healthy targets for 3 minutes
resource "aws_cloudwatch_metric_alarm" "no_healthy_targets" {
  for_each = {
    frontend = aws_lb_target_group.frontend
    api      = aws_lb_target_group.api
    grpc     = aws_lb_target_group.grpc
  }

  alarm_name        = "${var.name}-${each.key}-no-healthy-targets"
  alarm_description = "No healthy ${each.key} targets behind the ${var.name} load balancer"

  namespace   = "AWS/ApplicationELB"
  metric_name = "HealthyHostCount"
  dimensions = {
    TargetGroup  = each.value.arn_suffix
    LoadBalancer = aws_lb.main.arn_suffix
  }

  statistic           = "Minimum"
  period              = 60
  evaluation_periods  = 3
  comparison_operator = "LessThanThreshold"
  threshold           = 1

  # No data means nothing is reporting healthy
  treat_missing_data = "breaching"

  alarm_actions = [aws_sns_topic.alerts.arn]
  ok_actions    = [aws_sns_topic.alerts.arn]
}

# The load balancer itself is answering with errors (502/503/504: targets down or timing out)
resource "aws_cloudwatch_metric_alarm" "alb_5xx" {
  alarm_name        = "${var.name}-alb-5xx"
  alarm_description = "The ${var.name} load balancer returned more than 10 errors of its own in 5 minutes"

  namespace   = "AWS/ApplicationELB"
  metric_name = "HTTPCode_ELB_5XX_Count"
  dimensions = {
    LoadBalancer = aws_lb.main.arn_suffix
  }

  statistic           = "Sum"
  period              = 300
  evaluation_periods  = 1
  comparison_operator = "GreaterThanThreshold"
  threshold           = 10

  # No data means no errors
  treat_missing_data = "notBreaching"

  alarm_actions = [aws_sns_topic.alerts.arn]
  ok_actions    = [aws_sns_topic.alerts.arn]
}

# The data box's hardware or operating system stopped responding to AWS's checks
resource "aws_cloudwatch_metric_alarm" "data_box_status" {
  alarm_name        = "${var.name}-data-box-status-check"
  alarm_description = "The ${var.name} data box failed its EC2 status checks"

  namespace   = "AWS/EC2"
  metric_name = "StatusCheckFailed"
  dimensions = {
    InstanceId = aws_instance.data.id
  }

  statistic           = "Maximum"
  period              = 60
  evaluation_periods  = 2
  comparison_operator = "GreaterThanThreshold"
  threshold           = 0

  treat_missing_data = "breaching"

  alarm_actions = [aws_sns_topic.alerts.arn]
  ok_actions    = [aws_sns_topic.alerts.arn]
}

# The snapshot policy stopped running at all, which the failure alarm can't see
resource "aws_cloudwatch_metric_alarm" "data_snapshot_missing" {
  alarm_name        = "${var.name}-data-snapshot-missing"
  alarm_description = "No snapshot of the ${var.name} data volume started in 2 hours"

  namespace   = "AWS/EBS"
  metric_name = "SnapshotsCreateStarted"
  dimensions = {
    DLMPolicyId = aws_dlm_lifecycle_policy.data.id
  }

  statistic           = "Sum"
  period              = 3600
  evaluation_periods  = 2
  comparison_operator = "LessThanThreshold"
  threshold           = 1

  # No data means no snapshot started
  treat_missing_data = "breaching"

  alarm_actions = [aws_sns_topic.alerts.arn]
}