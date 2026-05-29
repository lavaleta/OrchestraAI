data "aws_caller_identity" "current" {}
data "aws_region" "current" {}

# ==============================================================================
# API GATEWAY (HTTP API v2)
# Faster, cheaper, and strictly better for FastAPI/Mangum proxy setups than REST APIs
# ==============================================================================

resource "aws_apigatewayv2_api" "http_api" {
  name          = "orchestra-ai-gateway-${var.environment}"
  protocol_type = "HTTP"
  
  # Optional but good to enable CORS properly
  cors_configuration {
    allow_origins = ["*"] # In prod this would be restricted
    allow_methods = ["GET", "POST", "OPTIONS"]
    allow_headers = ["content-type", "authorization", "x-idempotency-key"]
    max_age       = 300
  }
}

# The stage (e.g., dev, prod)
resource "aws_apigatewayv2_stage" "default" {
  api_id      = aws_apigatewayv2_api.http_api.id
  name        = "$default" # Automatically deployed stage
  auto_deploy = true

  access_log_settings {
    destination_arn = aws_cloudwatch_log_group.api_gw_logs.arn
    format = jsonencode({
      requestId               = "$context.requestId"
      sourceIp                = "$context.identity.sourceIp"
      requestTime             = "$context.requestTime"
      protocol                = "$context.protocol"
      httpMethod              = "$context.httpMethod"
      resourcePath            = "$context.resourcePath"
      routeKey                = "$context.routeKey"
      status                  = "$context.status"
      responseLength          = "$context.responseLength"
      integrationErrorMessage = "$context.integrationErrorMessage"
      duration                = "$context.responseLatency"
    })
  }
}

# Cloudwatch logs for the API Gateway
resource "aws_cloudwatch_log_group" "api_gw_logs" {
  name              = "/aws/apigateway/orchestra-ai-${var.environment}"
  retention_in_days = 14
}

# Integration: Connect API Gateway to our FastAPI Lambda
resource "aws_apigatewayv2_integration" "lambda_integration" {
  api_id                 = aws_apigatewayv2_api.http_api.id
  integration_type       = "AWS_PROXY" # Crucial: Passes everything directly to FastAPI/Mangum
  
  integration_uri        = var.api_lambda_arn
  integration_method     = "POST"
  payload_format_version = "2.0" # Use v2 payloads for modern HTTP APIs
}

# Route: Catch-all route ({proxy+}) to forward everything to FastAPI
resource "aws_apigatewayv2_route" "default_route" {
  api_id    = aws_apigatewayv2_api.http_api.id
  route_key = "ANY /{proxy+}"

  target = "integrations/${aws_apigatewayv2_integration.lambda_integration.id}"
}

# Route: Explicit root route (sometimes needed for FastAPI base path)
resource "aws_apigatewayv2_route" "root_route" {
  api_id    = aws_apigatewayv2_api.http_api.id
  route_key = "ANY /"

  target = "integrations/${aws_apigatewayv2_integration.lambda_integration.id}"
}

# ==============================================================================
# PERMISSIONS: Allow API Gateway to invoke the Lambda
# ==============================================================================
resource "aws_lambda_permission" "apigw" {
  statement_id  = "AllowExecutionFromAPIGateway"
  action        = "lambda:InvokeFunction"
  function_name = var.api_lambda_name
  principal     = "apigateway.amazonaws.com"

  # The /*/*/* part allows invocation from any stage, method and resource path
  source_arn = "${aws_apigatewayv2_api.http_api.execution_arn}/*/*"
}