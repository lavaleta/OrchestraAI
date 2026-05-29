variable "environment" {
  description = "The environment name"
  type        = string
}

variable "dynamodb_table_arn" {
  description = "ARN of the jobs DynamoDB table"
  type        = string
}

variable "dynamodb_table_name" {
  description = "Name of the jobs DynamoDB table"
  type        = string
}

variable "sqs_queue_arn" {
  description = "ARN of the main SQS queue"
  type        = string
}

variable "sqs_queue_url" {
  description = "URL of the main SQS queue"
  type        = string
}
