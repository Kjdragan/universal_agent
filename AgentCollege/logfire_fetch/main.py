import sys
import os

# Add repo root to sys.path to allow importing src.universal_agent
repo_root = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
if repo_root not in sys.path:
    sys.path.append(repo_root)

from fastapi import FastAPI, HTTPException, Request
from .models import TraceItem, FailureItem, WebhookPayload
from .logfire_reader import LogfireReader
from typing import List
import uvicorn

# Imports for Agent College Integration
try:
    from Memory_System.manager import MemoryManager
    from src.universal_agent.agent_college.critic import CriticAgent
except ImportError as e:
    print(f"Warning: Could not import Agent College modules: {e}")
    MemoryManager = None
    CriticAgent = None

app = FastAPI(title="LogfireFetch Service", version="0.1.0")

# Initialize Reader (Lazy load or global)
reader = None
critic = None
memory_manager = None

@app.on_event("startup")
def startup_event():
    global reader, critic, memory_manager
    try:
        reader = LogfireReader()
        print("LogfireReader initialized successfully.")
        
        # Initialize Memory & Critic if available
        if MemoryManager and CriticAgent:
            # Use the SAME storage path as main.py to share the database
            storage_path = os.path.join(repo_root, "Memory_System_Data")
            memory_manager = MemoryManager(storage_dir=storage_path)
            critic = CriticAgent(memory_manager)
            print(f"Critic initialized via Shared Memory at: {storage_path}")
            
    except Exception as e:
        print(f"Failed to initialize components: {e}")
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
    Current Logic: Log it -> Critic -> Sandbox.
    """
    body = await payload.json()
    print(f"Received Webhook Alert: {body}")
    
    status = "log_only"
    if critic:
        # Extract Trace ID or meaningful info from body
        # Logfire webhook payload structure varies, simpler to dump whole thing for now
        # or Try to find 'trace_id'
        trace_id = body.get("trace_id", "unknown")
        suggestion = f"Logfire Alert: {str(body)}"
        result = critic.propose_correction(trace_id, suggestion)
        print(f"Critic Proposal: {result}")
        status = "proposed_to_sandbox"
        
    return {"status": status, "payload_snippet": str(body)[:100]}

if __name__ == "__main__":
    uvicorn.run(app, host="0.0.0.0", port=8000)
