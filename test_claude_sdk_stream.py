import asyncio
from claude_agent_sdk.client import ClaudeSDKClient
from claude_agent_sdk.types import AssistantMessage, TextBlock

async def main():
    async with ClaudeSDKClient() as client:
        await client.query("Count to 5 slowly.")
        count = 0
        async for msg in client.receive_response():
            if isinstance(msg, AssistantMessage):
                for block in msg.content:
                    if isinstance(block, TextBlock):
                        count += 1
                        print(f"CHUNK {count}: {repr(block.text)}")

if __name__ == "__main__":
    asyncio.run(main())
