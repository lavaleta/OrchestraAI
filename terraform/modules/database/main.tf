# We use a single-table design for OrchestraAI
resource "aws_dynamodb_table" "jobs" {
  name         = "orchestra-ai-jobs-${var.environment}"
  billing_mode = "PAY_PER_REQUEST" # Crucial for serverless, scales automatically, costs nothing when idle
  
  # Partition Key: The unique job ID
  hash_key     = "job_id"

  attribute {
    name = "job_id"
    type = "S"
  }

  # Global Secondary Index for Idempotency
  # Allows us to quickly check if an idempotency key was already processed
  attribute {
    name = "idempotency_key"
    type = "S"
  }

  # Global Secondary Index for querying jobs by status (e.g., PENDING, PROCESSING)
  attribute {
    name = "status"
    type = "S"
  }

  global_secondary_index {
    name               = "IdempotencyKeyIndex"
    hash_key           = "idempotency_key"
    projection_type    = "KEYS_ONLY"
  }

  global_secondary_index {
    name               = "StatusIndex"
    hash_key           = "status"
    projection_type    = "ALL"
  }

  # Enable point-in-time recovery for production-grade data safety
  point_in_time_recovery {
    enabled = true
  }

  tags = {
    Name = "orchestra-ai-jobs-${var.environment}"
  }
}
