output "table_name" {
  description = "The name of the DynamoDB table"
  value       = aws_dynamodb_table.jobs.name
}

output "table_arn" {
  description = "The ARN of the DynamoDB table"
  value       = aws_dynamodb_table.jobs.arn
}