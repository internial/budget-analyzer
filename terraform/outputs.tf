# Values to use after terraform apply (URLs, names, ARNs).

output "s3_bucket_name" {
  description = "Primary S3 bucket for uploads and processing prefixes."
  value       = aws_s3_bucket.uploads.bucket
}

output "s3_replica_bucket_name" {
  description = "Cross-region replication destination bucket name."
  value       = aws_s3_bucket.uploads_replica.bucket
}

output "api_gateway_endpoint" {
  description = "Invoke URL for the REST API (stage prod)."
  value       = aws_api_gateway_stage.prod.invoke_url
}

output "api_gateway_execution_arn" {
  description = "Execution ARN of the REST API (for integrations and permissions)."
  value       = aws_api_gateway_rest_api.main.execution_arn
}

output "dynamodb_table_name" {
  description = "DynamoDB table storing analysis results."
  value       = aws_dynamodb_table.results.name
}

output "lambda_arns" {
  description = "Map of logical names to Lambda function ARNs."
  value = {
    upload_handler     = aws_lambda_function.upload_handler.arn
    document_processor = aws_lambda_function.document_processor.arn
    ai_analyzer        = aws_lambda_function.ai_analyzer.arn
  }
}

output "upload_handler_lambda_arn" {
  value = aws_lambda_function.upload_handler.arn
}

output "document_processor_lambda_arn" {
  value = aws_lambda_function.document_processor.arn
}

output "ai_analyzer_lambda_arn" {
  value = aws_lambda_function.ai_analyzer.arn
}

output "sns_budget_topic_arn" {
  description = "SNS topic receiving AWS Budgets notifications."
  value       = aws_sns_topic.budget_alerts.arn
}

output "cloudtrail_name" {
  description = "Multi-region CloudTrail delivering to the dedicated audit bucket."
  value       = aws_cloudtrail.audit.name
}
