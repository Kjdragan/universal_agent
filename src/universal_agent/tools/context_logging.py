"""
Module for logging context gaps and offline tasks to persistent storage.
This enables "Deferred Context Gathering" and "Offline Task Injection".
"""
import json
import os
import time
from typing import List, Dict, Any, Optional
from pathlib import Path
from claude_agent_sdk import SdkMcpTool, tool

# Paths to persistent storage
PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent.parent
CONFIG_DIR = PROJECT_ROOT / "config"
GAPS_FILE = CONFIG_DIR / "context_gaps.json"
TASKS_FILE = CONFIG_DIR / "offline_tasks_queue.json"

def _ensure_config_dir():
    """Ensure the config directory exists."""
    os.makedirs(CONFIG_DIR, exist_ok=True)

def _load_json_file(filepath: Path) -> List[Dict[str, Any]]:
    """Load list from JSON file, returning empty list if file doesn't exist."""
    if not filepath.exists():
        return []
    try:
        with open(filepath, "r", encoding="utf-8") as f:
            return json.load(f)
    except json.JSONDecodeError:
        return []

def _save_json_file(filepath: Path, data: List[Dict[str, Any]]):
    """Save list to JSON file."""
    _ensure_config_dir()
    with open(filepath, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2)

@tool("log_context_gap", "Log a question or issue to be addressed in the next interview.", {
    "question": str,
    "category": str,
    "urgency": str,
    "context_source": str
})
async def log_context_gap(args: Dict[str, Any]) -> Dict[str, Any]:
    """
    Log a context gap or operational issue.
    
    Args:
        question: The specific question or issue.
        category: 'profile_context' or 'operational_issue'.
        urgency: 'deferred' or 'immediate'.
        context_source: What triggered this (e.g. 'Task #123').
    """
    entry = {
        "id": f"gap_{int(time.time())}_{os.urandom(2).hex()}",
        "timestamp": time.time(),
        "question": args["question"],
        "category": args.get("category", "profile_context"),
        "urgency": args.get("urgency", "deferred"),
        "context_source": args.get("context_source", "unknown"),
        "status": "pending"
    }
    
    gaps = _load_json_file(GAPS_FILE)
    gaps.append(entry)
    _save_json_file(GAPS_FILE, gaps)
    
    msg = f"Logged context gap: {entry['question']} (Urgency: {entry['urgency']})"
    print(f"\n[Context Logging] {msg}")
    
    response_text = msg
    if entry['urgency'] == 'immediate':
        response_text += "\n[SUGGESTION] This is marked as IMMEDIATE. Consider requesting an interview now."

    return {
        "content": [{"type": "text", "text": response_text}]
    }

def get_pending_gaps() -> List[Dict[str, Any]]:
    """
    Retrieve all pending context gaps.
    Used by the interview skill to populate its queue.
    """
    gaps = _load_json_file(GAPS_FILE)
    return [g for g in gaps if g.get("status") == "pending"]

def mark_gaps_resolved(gap_ids: List[str]):
    """
    Mark specific gaps as resolved.
    """
    gaps = _load_json_file(GAPS_FILE)
    for gap in gaps:
        if gap["id"] in gap_ids:
            gap["status"] = "resolved"
            gap["resolved_at"] = time.time()
    _save_json_file(GAPS_FILE, gaps)

def log_offline_task(task_description: str, source_interview_id: str):
    """
    Log a task for the Heartbeat System to execute offline.
    """
    entry = {
        "id": f"task_{int(time.time())}_{os.urandom(2).hex()}",
        "timestamp": time.time(),
        "description": task_description,
        "source": "interview",
        "source_id": source_interview_id,
        "status": "pending"
    }
    
    tasks = _load_json_file(TASKS_FILE)
    tasks.append(entry)
    _save_json_file(TASKS_FILE, tasks)
    print(f"\n[Context Logging] Queued offline task: {task_description}")

