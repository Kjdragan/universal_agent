import asyncio
import json

from claude_agent_sdk.client import ClaudeSDKClient
from claude_agent_sdk.types import ClaudeAgentOptions


async def main():
    options = ClaudeAgentOptions(
        enable_file_checkpointing=False
    )
    async with ClaudeSDKClient(options) as client:
        await client.query("Say 'Let me invoke...' as a regular text message, AND THEN invoke the 'Bash' tool to run 'ls -la'. Both in one turn please.")
        
        async for msg in client.receive_response():
            print(f"MSG Type: {type(msg).__name__}")
            if type(msg).__name__ == "AssistantMessage":
                print(f"  Num Blocks: {len(msg.content)}")
                for block in msg.content:
                    print(f"  Block type: {type(block).__name__}")

asyncio.run(main())
