import asyncio

from claude_agent_sdk.client import ClaudeSDKClient
from claude_agent_sdk.types import (
    AssistantMessage,
    TextBlock,
    ToolResultBlock,
    ToolUseBlock,
    UserMessage,
)


async def main():
    async with ClaudeSDKClient() as client:
        await client.query("What's 5+5? Use the python tool or evaluate it.")
        async for msg in client.receive_response():
            print(f"MSG: {type(msg)}")
            if hasattr(msg, 'content') and isinstance(msg.content, list):
                for block in msg.content:
                    if isinstance(block, TextBlock):
                        print(f"TEXT: {block.text}")
                    elif isinstance(block, ToolUseBlock):
                        print(f"TOOL: {block.name}")

if __name__ == "__main__":
    asyncio.run(main())
