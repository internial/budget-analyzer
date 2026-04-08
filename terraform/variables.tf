# Settings you can change without editing main.tf (region, budget, API limits, etc.).

variable "aws_region" {
  description = "Primary AWS region for the workload (S3, Lambda, API Gateway, etc.)."
  type        = string
  default     = "us-east-1"
}

variable "replica_region" {
  description = "Destination region for S3 cross-region replication (disaster recovery)."
  type        = string
  default     = "us-west-2"
}

variable "project_name" {
  description = "Short project identifier used in resource names."
  type        = string
  default     = "budget-analyzer"
}

variable "s3_bucket_name" {
  description = "Primary uploads bucket name (globally unique)."
  type        = string
  default     = "budget-analyzer-uploads"
}

variable "bedrock_model_id" {
  description = "Bedrock foundation model ID for analysis (must be enabled in the account/region)."
  type        = string
  default     = "anthropic.claude-3-haiku-20240307-v1:0"
}

variable "budget_limit_usd" {
  description = "Monthly AWS cost budget limit in USD."
  type        = string
  default     = "5"
}

variable "budget_start_date" {
  description = "Budget period start (UTC), format YYYY-MM-DD_HH:MM. Update when creating a new budget in a new year if needed."
  type        = string
  default     = "2026-01-01_00:00"
}

variable "budget_notification_emails" {
  description = "Email addresses to subscribe to the budget alert SNS topic (optional; requires email confirmation)."
  type        = list(string)
  default     = []
}

variable "api_throttle_requests_per_minute" {
  description = "API Gateway steady-state limit expressed as requests per minute (converted to RPS on the stage)."
  type        = number
  default     = 10
}

variable "api_throttle_burst_limit" {
  description = "API Gateway burst limit for the stage."
  type        = number
  default     = 20
}

variable "lambda_log_retention_days" {
  description = "CloudWatch Logs retention for Lambda log groups."
  type        = number
  default     = 30
}

variable "cloudtrail_log_retention_days" {
  description = "S3 lifecycle expiration (days) for CloudTrail log objects in the dedicated audit bucket."
  type        = number
  default     = 365
}
