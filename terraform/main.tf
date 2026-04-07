# Infrastructure for the Fraud, Waste, and Abuse Budget Analyzer.
#
# Request path: POST /upload -> upload_handler -> S3 uploads/
# S3 event -> document_processor (Textract/CSV) -> extracted/*.json -> ai_analyzer (Bedrock + DynamoDB).
# GET /results reads DynamoDB.
#
# Also includes: S3 replication, CloudTrail, CloudWatch alarms, SNS + monthly budget.

############################
# Data & locals
############################

data "aws_caller_identity" "current" {}
data "aws_partition" "current" {}
data "aws_region" "current" {}

locals {
  common_tags = {
    Project     = var.project_name
    Environment = "production"
    System      = "fraud-waste-abuse-budget-analyzer"
  }

  account_id = data.aws_caller_identity.current.account_id
  region     = data.aws_region.current.name
  partition  = data.aws_partition.current.partition

  api_rate_limit_rps = var.api_throttle_requests_per_minute / 60.0

  dynamodb_table_name = "budget_analyzer_results"

  bedrock_model_arn = "arn:${local.partition}:bedrock:${local.region}::foundation-model/${var.bedrock_model_id}"
}

############################
# SNS (budget + operational alerts)
############################

resource "aws_sns_topic" "budget_alerts" {
  name = "${var.project_name}-budget-alerts"
}

resource "aws_sns_topic" "textract_notifications" {
  name = "${var.project_name}-textract-notifications"
}

resource "aws_sns_topic_policy" "textract_notifications" {
  arn = aws_sns_topic.textract_notifications.arn

  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      {
        Effect = "Allow"
        Principal = {
          Service = "textract.amazonaws.com"
        }
        Action   = "sns:Publish"
        Resource = aws_sns_topic.textract_notifications.arn
      }
    ]
  })
}

resource "aws_sns_topic_policy" "budget_alerts" {
  arn = aws_sns_topic.budget_alerts.arn

  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      {
        Sid    = "AllowBudgetsPublish"
        Effect = "Allow"
        Principal = {
          Service = "budgets.amazonaws.com"
        }
        Action   = "sns:Publish"
        Resource = aws_sns_topic.budget_alerts.arn
      }
    ]
  })
}

resource "aws_sns_topic_subscription" "budget_email" {
  for_each  = toset(var.budget_notification_emails)
  topic_arn = aws_sns_topic.budget_alerts.arn
  protocol  = "email"
  endpoint  = each.value
}

############################
# S3 — primary uploads bucket
############################

resource "aws_s3_bucket" "uploads" {
  bucket = var.s3_bucket_name
}

resource "aws_s3_bucket_versioning" "uploads" {
  bucket = aws_s3_bucket.uploads.id
  versioning_configuration {
    status = "Enabled"
  }
}

resource "aws_s3_bucket_public_access_block" "uploads" {
  bucket = aws_s3_bucket.uploads.id

  block_public_acls       = true
  block_public_policy     = true
  ignore_public_acls      = true
  restrict_public_buckets = true
}

resource "aws_s3_bucket_ownership_controls" "uploads" {
  bucket = aws_s3_bucket.uploads.id
  rule {
    object_ownership = "BucketOwnerEnforced"
  }
}



resource "aws_s3_bucket_server_side_encryption_configuration" "uploads" {
  bucket = aws_s3_bucket.uploads.id
  rule {
    apply_server_side_encryption_by_default {
      sse_algorithm = "AES256"
    }
  }
}

resource "aws_s3_bucket_lifecycle_configuration" "uploads" {
  bucket = aws_s3_bucket.uploads.id

  rule {
    id     = "uploads-tiering"
    status = "Enabled"
    filter {
      prefix = "uploads/"
    }
    transition {
      days          = 30
      storage_class = "GLACIER_IR"
    }
    transition {
      days          = 120
      storage_class = "DEEP_ARCHIVE"
    }
  }

  rule {
    id     = "extracted-expiration"
    status = "Enabled"
    filter {
      prefix = "extracted/"
    }
    expiration {
      days = 30
    }
  }
}

############################
# S3 — replica bucket (CRR)
############################

resource "aws_s3_bucket" "uploads_replica" {
  provider = aws.replica
  bucket   = "${var.s3_bucket_name}-replica-${local.account_id}"
  acl      = "private"

  versioning {
    enabled = true
  }

  tags = merge(local.common_tags, {
    Name = "budget-analyzer-uploads-replica"
  })
}

resource "aws_s3_bucket_public_access_block" "uploads_replica" {
  provider = aws.replica
  bucket   = aws_s3_bucket.uploads_replica.id

  block_public_acls       = true
  block_public_policy     = true
  ignore_public_acls      = true
  restrict_public_buckets = true
}

resource "aws_s3_bucket_ownership_controls" "uploads_replica" {
  provider = aws.replica
  bucket   = aws_s3_bucket.uploads_replica.id
  rule {
    object_ownership = "BucketOwnerEnforced"
  }
}



resource "aws_s3_bucket_server_side_encryption_configuration" "uploads_replica" {
  provider = aws.replica
  bucket   = aws_s3_bucket.uploads_replica.id
  rule {
    apply_server_side_encryption_by_default {
      sse_algorithm = "AES256"
    }
  }
}

resource "aws_iam_role" "s3_replication" {
  name = "${var.project_name}-s3-replication"

  assume_role_policy = jsonencode({
    Version = "2012-10-17"
    Statement = [{
      Effect = "Allow"
      Principal = {
        Service = "s3.amazonaws.com"
      }
      Action = "sts:AssumeRole"
    }]
  })
}

resource "aws_iam_role_policy" "s3_replication" {
  name = "${var.project_name}-s3-replication"
  role = aws_iam_role.s3_replication.id

  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      {
        Effect = "Allow"
        Action = [
          "s3:GetReplicationConfiguration",
          "s3:ListBucket"
        ]
        Resource = aws_s3_bucket.uploads.arn
      },
      {
        Effect = "Allow"
        Action = [
          "s3:GetObjectVersionForReplication",
          "s3:GetObjectVersionAcl",
          "s3:GetObjectVersionTagging",
          "s3:GetObjectRetention",
          "s3:GetObjectVersion"
        ]
        Resource = "${aws_s3_bucket.uploads.arn}/*"
      },
      {
        Effect = "Allow"
        Action = [
          "s3:ReplicateObject",
          "s3:ReplicateDelete",
          "s3:ReplicateTags",
          "s3:ObjectOwnerOverrideToBucketOwner"
        ]
        Resource = "${aws_s3_bucket.uploads_replica.arn}/*"
      }
    ]
  })
}

resource "aws_s3_bucket_replication_configuration" "uploads" {
  role   = aws_iam_role.s3_replication.arn
  bucket = aws_s3_bucket.uploads.id

  rule {
    id = "replicate-all-objects"
    priority = 1
    status = "Enabled"

    delete_marker_replication {
      status = "Disabled"
    }

    filter {
      prefix = ""
    }

    destination {
      bucket        = aws_s3_bucket.uploads_replica.arn
      storage_class = "STANDARD"
    }
  }
}

############################
# S3 — CloudTrail audit bucket (dedicated)
############################

resource "aws_s3_bucket" "cloudtrail" {
  bucket = "${var.project_name}-cloudtrail-${local.account_id}"
}

resource "aws_s3_bucket_public_access_block" "cloudtrail" {
  bucket = aws_s3_bucket.cloudtrail.id

  block_public_acls       = true
  block_public_policy     = true
  ignore_public_acls      = true
  restrict_public_buckets = true
}

resource "aws_s3_bucket_server_side_encryption_configuration" "cloudtrail" {
  bucket = aws_s3_bucket.cloudtrail.id
  rule {
    apply_server_side_encryption_by_default {
      sse_algorithm = "AES256"
    }
  }
}

resource "aws_s3_bucket_lifecycle_configuration" "cloudtrail" {
  bucket = aws_s3_bucket.cloudtrail.id

  rule {
    id     = "expire-old-trail-logs"
    status = "Enabled"
    filter {}
    expiration {
      days = var.cloudtrail_log_retention_days
    }
  }
}

data "aws_iam_policy_document" "cloudtrail_bucket_policy" {
  statement {
    sid    = "AWSCloudTrailAclCheck"
    effect = "Allow"
    principals {
      type        = "Service"
      identifiers = ["cloudtrail.amazonaws.com"]
    }
    actions   = ["s3:GetBucketAcl"]
    resources = [aws_s3_bucket.cloudtrail.arn]
    condition {
      test     = "ArnLike"
      variable = "aws:SourceArn"
      values   = ["arn:${local.partition}:cloudtrail:${local.region}:${local.account_id}:trail/*"]
    }
  }

  statement {
    sid    = "AWSCloudTrailWrite"
    effect = "Allow"
    principals {
      type        = "Service"
      identifiers = ["cloudtrail.amazonaws.com"]
    }
    actions   = ["s3:PutObject"]
    resources = ["${aws_s3_bucket.cloudtrail.arn}/AWSLogs/${local.account_id}/*"]
    condition {
      test     = "StringEquals"
      variable = "s3:x-amz-acl"
      values   = ["bucket-owner-full-control"]
    }
    condition {
      test     = "ArnLike"
      variable = "aws:SourceArn"
      values   = ["arn:${local.partition}:cloudtrail:${local.region}:${local.account_id}:trail/*"]
    }
  }
}

resource "aws_s3_bucket_policy" "cloudtrail" {
  bucket = aws_s3_bucket.cloudtrail.id
  policy = data.aws_iam_policy_document.cloudtrail_bucket_policy.json

  depends_on = [aws_s3_bucket_public_access_block.cloudtrail]
}

resource "aws_cloudtrail" "audit" {
  name                          = "${var.project_name}-audit-trail"
  s3_bucket_name                = aws_s3_bucket.cloudtrail.id
  include_global_service_events = true
  is_multi_region_trail         = true
  enable_logging                = true

  depends_on = [aws_s3_bucket_policy.cloudtrail]
}

############################
# DynamoDB
############################

resource "aws_dynamodb_table" "results" {
  name         = local.dynamodb_table_name
  billing_mode = "PAY_PER_REQUEST"
  hash_key     = "document_id"

  attribute {
    name = "document_id"
    type = "S"
  }

  attribute {
    name = "file_hash"
    type = "S"
  }

  global_secondary_index {
    name            = "file_hash-index"
    hash_key        = "file_hash"
    projection_type = "ALL"
  }

  point_in_time_recovery {
    enabled = true
  }

  server_side_encryption {
    enabled = true
  }
}

resource "aws_sqs_queue" "budget_analyzer_dlq" {
  name                      = "budget-analyzer-dlq"
  message_retention_seconds = 1209600 # 14 days
}

resource "aws_cloudwatch_metric_alarm" "budget_analyzer_dlq_alarm" {
  alarm_name          = "budget-analyzer-dlq-messages-visible-alarm"
  comparison_operator = "GreaterThanThreshold"
  evaluation_periods  = 1
  metric_name         = "ApproximateNumberOfMessagesVisible"
  namespace           = "AWS/SQS"
  period              = 300 # 5 minutes
  statistic           = "Sum"
  threshold           = 0
  alarm_description   = "Alarm when budget-analyzer-dlq has messages"
  dimensions = {
    QueueName = aws_sqs_queue.budget_analyzer_dlq.name
  }
  actions_enabled = true
  alarm_actions   = [aws_sns_topic.budget_alerts.arn]
  ok_actions      = [aws_sns_topic.budget_alerts.arn]
}


############################
# IAM — Lambda roles (least privilege)
############################

resource "aws_iam_role" "textract_service_role" {
  name = "${var.project_name}-textract-service-role"

  assume_role_policy = jsonencode({
    Version = "2012-10-17"
    Statement = [{
      Effect = "Allow"
      Principal = {
        Service = "textract.amazonaws.com"
      }
      Action = "sts:AssumeRole"
    }]
  })
}

resource "aws_iam_role_policy" "textract_service_role" {
  name = "${var.project_name}-textract-service-role-policy"
  role = aws_iam_role.textract_service_role.id

  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      {
        Effect   = "Allow"
        Action   = "sns:Publish"
        Resource = aws_sns_topic.textract_notifications.arn
      }
    ]
  })
}

resource "aws_iam_role" "upload_handler" {
  name = "${var.project_name}-upload-handler"

  assume_role_policy = jsonencode({
    Version = "2012-10-17"
    Statement = [{
      Effect = "Allow"
      Principal = {
        Service = "lambda.amazonaws.com"
      }
      Action = "sts:AssumeRole"
    }]
  })
}

resource "aws_iam_role_policy" "upload_handler" {
  name = "${var.project_name}-upload-handler"
  role = aws_iam_role.upload_handler.id

  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      {
        Effect = "Allow"
        Action = [
          "logs:CreateLogGroup",
          "logs:CreateLogStream",
          "logs:PutLogEvents",
          "xray:PutTraceSegments",
          "xray:PutTelemetryRecords"
        ]
        Resource = "*"
      },
      {
        Effect = "Allow"
        Action = [
          "s3:PutObject",
          "s3:AbortMultipartUpload",
          "s3:ListBucketMultipartUploads"
        ]
        Resource = [
          aws_s3_bucket.uploads.arn,
          "${aws_s3_bucket.uploads.arn}/uploads/*"
        ]
      },
      {
        Effect   = "Allow"
        Action   = ["sqs:SendMessage"]
        Resource = aws_sqs_queue.budget_analyzer_dlq.arn
      }
    ]
  })
}

resource "aws_iam_role" "document_processor" {
  name = "${var.project_name}-document-processor"

  assume_role_policy = jsonencode({
    Version = "2012-10-17"
    Statement = [{
      Effect = "Allow"
      Principal = {
        Service = "lambda.amazonaws.com"
      }
      Action = "sts:AssumeRole"
    }]
  })
}

resource "aws_iam_role_policy" "document_processor" {
  name = "${var.project_name}-document-processor"
  role = aws_iam_role.document_processor.id

  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      {
        Effect = "Allow"
        Action = [
          "logs:CreateLogStream",
          "logs:PutLogEvents",
          "xray:PutTraceSegments",
          "xray:PutTelemetryRecords"
        ]
        Resource = "${aws_cloudwatch_log_group.document_processor.arn}:*"
      },
      {
        Effect = "Allow"
        Action = [
          "s3:GetObject",
          "s3:GetObjectVersion",
          "s3:PutObject"
        ]
        Resource = "${aws_s3_bucket.uploads.arn}/*"
      },
      {
        Effect = "Allow"
        Action = [
          "s3:ListBucket"
        ]
        Resource = aws_s3_bucket.uploads.arn
      },
      {
        Effect = "Allow"
        Action = [
          "textract:DetectDocumentText",
          "textract:AnalyzeDocument",
          "textract:StartDocumentAnalysis",
          "textract:GetDocumentAnalysis"
        ]
        Resource = "*"
      },
      {
        Effect   = "Allow"
        Action   = "iam:PassRole"
        Resource = aws_iam_role.textract_service_role.arn
      },
      {
        Effect   = "Allow"
        Action   = ["lambda:InvokeFunction"]
        Resource = aws_lambda_function.ai_analyzer.arn
      },
      {
        Effect   = "Allow"
        Action   = ["sqs:SendMessage"]
        Resource = aws_sqs_queue.budget_analyzer_dlq.arn
      }
    ]
  })
}

resource "aws_iam_role" "ai_analyzer" {
  name = "${var.project_name}-ai-analyzer"

  assume_role_policy = jsonencode({
    Version = "2012-10-17"
    Statement = [{
      Effect = "Allow"
      Principal = {
        Service = "lambda.amazonaws.com"
      }
      Action = "sts:AssumeRole"
    }]
  })
}

resource "aws_iam_role" "textract_callback_handler" {
  name = "${var.project_name}-textract-callback-handler"

  assume_role_policy = jsonencode({
    Version = "2012-10-17"
    Statement = [{
      Effect = "Allow"
      Principal = {
        Service = "lambda.amazonaws.com"
      }
      Action = "sts:AssumeRole"
    }]
  })
}

resource "aws_iam_role_policy" "ai_analyzer" {
  name = "${var.project_name}-ai-analyzer"
  role = aws_iam_role.ai_analyzer.id

  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      {
        Effect = "Allow"
        Action = [
          "logs:CreateLogStream",
          "logs:PutLogEvents",
          "xray:PutTraceSegments",
          "xray:PutTelemetryRecords"
        ]
        Resource = "${aws_cloudwatch_log_group.ai_analyzer.arn}:*"
      },
      {
        Effect = "Allow"
        Action = [
          "bedrock:InvokeModel",
          "bedrock:InvokeModelWithResponseStream"
        ]
        Resource = local.bedrock_model_arn
      },
      {
        Effect = "Allow"
        Action = [
          "dynamodb:PutItem",
          "dynamodb:UpdateItem",
          "dynamodb:GetItem",
          "dynamodb:Query"
        ]
        Resource = [
          aws_dynamodb_table.results.arn,
          "${aws_dynamodb_table.results.arn}/index/file_hash-index"
        ]
      },
      {
        Effect   = "Allow"
        Action   = ["sqs:SendMessage"]
        Resource = aws_sqs_queue.budget_analyzer_dlq.arn
      }
    ]
  })
}

resource "aws_iam_role_policy" "textract_callback_handler" {
  name = "${var.project_name}-textract-callback-handler"
  role = aws_iam_role.textract_callback_handler.id

  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      {
        Effect = "Allow"
        Action = [
          "logs:CreateLogStream",
          "logs:PutLogEvents"
        ]
        Resource = "${aws_cloudwatch_log_group.textract_callback_handler.arn}:*"
      },
      {
        Effect = "Allow"
        Action = [
          "textract:GetDocumentAnalysis",
          "textract:GetDocumentTextDetection"
        ]
        Resource = "*"
      },
      {
        Effect = "Allow"
        Action = [
          "s3:PutObject"
        ]
        Resource = "${aws_s3_bucket.uploads.arn}/extracted/*"
      },
      {
        Effect   = "Allow"
        Action   = ["lambda:InvokeFunction"]
        Resource = aws_lambda_function.ai_analyzer.arn
      }
    ]
  })
}

############################
# CloudWatch log groups (Lambda)
############################

resource "aws_cloudwatch_log_group" "upload_handler" {
  name              = "/aws/lambda/upload_handler"
  retention_in_days = var.lambda_log_retention_days
}

resource "aws_cloudwatch_log_group" "document_processor" {
  name              = "/aws/lambda/document_processor"
  retention_in_days = var.lambda_log_retention_days
}

resource "aws_cloudwatch_log_group" "ai_analyzer" {
  name              = "/aws/lambda/ai_analyzer"
  retention_in_days = var.lambda_log_retention_days
}

resource "aws_cloudwatch_log_group" "textract_callback_handler" {
  name              = "/aws/lambda/textract_callback_handler"
  retention_in_days = var.lambda_log_retention_days
}

############################
# Lambda packages
############################

data "archive_file" "upload_handler" {
  type        = "zip"
  source_dir  = "${path.module}/../lambda/upload_handler"
  output_path = "${path.module}/build/upload_handler.zip"
}

data "archive_file" "document_processor" {
  type        = "zip"
  source_dir  = "${path.module}/../lambda/document_processor"
  output_path = "${path.module}/build/document_processor.zip"
}

data "archive_file" "ai_analyzer" {
  type        = "zip"
  source_dir  = "${path.module}/../lambda/ai_analyzer"
  output_path = "${path.module}/build/ai_analyzer.zip"
}

data "archive_file" "textract_callback_handler" {
  type        = "zip"
  source_dir  = "${path.module}/../lambda/textract_callback_handler"
  output_path = "${path.module}/build/textract_callback_handler.zip"
}

resource "aws_lambda_function" "document_processor" {
  function_name = "document_processor"
  role          = aws_iam_role.document_processor.arn
  runtime       = "python3.11"
  handler       = "document_processor.lambda_handler"
  timeout       = 120
  memory_size   = 512

  filename         = data.archive_file.document_processor.output_path
  source_code_hash = data.archive_file.document_processor.output_base64sha256

  environment {
    variables = {
      UPLOAD_BUCKET             = aws_s3_bucket.uploads.bucket
      AI_ANALYZER_NAME          = aws_lambda_function.ai_analyzer.function_name
      TEXTRACT_SNS_TOPIC_ARN    = aws_sns_topic.textract_notifications.arn
      TEXTRACT_SERVICE_ROLE_ARN = aws_iam_role.textract_service_role.arn
    }
  }

  dead_letter_config {
    target_arn = aws_sqs_queue.budget_analyzer_dlq.arn
  }

  tracing_config {
    mode = "Active"
  }

  depends_on = [aws_cloudwatch_log_group.document_processor]
}

resource "aws_lambda_function" "ai_analyzer" {
  function_name = "ai_analyzer"
  role          = aws_iam_role.ai_analyzer.arn
  runtime       = "python3.11"
  handler       = "ai_analyzer.lambda_handler"
  timeout       = 120
  memory_size   = 512

  filename         = data.archive_file.ai_analyzer.output_path
  source_code_hash = data.archive_file.ai_analyzer.output_base64sha256

  environment {
    variables = {
      TABLE_NAME       = aws_dynamodb_table.results.name
      BEDROCK_MODEL_ID = var.bedrock_model_id
    }
  }

  dead_letter_config {
    target_arn = aws_sqs_queue.budget_analyzer_dlq.arn
  }

  tracing_config {
    mode = "Active"
  }

  depends_on = [aws_cloudwatch_log_group.ai_analyzer]
}

resource "aws_lambda_function" "textract_callback_handler" {
  function_name = "textract_callback_handler"
  role          = aws_iam_role.textract_callback_handler.arn
  runtime       = "python3.11"
  handler       = "textract_callback_handler.textract_callback_handler.lambda_handler"
  timeout       = 120
  memory_size   = 512

  filename         = data.archive_file.textract_callback_handler.output_path
  source_code_hash = data.archive_file.textract_callback_handler.output_base64sha256

  environment {
    variables = {
      AI_ANALYZER_NAME = aws_lambda_function.ai_analyzer.function_name
      UPLOAD_BUCKET    = aws_s3_bucket.uploads.bucket
    }
  }

  depends_on = [aws_cloudwatch_log_group.textract_callback_handler]
}

resource "aws_lambda_function" "upload_handler" {
  function_name = "upload_handler"
  role          = aws_iam_role.upload_handler.arn
  runtime       = "python3.11"
  handler       = "upload_handler.lambda_handler"
  timeout       = 60
  memory_size   = 256

  filename         = data.archive_file.upload_handler.output_path
  source_code_hash = data.archive_file.upload_handler.output_base64sha256

  environment {
    variables = {
      UPLOAD_BUCKET       = aws_s3_bucket.uploads.bucket
      DYNAMODB_TABLE_NAME = aws_dynamodb_table.results.name
    }
  }

  dead_letter_config {
    target_arn = aws_sqs_queue.budget_analyzer_dlq.arn
  }

  tracing_config {
    mode = "Active"
  }

  depends_on = [aws_cloudwatch_log_group.upload_handler]
}

resource "aws_lambda_permission" "s3_invoke_document_processor" {
  statement_id  = "AllowS3InvokeDocumentProcessor"
  action        = "lambda:InvokeFunction"
  function_name = aws_lambda_function.document_processor.function_name
  principal     = "s3.amazonaws.com"
  source_arn    = aws_s3_bucket.uploads.arn
}

resource "aws_s3_bucket_notification" "uploads_triggers_processor" {
  bucket = aws_s3_bucket.uploads.id

  lambda_function {
    lambda_function_arn = aws_lambda_function.document_processor.arn
    events              = ["s3:ObjectCreated:*"]
    filter_prefix       = "uploads/"
  }

  depends_on = [aws_lambda_permission.s3_invoke_document_processor]
}

resource "aws_lambda_permission" "ai_analyzer_from_document_processor" {
  statement_id  = "AllowInvokeFromDocumentProcessorRole"
  action        = "lambda:InvokeFunction"
  function_name = aws_lambda_function.ai_analyzer.function_name
  principal     = aws_iam_role.document_processor.arn
}

resource "aws_lambda_permission" "sns_invoke_textract_callback_handler" {
  statement_id  = "AllowSNSInvokeTextractCallbackHandler"
  action        = "lambda:InvokeFunction"
  function_name = aws_lambda_function.textract_callback_handler.function_name
  principal     = "sns.amazonaws.com"
  source_arn    = aws_sns_topic.textract_notifications.arn
}

resource "aws_sns_topic_subscription" "textract_notifications_to_lambda" {
  topic_arn  = aws_sns_topic.textract_notifications.arn
  protocol   = "lambda"
  endpoint   = aws_lambda_function.textract_callback_handler.arn
  depends_on = [aws_lambda_permission.sns_invoke_textract_callback_handler]
}

############################
# API Gateway (REST)
############################

resource "aws_api_gateway_rest_api" "main" {
  name        = "${var.project_name}-rest-api"
  description = "Fraud, Waste, and Abuse Budget Analyzer API"

  endpoint_configuration {
    types = ["REGIONAL"]
  }
}

resource "aws_api_gateway_resource" "upload" {
  rest_api_id = aws_api_gateway_rest_api.main.id
  parent_id   = aws_api_gateway_rest_api.main.root_resource_id
  path_part   = "upload"
}

resource "aws_api_gateway_resource" "results" {
  rest_api_id = aws_api_gateway_rest_api.main.id
  parent_id   = aws_api_gateway_rest_api.main.root_resource_id
  path_part   = "results"
}

resource "aws_api_gateway_method" "upload_post" {
  rest_api_id   = aws_api_gateway_rest_api.main.id
  resource_id   = aws_api_gateway_resource.upload.id
  http_method   = "POST"
  authorization = "NONE"
}

resource "aws_api_gateway_method" "results_get" {
  rest_api_id   = aws_api_gateway_rest_api.main.id
  resource_id   = aws_api_gateway_resource.results.id
  http_method   = "GET"
  authorization = "NONE"
  request_parameters = {
    "method.request.querystring.documentId" = true
  }
}

resource "aws_api_gateway_integration" "upload_post" {
  rest_api_id = aws_api_gateway_rest_api.main.id
  resource_id = aws_api_gateway_resource.upload.id
  http_method = aws_api_gateway_method.upload_post.http_method

  integration_http_method = "POST"
  type                    = "AWS_PROXY"
  uri                     = aws_lambda_function.upload_handler.invoke_arn
}

resource "aws_api_gateway_integration" "results_get" {
  rest_api_id = aws_api_gateway_rest_api.main.id
  resource_id = aws_api_gateway_resource.results.id
  http_method = aws_api_gateway_method.results_get.http_method

  integration_http_method = "POST"
  type                    = "AWS_PROXY"
  uri                     = aws_lambda_function.ai_analyzer.invoke_arn
}

resource "aws_lambda_permission" "apigw_upload" {
  statement_id  = "AllowAPIGatewayInvokeUpload"
  action        = "lambda:InvokeFunction"
  function_name = aws_lambda_function.upload_handler.function_name
  principal     = "apigateway.amazonaws.com"
  source_arn    = "${aws_api_gateway_rest_api.main.execution_arn}/*/*"
}

resource "aws_lambda_permission" "apigw_results" {
  statement_id  = "AllowAPIGatewayInvokeResults"
  action        = "lambda:InvokeFunction"
  function_name = aws_lambda_function.ai_analyzer.function_name
  principal     = "apigateway.amazonaws.com"
  source_arn    = "${aws_api_gateway_rest_api.main.execution_arn}/*/*"
}

resource "aws_api_gateway_deployment" "main" {
  rest_api_id = aws_api_gateway_rest_api.main.id

  triggers = {
    redeploy = sha1(jsonencode([
      aws_api_gateway_integration.upload_post.id,
      aws_api_gateway_integration.results_get.id,
      aws_api_gateway_method.upload_post.id,
      aws_api_gateway_method.results_get.id,
    ]))
  }

  lifecycle {
    create_before_destroy = true
  }

  depends_on = [
    aws_api_gateway_integration.upload_post,
    aws_api_gateway_integration.results_get,
  ]
}

resource "aws_api_gateway_stage" "prod" {
  deployment_id = aws_api_gateway_deployment.main.id
  rest_api_id   = aws_api_gateway_rest_api.main.id
  stage_name    = "prod"

  xray_tracing_enabled = false
}

# REST API throttling is configured per stage via method settings (not on aws_api_gateway_stage).
resource "aws_api_gateway_method_settings" "prod_throttling" {
  rest_api_id = aws_api_gateway_rest_api.main.id
  stage_name  = aws_api_gateway_stage.prod.stage_name
  method_path = "*/*"

  settings {
    throttling_burst_limit = var.api_throttle_burst_limit
    throttling_rate_limit  = local.api_rate_limit_rps
  }
}

############################
# CloudWatch — monitoring
############################

resource "aws_cloudwatch_log_metric_filter" "textract_failures" {
  name           = "${var.project_name}-textract-failure-filter"
  log_group_name = aws_cloudwatch_log_group.document_processor.name
  pattern        = "TEXTRACT_FAILURE"

  metric_transformation {
    name      = "TextractFailureCount"
    namespace = "${var.project_name}/Textract"
    value     = "1"
  }
}

resource "aws_cloudwatch_metric_alarm" "lambda_errors_upload_handler" {
  alarm_name          = "${var.project_name}-upload-handler-errors"
  comparison_operator = "GreaterThanThreshold"
  evaluation_periods  = 1
  metric_name         = "Errors"
  namespace           = "AWS/Lambda"
  period              = 300
  statistic           = "Sum"
  threshold           = 0
  treat_missing_data  = "notBreaching"

  dimensions = {
    FunctionName = aws_lambda_function.upload_handler.function_name
  }
}

resource "aws_cloudwatch_metric_alarm" "lambda_errors_document_processor" {
  alarm_name          = "${var.project_name}-document-processor-errors"
  comparison_operator = "GreaterThanThreshold"
  evaluation_periods  = 1
  metric_name         = "Errors"
  namespace           = "AWS/Lambda"
  period              = 300
  statistic           = "Sum"
  threshold           = 0
  treat_missing_data  = "notBreaching"

  dimensions = {
    FunctionName = aws_lambda_function.document_processor.function_name
  }
}

resource "aws_cloudwatch_metric_alarm" "lambda_errors_ai_analyzer" {
  alarm_name          = "${var.project_name}-ai-analyzer-errors"
  comparison_operator = "GreaterThanThreshold"
  evaluation_periods  = 1
  metric_name         = "Errors"
  namespace           = "AWS/Lambda"
  period              = 300
  statistic           = "Sum"
  threshold           = 0
  treat_missing_data  = "notBreaching"

  dimensions = {
    FunctionName = aws_lambda_function.ai_analyzer.function_name
  }
}

resource "aws_cloudwatch_metric_alarm" "textract_failure_indicator" {
  alarm_name          = "${var.project_name}-textract-failures"
  comparison_operator = "GreaterThanThreshold"
  evaluation_periods  = 1
  metric_name         = "TextractFailureCount"
  namespace           = "${var.project_name}/Textract"
  period              = 300
  statistic           = "Sum"
  threshold           = 0
  treat_missing_data  = "notBreaching"
}

resource "aws_cloudwatch_metric_alarm" "dynamodb_throttling" {
  alarm_name          = "${var.project_name}-dynamodb-throttled-requests"
  comparison_operator = "GreaterThanThreshold"
  evaluation_periods  = 1
  metric_name         = "ThrottledRequests"
  namespace           = "AWS/DynamoDB"
  period              = 300
  statistic           = "Sum"
  threshold           = 0
  treat_missing_data  = "notBreaching"

  dimensions = {
    TableName = aws_dynamodb_table.results.name
  }
}

############################
# AWS Budgets
############################

resource "aws_budgets_budget" "monthly" {
  name              = "${var.project_name}-monthly-cost"
  budget_type       = "COST"
  limit_amount      = var.budget_limit_usd
  limit_unit        = "USD"
  time_period_start = var.budget_start_date
  time_unit         = "MONTHLY"

  notification {
    comparison_operator       = "GREATER_THAN"
    threshold                 = 50
    threshold_type            = "PERCENTAGE"
    notification_type         = "ACTUAL"
    subscriber_sns_topic_arns = [aws_sns_topic.budget_alerts.arn]
  }

  notification {
    comparison_operator       = "GREATER_THAN"
    threshold                 = 80
    threshold_type            = "PERCENTAGE"
    notification_type         = "ACTUAL"
    subscriber_sns_topic_arns = [aws_sns_topic.budget_alerts.arn]
  }

  notification {
    comparison_operator       = "GREATER_THAN"
    threshold                 = 100
    threshold_type            = "PERCENTAGE"
    notification_type         = "ACTUAL"
    subscriber_sns_topic_arns = [aws_sns_topic.budget_alerts.arn]
  }

  depends_on = [aws_sns_topic_policy.budget_alerts]
}

output "budget_analyzer_dlq_arn" {
  description = "The ARN of the budget analyzer SQS Dead Letter Queue"
  value       = aws_sqs_queue.budget_analyzer_dlq.arn
}
