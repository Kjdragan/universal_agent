
import asyncio
import os
import sys
from pathlib import Path
import logging

# Add src to path
sys.path.append(os.path.abspath("src"))

# Setup Logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("heartbeat_test")

from universal_agent.agent_core import AgentEvent
from universal_agent.main import ClaudeSDKClient, ExecutionResult
from universal_agent.utils.message_history import MessageHistory
from universal_agent.utils.message_history import MessageHistory
from universal_agent.heartbeat_service import DEFAULT_HEARTBEAT_PROMPT

# Target Session for Context
SESSION_ID = "session_20260204_232844_10dadb8e"
WORKSPACE = Path(f"AGENT_RUN_WORKSPACES/{SESSION_ID}").resolve()

# We need to simulate the agent "waking up" in that workspace
os.environ["CURRENT_SESSION_WORKSPACE"] = str(WORKSPACE)
os.environ["CURRENT_SESSION_ID"] = SESSION_ID

async def test_brain():
    print(f"🧠 Testing Heartbeat Brain in {WORKSPACE}")
    
    # Check HEARTBEAT.md content first to know what we are testing against
    hb_path = WORKSPACE / "HEARTBEAT.md"
    if hb_path.exists():
        print(f"📄 HEARTBEAT.md:\n{hb_path.read_text()}")
    else:
        print("❌ HEARTBEAT.md NOT FOUND")
        return

    # Initialize Client
    client = ClaudeSDKClient(
        workspace_dir=str(WORKSPACE),
        session_id=SESSION_ID,
        # We want to see the tools, so we enable them
        enable_prompt_caching=False # Keep it simple
    )
    
    print("\n🚀 Sending Heartbeat Prompt to LLM...")
    print(f"❓ Prompt: {DEFAULT_HEARTBEAT_PROMPT}")
    
    # We only want one turn to see if it grabs the tool or says OK
    history = MessageHistory()
    
    try:
        # We manually drive one turn of the conversation
        sys_prompt = client._build_system_prompt()
        messages = [{"role": "user", "content": DEFAULT_HEARTBEAT_PROMPT}]
        
        # We can't easily hook into the streaming 'run_conversation' without full harness.
        # But we can call the client directly if we construct the payload.
        # Actually, let's just reuse run_conversation but patch the history to start with our prompt
        # Wait, run_conversation is a loop.
        
        # Simpler: Use the internal client.client.messages.create if possible, OR
        # Just use main.run_conversation with a 'run_once' flag if it existed.
        # Let's write a mini-loop here.
        
        print("⏳ Waiting for model response (this consumes API credits)...")
        
        # This is a raw call to see what the model WANTS to do
        response = client.client.messages.create(
            model=client.model,
            max_tokens=1024,
            system=sys_prompt,
            messages=messages,
            tools=client.tools, # Give it the tools so it can call Read/Bash
            tool_choice={"type": "auto"}
        )
        
        print("\n🤖 Model Response:")
        if response.content:
            for block in response.content:
                if block.type == "text":
                    print(f"📝 TEXT: {block.text}")
                elif block.type == "tool_use":
                    print(f"🛠️  TOOL: {block.name} ({block.input})")
        
        stop_reason = response.stop_reason
        print(f"\n🛑 Stop Reason: {stop_reason}")
        
    except Exception as e:
        print(f"❌ Error: {e}")
        import traceback
        traceback.print_exc()

if __name__ == "__main__":
    asyncio.run(test_brain())
