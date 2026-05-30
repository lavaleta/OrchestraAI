import os
import sys
from pathlib import Path
import pytest
import boto3
from moto import mock_aws

# Add the 'src' directory to the Python path so tests can import our app and workers
sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

# Set environment variables BEFORE importing the app
os.environ["ENVIRONMENT"] = "test"
os.environ["JOBS_TABLE"] = "orchestra-ai-jobs-test"
os.environ["SQS_QUEUE_URL"] = (
    "https://sqs.eu-west-1.amazonaws.com/123456789012/test-queue"
)
os.environ["AWS_REGION"] = "eu-west-1"
os.environ["AWS_DEFAULT_REGION"] = "eu-west-1"
# Required by boto3/moto
os.environ["AWS_ACCESS_KEY_ID"] = "testing"
os.environ["AWS_SECRET_ACCESS_KEY"] = "testing"
os.environ["AWS_SECURITY_TOKEN"] = "testing"
os.environ["AWS_SESSION_TOKEN"] = "testing"


@pytest.fixture(scope="function")
def aws_credentials():
    """Mocked AWS Credentials for moto."""
    pass


@pytest.fixture(scope="function")
def dynamodb(aws_credentials):
    """Yields a mocked DynamoDB client and creates the jobs table."""
    with mock_aws():
        dynamodb_client = boto3.client("dynamodb", region_name="eu-west-1")

        # Create the table schema to match Terraform
        dynamodb_client.create_table(
            TableName=os.environ["JOBS_TABLE"],
            KeySchema=[{"AttributeName": "job_id", "KeyType": "HASH"}],
            AttributeDefinitions=[
                {"AttributeName": "job_id", "AttributeType": "S"},
                {"AttributeName": "idempotency_key", "AttributeType": "S"},
                {"AttributeName": "status", "AttributeType": "S"},
            ],
            GlobalSecondaryIndexes=[
                {
                    "IndexName": "IdempotencyKeyIndex",
                    "KeySchema": [
                        {"AttributeName": "idempotency_key", "KeyType": "HASH"}
                    ],
                    "Projection": {"ProjectionType": "KEYS_ONLY"},
                },
                {
                    "IndexName": "StatusIndex",
                    "KeySchema": [{"AttributeName": "status", "KeyType": "HASH"}],
                    "Projection": {"ProjectionType": "ALL"},
                },
            ],
            BillingMode="PAY_PER_REQUEST",
        )
        yield dynamodb_client


@pytest.fixture(scope="function")
def sqs(aws_credentials):
    """Yields a mocked SQS client and creates the main queue."""
    with mock_aws():
        sqs_client = boto3.client("sqs", region_name="eu-west-1")
        response = sqs_client.create_queue(QueueName="test-queue")
        os.environ["SQS_QUEUE_URL"] = response["QueueUrl"]
        yield sqs_client
