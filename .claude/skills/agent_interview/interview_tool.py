import asyncio
from typing import Dict, Any, List, Optional
from claude_agent_sdk import tool
from universal_agent.api.input_bridge import request_user_input
from universal_agent.tools.context_logging import get_pending_gaps, mark_gaps_resolved, log_offline_task

@tool("fetch_context_gaps", "Fetch pending questions or issues logged for this interview.", {})
async def fetch_context_gaps(args: Dict[str, Any]) -> Dict[str, Any]:
    """
    Retrieve any pending context gaps that need to be addressed.
    Use this at the start of the interview to prioritize logged issues.
    """
    gaps = get_pending_gaps()
    if not gaps:
        return {
            "content": [{"type": "text", "text": "No pending context gaps found."}]
        }
    
    # Mark these as 'in_progress' or just return them?
    # For now, we return them. The agent should ask them.
    # We will mark them resolved when the interview finishes? 
    # Or should we mark them resolved as they are fetched? 
    # Better: The agent should list them. We can mark them resolved here given the agent *validly* retrieved them.
    # But if the agent crashes, they are lost. 
    # Let's mark them resolved here for simplicity in this MVP, 
    # assuming the agent will subsequently ask them.
    gap_ids = [g["id"] for g in gaps]
    mark_gaps_resolved(gap_ids)
    
    formatted_gaps = "\n".join([
        f"- [{g['category'].upper()}] {g['question']} (Source: {g['context_source']}, Urgency: {g['urgency']})"
        for g in gaps
    ])
    
    return {
        "content": [{
            "type": "text", 
            "text": f"Found the following pending context gaps. Please address these first:\n{formatted_gaps}"
        }]
    }

@tool("ask_user", "Ask the user a question and wait for their response.", {
    "question": str,
    "category": str,
    "options": list
})
async def ask_user(args: Dict[str, Any]) -> Dict[str, Any]:
    """
    Asks the user a question using the universal input bridge.
    """
    question = args["question"]
    category = args.get("category", "general")
    options = args.get("options", [])
    
    # Correctly handle options if passed as comma-separated string (defensive coding)
    if isinstance(options, str):
        options = [opt.strip() for opt in options.split(",") if opt.strip()]

    print(f"\n[Interview Skill] Asking: {question}")
    
    try:
        response = await request_user_input(question, category=category, options=options)
        return {
            "content": [
                {
                    "type": "text",
                    "text": f"User Answer: {response}"
                }
            ]
        }
    except Exception as e:
        return {
            "content": [
                {
                    "type": "text",
                    "text": f"Error asking user: {str(e)}"
                }
            ],
            "isError": True
        }

@tool("finish_interview", "End the interview loop.", {
    "summary": str,
    "suggested_offline_tasks": list
})
async def finish_interview(args: Dict[str, Any]) -> Dict[str, Any]:
    """
    Signal that the interview is complete.
    """
    summary = args["summary"]
    offline_tasks = args.get("suggested_offline_tasks", [])
    
    print(f"\n[Interview Skill] Interview Finished. Summary: {summary[:100]}...")
    
    # --- MEMORY PERSISTENCE ---
    try:
        from universal_agent.memory.orchestrator import get_memory_orchestrator
        broker = get_memory_orchestrator()
        
        # Save as a "daily_interview" entry
        entry = broker.write(
            content=summary,
            source="daily_interview",
            session_id=None,  # Global memory, not tied to a specific session ID
            tags=["daily_interview", "goals"],
            memory_class="long_term",
            importance=0.9
        )
        if entry:
            print("[Interview Skill] Summary saved to global memory.")
        else:
            print("[Interview Skill] Warning: Summary NOT saved (broker returned None).")
            
    except Exception as e:
        print(f"[Interview Skill] Error saving to memory: {e}")
    # --------------------------

    # Log offline tasks
    if offline_tasks:
        print(f"[Interview Skill] Logging {len(offline_tasks)} offline tasks...")
        for task in offline_tasks:
            # Handle if task is dict or str
            desc = task if isinstance(task, str) else str(task)
            log_offline_task(desc, "interview_session")

    return {
        "content": [
            {
                "type": "text",
                "text": f"Interview Complete. Summary saved to memory.\nQueued {len(offline_tasks)} offline tasks."
            }
        ]
    }
