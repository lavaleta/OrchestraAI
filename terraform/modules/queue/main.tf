# -----------------------------------------------------------------------------
# Dead Letter Queue (DLQ)
# -----------------------------------------------------------------------------
# Messages that fail repeatedly end up here so they don't block the main queue.
resource "aws_sqs_queue" "jobs_dlq" {
  name                      = "orchestra-ai-jobs-dlq-${var.environment}"
  message_retention_seconds = 1209600 # 14 days, max allowed, so we have time to debug

  # Enable encryption at rest (AWS managed key)
  sqs_managed_sse_enabled = true
}

# -----------------------------------------------------------------------------
# Main Processing Queue
# -----------------------------------------------------------------------------
resource "aws_sqs_queue" "jobs_queue" {
  name = "orchestra-ai-jobs-${var.environment}"
  
  # Visibility Timeout must be GREATER than the Lambda timeout.
  # AI APIs can take a while (e.g., 60-90s). We give the queue 3 minutes 
  # so the message isn't picked up by another worker while still processing.
  visibility_timeout_seconds = 180 

  # Keep messages for 4 days if no workers are available
  message_retention_seconds  = 345600 
  
  # Wait time for long polling (reduces cost, improves speed)
  receive_wait_time_seconds  = 20 

  sqs_managed_sse_enabled = true

  redrive_policy = jsonencode({
    deadLetterTargetArn = aws_sqs_queue.jobs_dlq.arn
    maxReceiveCount     = 3 # Move to DLQ after 3 failed attempts
  })
}

# Allows the DLQ to receive redriven messages from the main queue
resource "aws_sqs_queue_redrive_allow_policy" "jobs_queue_redrive" {
  queue_url = aws_sqs_queue.jobs_dlq.id

  redrive_allow_policy = jsonencode({
    redrivePermission = "byQueue",
    sourceQueueArns   = [aws_sqs_queue.jobs_queue.arn]
  })
}