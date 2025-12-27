from pydantic import BaseModel
from typing import Optional, Dict, Any, List

class TraceItem(BaseModel):
    span_name: str
    start_timestamp: str
    end_timestamp: Optional[str] = None
    trace_id: str
    attributes: Optional[Dict[str, Any]] = None
    level: int

class FailureItem(BaseModel):
    span_name: str
    start_timestamp: str
    trace_id: str
    exception_type: Optional[str] = None
    exception_message: Optional[str] = None
    level: int

class WebhookPayload(BaseModel):
    """
    Generic payload for Logfire Webhooks.
    We will refine this once we capture a real example.
    """
    event_type: str
    trace_id: Optional[str] = None
    data: Optional[Dict[str, Any]] = None
