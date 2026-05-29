import json
import os
import pytest
from unittest.mock import patch, MagicMock

from workers.handler import sqs_handler
from workers.services.ai_service import RateLimitException

def create_mock_sqs_event(job_id: str, receive_count: int = 1):
    """Helper to generate an AWS SQS Lambda trigger event"""
    body = {
        "job_id": job_id,
        "text_payload": "Test text",
        "model_profile": "fast-cost-effective",
        "correlation_id": "corr-123"
    }
    return {
        "Records": [
            {
                "messageId": f"msg-{job_id}",
                "receiptHandle": "handle-123",
                "body": json.dumps(body),
                "attributes": {
                    "ApproximateReceiveCount": str(receive_count)
                }
            }
        ]
    }

@patch("workers.handler.AIServiceFactory.get_provider")
def test_worker_success(mock_get_provider, dynamodb):
    """
    Test the happy path of the worker Lambda.
    """
    # 1. Setup: Create a pending job in DDB
    job_id = "job-test-success"
    dynamodb.put_item(
        TableName=os.environ["JOBS_TABLE"],
        Item={
            "job_id": {"S": job_id},
            "status": {"S": "PENDING"}
        }
    )
    
    # 2. Setup: Mock the AI Provider response
    mock_provider = MagicMock()
    mock_provider.process.return_value = {
        "result": {"summary": "Mocked summary"},
        "metrics": {"input_tokens": 10, "output_tokens": 20, "cost_usd": 0.001}
    }
    mock_get_provider.return_value = mock_provider
    
    # 3. Action: Invoke the lambda handler
    event = create_mock_sqs_event(job_id)
    response = sqs_handler(event, None)
    
    # 4. Assert: Empty batchItemFailures (meaning success)
    assert response == {"batchItemFailures": []}
    
    # 5. Assert: DDB status updated to COMPLETED with result
    db_item = dynamodb.get_item(
        TableName=os.environ["JOBS_TABLE"],
        Key={"job_id": {"S": job_id}}
    )["Item"]
    
    assert db_item["status"]["S"] == "COMPLETED"
    assert "Mocked summary" in db_item["result"]["S"]

@patch("workers.handler.AIServiceFactory.get_provider")
@patch("workers.handler.change_message_visibility")
def test_worker_rate_limit_backoff(mock_change_visibility, mock_get_provider, dynamodb):
    """
    Test that a 429 Rate Limit triggers the smart exponential backoff
    and returns a batchItemFailure so SQS knows to retry later.
    """
    job_id = "job-test-429"
    dynamodb.put_item(
        TableName=os.environ["JOBS_TABLE"],
        Item={"job_id": {"S": job_id}, "status": {"S": "PENDING"}}
    )
    
    # Mock the AI Provider to throw a RateLimitException
    mock_provider = MagicMock()
    mock_provider.process.side_effect = RateLimitException("429 Too Many Requests")
    mock_get_provider.return_value = mock_provider
    
    # Simulate the 2nd attempt from SQS (receive_count = 2)
    # Expected backoff: 30 * (2^(2-1)) = 60 seconds
    event = create_mock_sqs_event(job_id, receive_count=2)
    
    response = sqs_handler(event, None)
    
    # Assert: It returned the messageId as a failure
    assert len(response["batchItemFailures"]) == 1
    assert response["batchItemFailures"][0]["itemIdentifier"] == f"msg-{job_id}"
    
    # Assert: It dynamically extended the visibility timeout to 60 seconds
    mock_change_visibility.assert_called_once_with(
        "handle-123", 
        os.environ["SQS_QUEUE_URL"], 
        60
    )
    
    # Assert: Status in DDB is still PROCESSING (not FAILED, because it's retrying)
    db_item = dynamodb.get_item(
        TableName=os.environ["JOBS_TABLE"],
        Key={"job_id": {"S": job_id}}
    )["Item"]
    assert db_item["status"]["S"] == "PROCESSING"