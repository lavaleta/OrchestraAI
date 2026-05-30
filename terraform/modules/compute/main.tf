data "aws_caller_identity" "current" {}
data "aws_region" "current" {}

# ==============================================================================
# API LAMBDA (Synchronous FastAPI Handler)
# ==============================================================================

# IAM Role for API Lambda
resource "aws_iam_role" "api_lambda_role" {
  name = "orchestra-ai-api-role-${var.environment}"

  assume_role_policy = jsonencode({
    Version = "2012-10-17"
    Statement = [{
      Action = "sts:AssumeRole"
      Effect = "Allow"
      Principal = {
        Service = "lambda.amazonaws.com"
      }
    }]
  })
}

# Attach basic execution role (for CloudWatch logs)
resource "aws_iam_role_policy_attachment" "api_lambda_basic_execution" {
  role       = aws_iam_role.api_lambda_role.name
  policy_arn = "arn:aws:iam::aws:policy/service-role/AWSLambdaBasicExecutionRole"
}

# Least Privilege Policy: API only needs to Write/Read DynamoDB and Send SQS Messages
resource "aws_iam_policy" "api_lambda_policy" {
  name        = "orchestra-ai-api-policy-${var.environment}"
  description = "Permissions for the API Lambda to interact with DDB and SQS"

  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      {
        Effect = "Allow"
        Action = [
          "dynamodb:PutItem",
          "dynamodb:GetItem",
          "dynamodb:Query"
        ]
        Resource = [
          var.dynamodb_table_arn,
          "${var.dynamodb_table_arn}/index/*"
        ]
      },
      {
        Effect = "Allow"
        Action = [
          "sqs:SendMessage"
        ]
        Resource = var.sqs_queue_arn
      }
    ]
  })
}

resource "aws_iam_role_policy_attachment" "api_lambda_custom_policy" {
  role       = aws_iam_role.api_lambda_role.name
  policy_arn = aws_iam_policy.api_lambda_policy.arn
}

# Dummy archive to allow Terraform to create the Lambda before we build the actual Python code
data "archive_file" "dummy_api" {
  type        = "zip"
  output_path = "${path.module}/dummy_api.zip"
  source {
    content  = "def handler(event, context): return {'statusCode': 200, 'body': 'dummy'}"
    filename = "main.py"
  }
}

resource "aws_lambda_function" "api" {
  function_name    = "orchestra-ai-api-${var.environment}"
  role             = aws_iam_role.api_lambda_role.arn
  handler          = "app.main.handler" # Mangum entrypoint
  runtime          = "python3.11"
  timeout          = 29 # API Gateway has a 29s hard timeout limit
  memory_size      = 256 # Usually enough for FastAPI

  filename         = data.archive_file.dummy_api.output_path
  source_code_hash = data.archive_file.dummy_api.output_base64sha256

    environment {
    variables = {
      ENVIRONMENT   = var.environment
      JOBS_TABLE    = var.dynamodb_table_name
      SQS_QUEUE_URL = var.sqs_queue_url
    }
  }

  lifecycle {
    ignore_changes = [filename, source_code_hash]
  }
}

# ==============================================================================
# WORKER LAMBDA (Asynchronous SQS Consumer)
# ==============================================================================

# IAM Role for Worker Lambda
resource "aws_iam_role" "worker_lambda_role" {
  name = "orchestra-ai-worker-role-${var.environment}"

  assume_role_policy = jsonencode({
    Version = "2012-10-17"
    Statement = [{
      Action = "sts:AssumeRole"
      Effect = "Allow"
      Principal = {
        Service = "lambda.amazonaws.com"
      }
    }]
  })
}

resource "aws_iam_role_policy_attachment" "worker_lambda_basic_execution" {
  role       = aws_iam_role.worker_lambda_role.name
  policy_arn = "arn:aws:iam::aws:policy/service-role/AWSLambdaBasicExecutionRole"
}

# Least Privilege Policy: Worker needs to Read SQS, Update DDB, and read SSM parameters (for API keys)
resource "aws_iam_policy" "worker_lambda_policy" {
  name        = "orchestra-ai-worker-policy-${var.environment}"
  description = "Permissions for the Worker Lambda"

  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      {
        Effect = "Allow"
        Action = [
          "dynamodb:UpdateItem",
          "dynamodb:GetItem"
        ]
        Resource = var.dynamodb_table_arn
      },
      {
        Effect = "Allow"
        Action = [
          "sqs:ReceiveMessage",
          "sqs:DeleteMessage",
          "sqs:GetQueueAttributes",
          "sqs:ChangeMessageVisibility" # Crucial for our smart rate-limiting backoff
        ]
        Resource = var.sqs_queue_arn
      },
      {
        Effect = "Allow"
        Action = [
          "ssm:GetParameter",
          "ssm:GetParameters"
        ]
        Resource = "arn:aws:ssm:${data.aws_region.current.name}:${data.aws_caller_identity.current.account_id}:parameter/orchestra-ai/${var.environment}/*"
      }
    ]
  })
}

resource "aws_iam_role_policy_attachment" "worker_lambda_custom_policy" {
  role       = aws_iam_role.worker_lambda_role.name
  policy_arn = aws_iam_policy.worker_lambda_policy.arn
}

data "archive_file" "dummy_worker" {
  type        = "zip"
  output_path = "${path.module}/dummy_worker.zip"
  source {
    content  = "def handler(event, context): print('dummy')"
    filename = "handler.py"
  }
}

resource "aws_lambda_function" "worker" {
  function_name    = "orchestra-ai-worker-${var.environment}"
  role             = aws_iam_role.worker_lambda_role.arn
  handler          = "workers.handler.sqs_handler"
  runtime          = "python3.11"
  timeout          = 120 # Give enough time for LLM calls (OpenAI can be slow)
  memory_size      = 256

  filename         = data.archive_file.dummy_worker.output_path
  source_code_hash = data.archive_file.dummy_worker.output_base64sha256

    environment {
    variables = {
      ENVIRONMENT   = var.environment
      JOBS_TABLE    = var.dynamodb_table_name
    }
  }

  lifecycle {
    ignore_changes = [filename, source_code_hash]
  }
}

# ==============================================================================
# SQS -> LAMBDA TRIGGER (Event Source Mapping)
# ==============================================================================
resource "aws_lambda_event_source_mapping" "worker_sqs_trigger" {
  event_source_arn = var.sqs_queue_arn
  function_name    = aws_lambda_function.worker.arn
  
  # How many messages Lambda processes at once. 
  # For LLMs, we want smaller batches so one timeout doesn't fail 10 messages.
  batch_size       = 5 
  
  # If the worker lambda fails to process the batch, it won't be deleted from the queue 
  # and will eventually move to DLQ.
}