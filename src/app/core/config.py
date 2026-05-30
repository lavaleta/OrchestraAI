import os
import boto3
import logging

logger = logging.getLogger("orchestra-ai")
logger.setLevel(logging.INFO)


class Settings:
    # Read environment variables injected by Terraform
    ENVIRONMENT = os.getenv("ENVIRONMENT", "local")
    JOBS_TABLE = os.getenv("JOBS_TABLE", "orchestra-ai-jobs-local")
    SQS_QUEUE_URL = os.getenv(
        "SQS_QUEUE_URL", "http://localhost:4566/000000000000/local-queue"
    )
    AWS_REGION = os.getenv("AWS_REGION", "eu-west-1")


settings = Settings()

# Initialize boto3 clients once per Lambda execution environment (cold start optimization)
# If we are not running locally, boto3 will automatically use the IAM role we created in Terraform
dynamodb_client = boto3.client("dynamodb", region_name=settings.AWS_REGION)
sqs_client = boto3.client("sqs", region_name=settings.AWS_REGION)
