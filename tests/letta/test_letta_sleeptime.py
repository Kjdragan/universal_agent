"""
Test Letta Sleeptime Agent trigger.
Run: uv run python tests/test_letta_sleeptime.py
"""

import os
from dotenv import load_dotenv
load_dotenv()

from agentic_learning import AgenticLearning

AGENT_NAME = "test_flow_agent"

def main():
    print("=" * 60)
    print("Letta Sleeptime Agent Test")
    print("=" * 60)
    
    client = AgenticLearning()
    
    # Check if sleeptime API exists
    print("\n1. Exploring client API...")
    print(f"   Client type: {type(client)}")
    print(f"   Client attributes: {dir(client)}")
    
    # Check agents API
    print("\n2. Checking agents.sleeptime API...")
    if hasattr(client, 'agents'):
        print(f"   client.agents attributes: {[a for a in dir(client.agents) if not a.startswith('_')]}")
        
        if hasattr(client.agents, 'sleeptime'):
            print("   ✅ sleeptime API exists!")
            sleeptime = client.agents.sleeptime
            print(f"   sleeptime attributes: {[a for a in dir(sleeptime) if not a.startswith('_')]}")
            
            # Try to trigger sleeptime
            print("\n3. Trying to trigger sleeptime...")
            try:
                result = sleeptime.trigger(AGENT_NAME)
                print(f"   Result: {result}")
            except Exception as e:
                print(f"   Error: {e}")
            
            # Try run method if exists
            try:
                result = sleeptime.run(agent=AGENT_NAME)
                print(f"   Run result: {result}")
            except Exception as e:
                print(f"   Run error: {e}")
    
    # Check memory state
    print("\n4. Current memory state...")
    blocks = client.memory.list(agent=AGENT_NAME)
    for block in blocks:
        value = block.value if hasattr(block, 'value') else str(block)
        print(f"   [{block.label}]: {value[:120]}...")
    
    # Check if there's a way to run memory update explicitly
    print("\n5. Checking memory API for update methods...")
    if hasattr(client, 'memory'):
        print(f"   memory attributes: {[a for a in dir(client.memory) if not a.startswith('_')]}")
        
        if hasattr(client.memory, 'context'):
            print(f"   memory.context attributes: {[a for a in dir(client.memory.context) if not a.startswith('_')]}")


if __name__ == "__main__":
    main()
