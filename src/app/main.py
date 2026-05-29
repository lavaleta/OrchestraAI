from fastapi import FastAPI, Header, HTTPException, Request
from fastapi.responses import JSONResponse
from mangum import Mangum
from contextvars import ContextVar
import uuid
import time
import json

from app.schemas.jobs import JobRequest, JobResponse, JobStatus, JobStatusResponse
from app.core.config import settings, dynamodb_client, sqs_client, logger

# 1. Define the Context Variable. This is safe across async boundaries!
correlation_id_ctx: ContextVar[str] = ContextVar("correlation_id", default="unknown")

app = FastAPI(
    title="OrchestraAI API",
    description="Asynchronous AI Batch Processing Pipeline",
    version="1.0.0"
)

@app.middleware("http")
async def add_structured_logging_and_correlation_id(request: Request, call_next):
    """
    Injects a correlation ID into every request so we can track a single user action.
    Uses ContextVar to be safe in Python's async event loop.
    """
    # Use provided ID or generate a new UUID
    req_id = request.headers.get("x-correlation-id", str(uuid.uuid4()))
    
    # Set the variable for this specific async task execution
    token = correlation_id_ctx.set(req_id)
    
    start_time = time.time()
    
    try:
        response = await call_next(request)
        
        process_time = (time.time() - start_time) * 1000
        logger.info(
            json.dumps({
                "correlation_id": req_id, # Log the ID
                "method": request.method,
                "path": request.url.path,
                "status_code": response.status_code,
                "duration_ms": round(process_time, 2)
            })
        )
        
        # Return it to the client so they can open support tickets with this ID
        response.headers["X-Correlation-ID"] = req_id
        return response
    finally:
        # CRITICAL: Reset the context to prevent memory leaks
        correlation_id_ctx.reset(token)

@app.get("/health")
def health_check():
    return {"status": "healthy", "environment": settings.ENVIRONMENT}

@app.post("/api/v1/jobs", response_model=JobResponse, status_code=202)
def submit_job(job: JobRequest, x_idempotency_key: str = Header(None)):
    """
    Submit a text payload for asynchronous AI processing.
    Returns immediately with a job_id.
    """
    # 1. Idempotency Check (If key is provided)
    if x_idempotency_key:
        try:
            response = dynamodb_client.query(
                TableName=settings.JOBS_TABLE,
                IndexName="IdempotencyKeyIndex",
                KeyConditionExpression="idempotency_key = :k",
                ExpressionAttributeValues={":k": {"S": x_idempotency_key}}
            )
            if response.get("Items"):
                existing_job_id = response["Items"][0]["job_id"]["S"]
                logger.info(f"Idempotency hit for key {x_idempotency_key}. Returning existing job {existing_job_id}")
                return JSONResponse(
                    status_code=200, 
                    content={"job_id": existing_job_id, "status": JobStatus.PENDING.value, "message": "Idempotency key recognized. Job already in queue."}
                )
        except Exception as e:
            logger.error(f"Error checking idempotency: {str(e)}")
            # We don't fail the request if the GSI read fails, we just proceed.

    # 2. Generate State
    job_id = str(uuid.uuid4())
    timestamp = str(int(time.time()))
    
        # 3. Save Initial State to DynamoDB
    try:
        current_correlation_id = correlation_id_ctx.get()
        item = {
            "job_id": {"S": job_id},
            "status": {"S": JobStatus.PENDING.value},
            "created_at": {"S": timestamp},
            "updated_at": {"S": timestamp},
            "model_profile": {"S": job.model_profile.value},
            "correlation_id": {"S": current_correlation_id} # Stored for end-to-end tracing
        }
        
        if x_idempotency_key:
            item["idempotency_key"] = {"S": x_idempotency_key}
            
        dynamodb_client.put_item(
            TableName=settings.JOBS_TABLE,
            Item=item
        )
    except Exception as e:
        logger.error(f"DynamoDB PutItem Failed: {str(e)}")
        raise HTTPException(status_code=500, detail="Failed to initialize job state")

        # 4. Enqueue the work to SQS
    try:
        # We grab the correlation ID from the ContextVar to pass it down the pipeline
        current_correlation_id = correlation_id_ctx.get()
        
        sqs_payload = {
            "job_id": job_id,
            "text_payload": job.text_payload,
            "model_profile": job.model_profile.value,
            "extraction_schema": job.extraction_schema,
            "correlation_id": current_correlation_id # PASSED TO WORKER HERE
        }
        
        sqs_client.send_message(
            QueueUrl=settings.SQS_QUEUE_URL,
            MessageBody=json.dumps(sqs_payload),
            # In a real system, you might also use MessageAttributes for the correlation ID
        )
    except Exception as e:
        logger.error(f"SQS SendMessage Failed: {str(e)}")
        # If queuing fails, we should ideally mark the DB record as FAILED, but for now we throw 500.
        raise HTTPException(status_code=500, detail="Failed to enqueue job for processing")

    # 5. Return immediately (Fast API response, happy client)
    return JSONResponse(
        status_code=202,
        content={"job_id": job_id, "status": JobStatus.PENDING.value, "message": "Job accepted and queued for processing"}
    )

@app.get("/api/v1/jobs/{job_id}", response_model=JobStatusResponse)
def get_job_status(job_id: str):
    """
    Client polls this endpoint to get the status of their AI extraction.
    """
    try:
        response = dynamodb_client.get_item(
            TableName=settings.JOBS_TABLE,
            Key={"job_id": {"S": job_id}}
        )
    except Exception as e:
        logger.error(f"DynamoDB GetItem Failed: {str(e)}")
        raise HTTPException(status_code=500, detail="Database connection error")

    item = response.get("Item")
    if not item:
        raise HTTPException(status_code=404, detail="Job not found")

    # Map DynamoDB item back to our Pydantic response schema
    result_data = None
    if "result" in item:
        result_data = json.loads(item["result"]["S"])
        
    metrics_data = None
    if "metrics" in item:
         metrics_data = json.loads(item["metrics"]["S"])

    return {
        "job_id": item["job_id"]["S"],
        "status": item["status"]["S"],
        "created_at": item["created_at"]["S"],
        "updated_at": item["updated_at"]["S"],
        "result": result_data,
        "error_reason": item.get("error_reason", {}).get("S"),
        "metrics": metrics_data
    }

# This wraps the FastAPI app so it can be invoked by AWS Lambda / API Gateway.
handler = Mangum(app, lifespan="off")