# OrchestraAI 🎼

**A Resilient, Serverless Multi-Model AI Orchestration Pipeline**

OrchestraAI is a multi-tenant, asynchronous API built to reliably process massive text extraction and AI batch jobs without blocking synchronous API threads. It acts as an abstraction and orchestration layer over external LLM providers (OpenAI, Anthropic), handling their inherent latency, strict rate limits, and intermittent timeouts gracefully.

This project is built as a demo — focusing on decoupling, resiliency, Infrastructure as Code (IaC), and strict failure handling.

---

## 🏗️ Architecture & System Design

Synchronously calling LLMs from an API introduces severe vulnerability to traffic spikes, upstream timeouts, and dropped connections. OrchestraAI solves this by implementing an asynchronous, queue-based worker pattern.

### The Request Lifecycle
1. **Ingestion**: Client requests are authenticated and routed via AWS API Gateway to a FastAPI handler running on an AWS Lambda function.
2. **State & Queuing**: The API synchronously generates a job state in DynamoDB, enqueues the payload into an SQS Queue, and immediately returns a `202 Accepted` with a `job_id`.
3. **Async Processing**: SQS triggers a fleet of Worker Lambdas. These workers handle the heavy lifting: fetching secrets, orchestrating external AI APIs, parsing results, and managing retries.
4. **Completion**: The worker updates the DynamoDB record with the final AI output and usage metrics, making it available for client polling or webhook dispatch.

### Architecture Diagram

```mermaid
graph TD
    Client([Client]) -->|1. HTTP POST /jobs| APIGW[AWS API Gateway]
    APIGW -->|2. Proxy Integration| FastAPILambda[FastAPI Lambda<br/>Sync Handler]
    
    FastAPILambda -->|3. Save Initial State| DDB[(Amazon DynamoDB<br/>Jobs Table)]
    FastAPILambda -->|4. Push Event| SQS[Amazon SQS<br/>Job Queue]
    
    SQS -->|5. Trigger| WorkerLambda[Worker Lambda<br/>Async Processor]
    WorkerLambda <-->|6. Fetch Config/Keys| SSM[AWS Systems Manager<br/>Parameter Store]
    WorkerLambda <-->|7. External API Calls<br/>with Backoff| AIProviders((External LLMs<br/>OpenAI / Anthropic))
    
    WorkerLambda -->|8. Update Final State & Metrics| DDB
    
    WorkerLambda -.->|On Failure| DLQ[Amazon SQS<br/>Dead Letter Queue]
    
    classDef aws fill:#FF9900,stroke:#232F3E,stroke-width:2px,color:#232F3E;
    classDef compute fill:#F58536,stroke:#232F3E,stroke-width:2px,color:white;
    classDef storage fill:#3B48CC,stroke:#232F3E,stroke-width:2px,color:white;
    
    class APIGW,SQS,DLQ,SSM aws;
    class FastAPILambda,WorkerLambda compute;
    class DDB storage;
```

---

## ⚙️ Core Engineering Principles

This system is engineered for production-grade reliability, addressing the failure modes commonly ignored in basic "AI wrapper" applications.

### 1. Idempotency Insurance
External systems inevitably experience network blips, leading to duplicate client requests. The ingestion API implements strict idempotency using DynamoDB conditional checks. If a request with an existing `idempotency_key` is received, the system returns the existing `job_id` instead of spinning up duplicate, costly downstream compute.

### 2. Resiliency & Dead Letter Queues (DLQs)
If an LLM payload is corrupted or causes repeated execution failures, it should not poison the queue or stall the system. After `maxReceiveCount` failures, SQS automatically routes the dead message to a DLQ. This allows operations teams to inspect the payload, patch the worker logic, and re-drive the messages without data loss.

### 3. Smart Rate-Limiting & Backoff
LLM APIs enforce strict Token Per Minute (TPM) and Requests Per Minute (RPM) limits. The Worker Lambda is designed to trap `HTTP 429 Too Many Requests` exceptions. Instead of failing the job, it leverages SQS visibility timeouts to return the message to the queue for a retry after an exponential backoff period.

### 4. Cost Observability
Every LLM execution costs money. The Worker Lambda strips input/output token metrics from the AI provider's response, calculates the exact cost against a predefined pricing matrix, and writes it back to DynamoDB. This enables business-level observability per tenant and per job.

### 5. Infrastructure as Code (IaC) & Least Privilege
The entire AWS footprint is codified using **Terraform**. IAM roles are strictly scoped following the principle of least privilege:
* **API Lambda**: Only possesses `dynamodb:PutItem`, `dynamodb:GetItem`, and `sqs:SendMessage`.
* **Worker Lambda**: Only possesses `dynamodb:UpdateItem`, `sqs:ReceiveMessage`, and access to specific decryption keys in SSM.

---

## 🗂️ Project Structure

```text
/
├── .github/workflows/       # CI/CD pipelines for linting, testing, and Terraform deployment
├── src/
│   ├── app/                 # FastAPI synchronous API logic (Mangum entrypoint)
│   └── workers/             # Asynchronous SQS consumer logic & AI provider abstractions
├── terraform/               # Modular IaC for AWS infrastructure
│   ├── modules/             # Segregated config for api_gateway, compute, database, and queue
│   └── main.tf              # Root orchestration
└── tests/                   # Pytest suite with mocked AWS/LLM responses
```

---

## 🚀 Deployment & Operations

OrchestraAI utilizes a fully automated CI/CD pipeline via GitHub Actions.

1. **Continuous Integration**: On every PR, the pipeline runs code formatters, security vulnerability checks, and the `pytest` suite using `moto` to mock AWS services. It also verifies `terraform plan` and uses `tfsec` to ensure infrastructure security compliance.
2. **Continuous Deployment**: Upon merging to `main`, the pipeline automatically packages the Python code into Lambda deployment artifacts and executes `terraform apply -auto-approve` to securely update the cloud environment.

### Local Development
*(Detailed setup instructions will be added as the codebase grows).*
