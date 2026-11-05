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