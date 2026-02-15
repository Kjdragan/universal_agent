
import asyncio
import sys
import os
from pathlib import Path
from dotenv import load_dotenv

# Setup paths
PROJECT_ROOT = Path("/home/kjdragan/lrepos/universal_agent")
load_dotenv(PROJECT_ROOT / ".env")

sys.path.append(str(PROJECT_ROOT / "src"))
sys.path.append(str(PROJECT_ROOT))  # Add root for Memory_System package
sys.path.append(str(PROJECT_ROOT / "skills/agent_interview"))

from claude_agent_sdk import ClaudeAgentOptions, ClaudeSDKClient, create_sdk_mcp_server
from universal_agent.utils.model_resolution import resolve_claude_code_model
from interview_tool import ask_user, finish_interview, fetch_context_gaps

# Load SKILL.md content for instructions
SKILL_MD = (PROJECT_ROOT / "skills/agent_interview/SKILL.md").read_text()

async def run_interview():
    print("🚀 Starting Daily Interview Agent...")
    
    # Create MCP server from tools
    interview_server = create_sdk_mcp_server(
        name="interview",
        version="1.0.0",
        tools=[fetch_context_gaps, ask_user, finish_interview]
    )
    
    options = ClaudeAgentOptions(
        model=resolve_claude_code_model(default="sonnet"),
        mcp_servers={"interview": interview_server},
        allowed_tools=["mcp__interview__fetch_context_gaps", "mcp__interview__ask_user", "mcp__interview__finish_interview"],
        system_prompt=f"""
You are the **Daily Interview Agent**. Your purpose is to ground the user and the agent system in the day's objectves.

{SKILL_MD}

# YOUR MISSION: EXECUTE THE STANDARD DAILY PROTOCOL

You must follow this agenda strictly. Do NOT deviate unless the user asks to skip.

## Phase 1: Goal Alignment (KICK OFF HERE)
1. "What are your goals for **Today**?"
2. "What are your goals for **This Week**?"
3. "What are your goals for **This Month**?"

## Phase 2: Gap Resolution
4. Call `fetch_context_gaps` to check for pending issues.
5. If gaps exist, ask them one by one.

## Phase 3: Open Floor
6. Ask: "Is there anything else you'd like to discuss or add to our context?"
7. Listen to the user. If they suggest new work/research, log it via `finish_interview(suggested_offline_tasks=[...])`.

## Phase 4: Finish
8. Call `finish_interview`.

REMEMBER: 
- Be concise. 
- One question at a time.
- If the user says "skip", move to the next phase immediately.
"""
    )

    async with ClaudeSDKClient(options=options) as client:
        # Initial trigger
        print("Sending initial query...")
        await client.query("Start the Daily Interview. Begin with Phase 1.")
        
        print("Waiting for response...")
        async for message in client.receive_response():
            # Check for text content
            if hasattr(message, 'content'):
                for block in message.content:
                    if hasattr(block, 'text'):
                        print(f"🤖 Agent: {block.text}")
            
            # The tools (ask_user) print their own output via the tool function.
    
    print("Stream finished.")

if __name__ == "__main__":
    try:
        asyncio.run(run_interview())
    except KeyboardInterrupt:
        print("\nInterview interrupted.")
