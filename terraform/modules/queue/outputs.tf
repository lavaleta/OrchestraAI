output "queue_url" {
  description = "The URL of the main SQS queue"
  value       = aws_sqs_queue.jobs_queue.url
}

output "queue_arn" {
  description = "The ARN of the main SQS queue"
  value       = aws_sqs_queue.jobs_queue.arn
}

output "dlq_url" {
  description = "The URL of the Dead Letter Queue"
  value       = aws_sqs_queue.jobs_dlq.url
}

output "dlq_arn" {
  description = "The ARN of the Dead Letter Queue"
  value       = aws_sqs_queue.jobs_dlq.arn
}