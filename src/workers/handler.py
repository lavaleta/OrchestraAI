import json
import time
import logging
from typing import Dict, Any, Optional

from app.core.config import settings, dynamodb_client, sqs_client
from workers.services.ai_service import AIServiceFactory, RateLimitException

# Ensure logger captures worker context
logger = logging.getLogger("orchestra-ai-worker")
logger.setLevel(logging.INFO)


def change_message_visibility(
    receipt_handle: str, queue_url: str, visibility_timeout: int
):
    """
    Smart Backoff.
    If we get a 429 Rate Limit, we don't just 'fail' the message and let Lambda auto-retry immediately.
    We explicitly tell SQS: "Hide this message for X seconds so the external API can cool down."
    """
    try:
        sqs_client.change_message_visibility(
            QueueUrl=queue_url,
            ReceiptHandle=receipt_handle,
            VisibilityTimeout=visibility_timeout,
        )
        logger.info(
            f"Extended visibility timeout by {visibility_timeout}s due to rate limit."
        )
    except Exception as e:
        logger.error(f"Failed to change message visibility: {str(e)}")


def update_job_status(
    job_id: str,
    status: str,
    result: Optional[Dict] = None,
    metrics: Optional[Dict] = None,
    error_reason: Optional[str] = None,
):
    """Updates the job record in DynamoDB"""
    timestamp = str(int(time.time()))

    update_expr = "SET #st = :status, updated_at = :updated_at"
    expr_attr_names = {"#st": "status"}
    expr_attr_values = {":status": {"S": status}, ":updated_at": {"S": timestamp}}

    if result is not None:
        update_expr += ", #res = :result"
        expr_attr_names["#res"] = "result"
        expr_attr_values[":result"] = {"S": json.dumps(result)}

    if metrics is not None:
        update_expr += ", #metrics_kw = :metrics"
        expr_attr_names["#metrics_kw"] = "metrics"
        expr_attr_values[":metrics"] = {"S": json.dumps(metrics)}

    if error_reason is not None:
        update_expr += ", error_reason = :error_reason"
        expr_attr_values[":error_reason"] = {"S": error_reason}

    try:
        dynamodb_client.update_item(
            TableName=settings.JOBS_TABLE,
            Key={"job_id": {"S": job_id}},
            UpdateExpression=update_expr,
            ExpressionAttributeNames=expr_attr_names,
            ExpressionAttributeValues=expr_attr_values,
        )
    except Exception as e:
        logger.error(f"Failed to update DDB for job {job_id}: {str(e)}")
        raise


def process_message(record: Dict[str, Any]):
    """Processes a single SQS message."""
    receipt_handle = record["receiptHandle"]

    # Extract the receive count to calculate true exponential backoff
    attributes = record.get("attributes", {})
    receive_count = int(attributes.get("ApproximateReceiveCount", 1))

    try:
        payload = json.loads(record["body"])
    except json.JSONDecodeError:
        logger.error("Failed to decode SQS message body.")
        # If we can't decode it, we can't process it. We let it fail so it goes to the DLQ.
        raise ValueError("Invalid JSON in message body")

    job_id = payload.get("job_id")
    text_payload = payload.get("text_payload")
    model_profile = payload.get("model_profile", "fast-cost-effective")
    extraction_schema = payload.get("extraction_schema")
    correlation_id = payload.get("correlation_id", "unknown")

    # Inject correlation ID into our logs manually for this function scope
    log_prefix = f"[correlation_id={correlation_id}] [job_id={job_id}]"
    logger.info(f"{log_prefix} Started processing (Attempt {receive_count}).")

    if not job_id or not text_payload:
        logger.error(f"{log_prefix} Missing required fields in payload.")
        raise ValueError("Missing job_id or text_payload")

    try:
        # Mark as processing in DB
        update_job_status(job_id, status="PROCESSING")

        # Instantiate the correct provider and run the LLM call
        ai_provider = AIServiceFactory.get_provider(model_profile)

        logger.info(f"{log_prefix} Hitting AI Provider...")
        start_time = time.time()

        # ACTUALLY CALL THE LLM
        ai_response = ai_provider.process(text=text_payload, schema=extraction_schema)

        duration = round(time.time() - start_time, 2)
        logger.info(f"{log_prefix} AI Provider success. Took {duration}s.")

        # Save results back to DDB
        update_job_status(
            job_id=job_id,
            status="COMPLETED",
            result=ai_response["result"],
            metrics=ai_response["metrics"],
        )

        logger.info(f"{log_prefix} Job fully completed.")

    except RateLimitException as rle:
        # Calculate true exponential backoff: base_delay * (10 ^ (receive_count - 1))
        # Attempt 1: 60 * 10^0 = 60s
        # Attempt 2: 60 * 10^1 = 600s
        # Attempt 3: 60 * 10^2 = 6000s
        base_delay = 60
        visibility_timeout = base_delay * (10 ** (receive_count - 1))
        # Cap at 15 minutes (900 seconds) just to be safe
        visibility_timeout = min(visibility_timeout, 9000)

        logger.warning(
            f"{log_prefix} Rate limited by provider."
            f" Triggering exponential backoff of {visibility_timeout}s (Attempt {receive_count}). Details: {str(rle)}"
        )

        # Extend visibility timeout so SQS waits exactly that long before letting another worker grab it.
        change_message_visibility(
            receipt_handle, settings.SQS_QUEUE_URL, visibility_timeout
        )
        # We raise the exception so Lambda knows the execution failed and leaves the message in the queue.
        raise

    except Exception as e:
        logger.error(
            f"{log_prefix} Fatal error during processing: {str(e)}", exc_info=True
        )
        # If it's a general exception, we update the DB so the user knows it failed
        update_job_status(job_id=job_id, status="FAILED", error_reason=str(e))
        # We re-raise the exception so SQS increments the receive count.
        # After 3 tries, it goes to the DLQ.
        raise


def sqs_handler(event, context):
    """
    AWS Lambda entrypoint for SQS trigger.
    """
    logger.info(f"Worker invoked. Processing {len(event.get('Records', []))} records.")

    # AWS Lambda allows batch processing (we configured batch_size=5 in Terraform).
    # If one message fails but 4 succeed, we need a way to tell AWS *which* one failed,
    # otherwise it will retry all 5.

    batch_item_failures = []

    for record in event.get("Records", []):
        try:
            process_message(record)
        except Exception as e:
            # Catch the error so we can process the rest of the batch
            logger.error(f"Failed to process message {record['messageId']}: {str(e)}")
            # Add to failures list. AWS will only retry this specific message.
            batch_item_failures.append({"itemIdentifier": record["messageId"]})

    # Return the explicit list of failed messages
    return {"batchItemFailures": batch_item_failures}
