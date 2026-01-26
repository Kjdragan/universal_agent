
import asyncio
import os
import sys
from pprint import pprint

# Ensure we can import from src
sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), "../src")))

from claude_agent_sdk.client import ClaudeSDKClient
from claude_agent_sdk.types import ClaudeAgentOptions, HookMatcher

async def main():
    print("🔍 Inspecting ClaudeSDKClient Context Management...")
    
    # Minimal options
    options = ClaudeAgentOptions(
        model="claude-3-5-sonnet-20241022",
        system_prompt="You are a test agent.",
    )
    
    async with ClaudeSDKClient(options) as client:
        print("\n1. Initial State:")
        # detailed introspection of client structure
        # We look for _history, history, messages, or similar
        potential_history_attrs = [
            "history", "_history", "messages", "_messages", 
            "conversation", "_conversation", "state", "_state"
        ]
        
        found_attr = None
        for attr in dir(client):
            if any(x in attr.lower() for x in ["history", "message", "convers"]):
                val = getattr(client, attr)
                print(f"   found attribute: {attr} type={type(val)}")
                if isinstance(val, list):
                    found_attr = attr

        print("\n2. Getting Server Info...")
        try:
            info = await client.get_server_info()
            if info:
                print(f"   Server Info Type: {type(info)}")
                pprint(info)
                
                commands = info.get('commands', [])
                print(f"\n   Available Commands ({len(commands)}):")
                for cmd in commands:
                    print(f"   - {cmd}")
            else:
                print("   ❌ No server info received.")
        except Exception as e:
            print(f"   Get Server Info failed: {e}")

        print("\n3. Sending Test Query...")


if __name__ == "__main__":
    asyncio.run(main())
