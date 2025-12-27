from fastapi import FastAPI, HTTPException, Request
from .models import TraceItem, FailureItem, WebhookPayload
from .logfire_reader import LogfireReader
from typing import List
import uvicorn
import os

app = FastAPI(title="LogfireFetch Service", version="0.1.0")

# Initialize Reader (Lazy load or global)
reader = None

@app.on_event("startup")
def startup_event():
    global reader
    try:
        reader = LogfireReader()
        print("LogfireReader initialized successfully.")
    except Exception as e:
        print(f"Failed to initialize LogfireReader: {e}")
        # In production we might exit, but for dev we'll allow startup so /health works
        pass

@app.get("/health")
def health_check():
    return {"status": "ok", "reader_initialized": reader is not None}

@app.get("/traces/recent", response_model=List[TraceItem])
def get_recent_traces(limit: int = 10):
    if not reader:
        raise HTTPException(status_code=503, detail="LogfireReader not initialized")
    traces = reader.get_recent_traces(limit)
    return traces

@app.get("/failures", response_model=List[FailureItem])
def get_recent_failures(limit: int = 10):
    if not reader:
        raise HTTPException(status_code=503, detail="LogfireReader not initialized")
    failures = reader.get_failures(limit)
    return failures

@app.post("/webhook/alert")
async def receive_alert(payload: Request):
    """
    Receives alerts from Logfire.
    Current Logic: Log it.
    Future Logic: Notify the 'Critic' Agent.
    """
    body = await payload.json()
    print(f"Received Webhook Alert: {body}")
    # TODO: Connect to Agent Runtime / Critic
    return {"status": "received", "payload_snippet": str(body)[:100]}

if __name__ == "__main__":
    uvicorn.run(app, host="0.0.0.0", port=8000)
