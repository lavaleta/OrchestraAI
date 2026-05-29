from pydantic import BaseModel, Field
from typing import Optional, Dict, Any
from enum import Enum
from uuid import UUID

class JobStatus(str, Enum):
    PENDING = "PENDING"
    PROCESSING = "PROCESSING"
    COMPLETED = "COMPLETED"
    FAILED = "FAILED"

class AIModelProfile(str, Enum):
    FAST_COST_EFFECTIVE = "fast-cost-effective" # e.g., GPT-4o-mini or Claude Haiku
    ULTRA_PREMIUM = "ultra-premium"             # e.g., GPT-4o or Claude Opus

class JobRequest(BaseModel):
    """Payload received from the client"""
    text_payload: str = Field(..., description="The raw text or document content to be processed")
    model_profile: AIModelProfile = Field(
        default=AIModelProfile.FAST_COST_EFFECTIVE, 
        description="The target AI model performance profile"
    )
    extraction_schema: Optional[Dict[str, Any]] = Field(
        default=None, 
        description="Optional JSON schema for structured data extraction"
    )

class JobResponse(BaseModel):
    """Immediate response sent back to the client"""
    job_id: str
    status: JobStatus
    message: str

class JobStatusResponse(BaseModel):
    """Response when a client polls for their job status"""
    job_id: str
    status: JobStatus
    created_at: str
    updated_at: str
    result: Optional[Dict[str, Any]] = None
    error_reason: Optional[str] = None
    metrics: Optional[Dict[str, Any]] = None # To show token usage / cost observability