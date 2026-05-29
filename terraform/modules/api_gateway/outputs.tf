output "api_endpoint" {
  description = "The default URL for the API Gateway"
  value       = aws_apigatewayv2_api.http_api.api_endpoint
}

output "execution_arn" {
  description = "The execution ARN of the API Gateway"
  value       = aws_apigatewayv2_api.http_api.execution_arn
}