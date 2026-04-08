import asyncio
from claude_agent_sdk.client import ClaudeSDKClient
from claude_agent_sdk.types import AssistantMessage, UserMessage, ToolUseBlock, ToolResultBlock, TextBlock
import sys

async def main():
    async with ClaudeSDKClient() as client:
        await client.query("Run 'uptime' command in bash via tools to see how long I've been running.")
        async for msg in client.receive_response():
            print(f"MSG: {type(msg)}")
            if hasattr(msg, 'content') and isinstance(msg.content, list):
                for i, block in enumerate(msg.content):
                    if isinstance(block, TextBlock):
                        print(f"TEXT[{i}] len: {len(block.text)} -> {repr(block.text)}")
                    elif isinstance(block, ToolUseBlock):
                        print(f"TOOL[{i}]: {block.name}")

if __name__ == "__main__":
    with open("test_out_tools.txt", "w") as f:
        sys.stdout = f
        asyncio.run(main())
