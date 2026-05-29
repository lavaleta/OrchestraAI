output "api_lambda_arn" {
  description = "The ARN of the API Lambda function"
  value       = aws_lambda_function.api.arn
}

output "api_lambda_name" {
  description = "The name of the API Lambda function"
  value       = aws_lambda_function.api.function_name
}

output "worker_lambda_arn" {
  description = "The ARN of the Worker Lambda function"
  value       = aws_lambda_function.worker.arn
}

output "worker_lambda_name" {
  description = "The name of the Worker Lambda function"
  value       = aws_lambda_function.worker.function_name
}