from fastapi.testclient import TestClient

# Import the app after environment variables are set in conftest
from app.main import app

client = TestClient(app)


def test_health_check():
    """Test that the API is up and running"""
    response = client.get("/health")
    assert response.status_code == 200
    assert response.json() == {"status": "healthy", "environment": "test"}


def test_submit_job_success(dynamodb, sqs):
    """
    Test submitting a job successfully.
    We verify that the API writes to DB, sends to SQS,
    and returns 202 instantly.
    """
    payload = {
        "text_payload": "Analyze this test document.",
        "model_profile": "fast-cost-effective",
    }

    response = client.post("/api/v1/jobs", json=payload)

    # Assert fast response
    assert response.status_code == 202
    data = response.json()
    assert "job_id" in data
    assert data["status"] == "PENDING"
    assert "X-Correlation-ID" in response.headers

    job_id = data["job_id"]

    # Assert it was written to DynamoDB
    import os

    db_response = dynamodb.get_item(
        TableName=os.environ["JOBS_TABLE"], Key={"job_id": {"S": job_id}}
    )
    assert "Item" in db_response
    assert db_response["Item"]["status"]["S"] == "PENDING"
    assert "correlation_id" in db_response["Item"]

    # Assert it was queued to SQS
    sqs_response = sqs.receive_message(
        QueueUrl=os.environ["SQS_QUEUE_URL"], MaxNumberOfMessages=1
    )
    assert "Messages" in sqs_response
    assert len(sqs_response["Messages"]) == 1

    import json

    sqs_body = json.loads(sqs_response["Messages"][0]["Body"])
    assert sqs_body["job_id"] == job_id
    assert sqs_body["text_payload"] == payload["text_payload"]
    assert "correlation_id" in sqs_body


def test_submit_job_idempotency(dynamodb, sqs):
    """
    Test that sending the same idempotency key twice returns the exact same job_id
    without duplicating the workload.
    """
    payload = {
        "text_payload": "Duplicate test document.",
        "model_profile": "fast-cost-effective",
    }
    headers = {"X-Idempotency-Key": "test-key-12345"}

    # First request
    response1 = client.post("/api/v1/jobs", json=payload, headers=headers)
    assert response1.status_code == 202
    job_id_1 = response1.json()["job_id"]

    # Second request (Network retry simulation)
    response2 = client.post("/api/v1/jobs", json=payload, headers=headers)
    assert response2.status_code == 200  # Notice it's 200 OK, not 202 Accepted
    job_id_2 = response2.json()["job_id"]

    # Ensure they match exactly
    assert job_id_1 == job_id_2
    assert (
        response2.json()["message"]
        == "Idempotency key recognized. Job already in queue."
    )

    # Ensure SQS only has ONE message
    import os

    sqs_response = sqs.receive_message(
        QueueUrl=os.environ["SQS_QUEUE_URL"], MaxNumberOfMessages=10
    )
    assert len(sqs_response["Messages"]) == 1
