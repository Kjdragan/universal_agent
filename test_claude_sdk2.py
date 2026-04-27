import asyncio
import sys

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
        await client.query("What's 5+5?")
        async for msg in client.receive_response():
            print(f"MSG: {type(msg)}")
            if hasattr(msg, 'content') and isinstance(msg.content, list):
                for i, block in enumerate(msg.content):
                    if isinstance(block, TextBlock):
                        print(f"TEXT[{i}] len: {len(block.text)} -> {repr(block.text)}")

if __name__ == "__main__":
    with open("test_out.txt", "w") as f:
        sys.stdout = f
        asyncio.run(main())
