#!/usr/bin/env python
"""Minimal test to reproduce URW hang in isolation."""

import asyncio
import os
import sys
from pathlib import Path
from dotenv import load_dotenv

load_dotenv()

# Mimic run_urw.py imports
from anthropic import Anthropic

# Now import the agent adapter exactly as URW does
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))
from universal_agent.urw.integration import UniversalAgentAdapter


async def test_urw_adapter():
    """Test the exact flow that run_urw.py uses."""
    print("[TEST] Starting URW Adapter test...")
    
    # Create workspace like run_urw.py does
    workspace = Path("/tmp/test_urw_minimal")
    workspace.mkdir(parents=True, exist_ok=True)
    
    # Create adapter exactly as run_urw.py does
    adapter = UniversalAgentAdapter(
        {"model": os.getenv("ANTHROPIC_DEFAULT_SONNET_MODEL", "glm-5"), "verbose": True}
    )
    
    # Manually call _create_agent like execute_task does internally
    print("[TEST] Creating agent...")
    adapter._workspace_path = workspace
    agent = await adapter._create_agent()
    print(f"[TEST] Agent created: {agent}")
    
    # Now try running a minimal query
    print("[TEST] Running minimal query...")
    prompt = "Say hello in 5 words or less."
    
    from universal_agent.agent_core import EventType
    
    try:
        async for event in agent.run_query(prompt):
            print(f"[TEST EVENT] {event.type}", flush=True)
            if event.type == EventType.TEXT:
                text = event.data.get("text", "")
                if text:
                    print(f"[TEST TEXT] {text[:100]}")
        print("[TEST] Query completed successfully!")
    except Exception as e:
        print(f"[TEST ERROR] {e}")
        import traceback
        traceback.print_exc()


if __name__ == "__main__":
    print("[TEST] Launching asyncio.run()...")
    asyncio.run(test_urw_adapter())
    print("[TEST] Done.")
