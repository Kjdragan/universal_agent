
import asyncio
import os
import sys
from dotenv import load_dotenv

load_dotenv()
import logging
from datetime import datetime

# Setup path
sys.path.append(os.path.join(os.getcwd(), "src"))

# Mock logic or real imports? Real imports.
try:
    from universal_agent.agent_core import UniversalAgent as ClaudeAgent, ClaudeAgentOptions
except ImportError as e:
    print(f"Import Error: {e}")
    sys.exit(1)

logging.basicConfig(level=logging.INFO)

async def test_agent():
    print("Testing Agent Initialization...")
    workspace_path = os.path.abspath("urw_sessions/test_session_init")
    os.makedirs(workspace_path, exist_ok=True)
    
    print("Creating UniversalAgent...")
    try:
        # UniversalAgent only takes workspace_dir and user_id
        # Use the ID from .env to verify auth
        user_id = os.environ.get("DEFAULT_USER_ID", "test_user")
        print(f"Using User ID: {user_id}")
        agent = ClaudeAgent(workspace_dir=workspace_path, user_id=user_id)
    except Exception as e:
        print(f"Agent Creation Failed: {e}")
        return

    print("Agent Created. Running Query...")
    try:
        # Simple query
        async for event in agent.run_query("Hello, are you working?"):
            print(f"Event: {event.type}")
            if event.type == "text":
                # Check actual attribute name for text content
                content = event.data.get("text") if hasattr(event, "data") else str(event)
                print(f"Response: {content}")
    except Exception as e:
        print(f"Query Failed: {e}")
        return

    print("Test Complete.")

if __name__ == "__main__":
    if not os.environ.get("ANTHROPIC_API_KEY"):
        print("ANTHROPIC_API_KEY not set")
    # load dotenv if needed
    from dotenv import load_dotenv
    load_dotenv()
    
    asyncio.run(test_agent())
