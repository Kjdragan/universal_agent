import asyncio
import sys
import time
from typing import Any
from claude_agent_sdk import tool, create_sdk_mcp_server, ClaudeAgentOptions, ClaudeSDKClient, AssistantMessage, TextBlock, ToolUseBlock

# 1. Define an in-process tool with explicit logging
@tool("simulate_long_task", "Simulates a long running task with progress updates", {"duration": int})
async def simulate_long_task(args: dict[str, Any]) -> dict[str, Any]:
    duration = args.get("duration", 5)
    print(f"\n[TOOL-STDOUT] Starting simulation for {duration} seconds...", flush=True)
    sys.stderr.write(f"[TOOL-STDERR] Starting simulation (stderr)...\n")
    sys.stderr.flush()

    for i in range(duration):
        # Sleep to simulate work
        await asyncio.sleep(1.0)
        
        # Print progress to both stdout and stderr
        print(f"[TOOL-STDOUT] Progress: Step {i+1}/{duration}", flush=True)
        sys.stderr.write(f"[TOOL-STDERR] Progress: Step {i+1}/{duration}\n")
        sys.stderr.flush()

    print("[TOOL-STDOUT] Simulation complete!", flush=True)
    return {
        "content": [{
            "type": "text",
            "text": f"Task completed after {duration} seconds."
        }]
    }

async def main():
    print("=== STARTING IN-PROCESS MCP VERIFICATION ===")
    print("You should see [TOOL-STDOUT] and [TOOL-STDERR] messages appear in REAL-TIME below.")
    print("If they appear all at once at the end, the verification failed.\n")

    # 2. Create the SDK MCP server config
    test_server = create_sdk_mcp_server(
        name="test_server",
        version="1.0.0",
        tools=[simulate_long_task]
    )

    # 3. Configure Agent Options
    # We add a stderr callback to prove we can capture it if needed, 
    # though in-process stdout/stderr should verify directly to console.
    def stderr_callback(msg):
        # reliable capture for CLI stderr, but our tool is IN-PROCESS so it might bypass this
        sys.stderr.write(f"[SDK-CALLBACK] {msg}")
        sys.stderr.flush()

    options = ClaudeAgentOptions(
        mcp_servers={"test": test_server},
        allowed_tools=["mcp__test__simulate_long_task"],
        stderr=stderr_callback,
        # minimal system prompt to save tokens/time
        system_prompt="You are a test assistant. Use the simulate_long_task tool when asked."
    )

    # 4. Run Agent
    async with ClaudeSDKClient(options=options) as client:
        print(">> Sending query to agent...")
        # We ask it to run for 5 seconds
        query_text = "Run the simulation for 5 seconds."
        
        await client.query(query_text)

        async for message in client.receive_response():
            if isinstance(message, AssistantMessage):
                for block in message.content:
                    if isinstance(block, TextBlock):
                        print(f"\n[AGENT] {block.text}")
                    elif isinstance(block, ToolUseBlock):
                        print(f"\n[AGENT] Calling Tool: {block.name}")

if __name__ == "__main__":
    asyncio.run(main())
