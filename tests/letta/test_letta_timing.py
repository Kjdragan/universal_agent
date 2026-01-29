"""
Test Letta memory turnaround time.
Run: uv run python tests/test_letta_timing.py
"""

import os
import time
from dotenv import load_dotenv
load_dotenv()

from agentic_learning import learning, AgenticLearning
import anthropic

AGENT_NAME = "timing_test_agent"

def main():
    print("=" * 60)
    print("Letta Memory Turnaround Time Test")
    print("=" * 60)
    
    client = AgenticLearning()
    anthropic_client = anthropic.Anthropic()
    
    # Create fresh agent
    print("\n1. Creating fresh agent...")
    try:
        client.agents.delete(AGENT_NAME)
    except:
        pass
    
    agent = client.agents.create(
        agent=AGENT_NAME,
        memory=["human"],
        model="anthropic/claude-sonnet-4-20250514"
    )
    print(f"   ✅ Created: {agent.name}")
    
    # Send unique info with timestamp
    unique_info = f"favorite_number_{int(time.time())}"
    print(f"\n2. Sending unique info: {unique_info}")
    
    t_send = time.time()
    with learning(agent=AGENT_NAME, memory=["human"]):
        response = anthropic_client.messages.create(
            model="claude-sonnet-4-20250514",
            max_tokens=200,
            messages=[
                {"role": "user", "content": f"My secret code is {unique_info}. Remember this."}
            ]
        )
    t_after_send = time.time()
    print(f"   Sent in {t_after_send - t_send:.2f}s")
    
    # Poll for memory update
    print("\n3. Polling for memory update...")
    max_wait = 60
    poll_interval = 3
    elapsed = 0
    memory_updated = False
    
    while elapsed < max_wait:
        blocks = client.memory.list(agent=AGENT_NAME)
        for block in blocks:
            if unique_info in (block.value or ""):
                memory_updated = True
                break
        
        if memory_updated:
            break
        
        print(f"   {elapsed}s - memory not updated yet...")
        time.sleep(poll_interval)
        elapsed += poll_interval
    
    if memory_updated:
        print(f"\n✅ Memory updated in {elapsed}s!")
    else:
        print(f"\n⚠️ Memory not updated after {max_wait}s")
    
    # Show final memory state
    print("\n4. Final memory state:")
    blocks = client.memory.list(agent=AGENT_NAME)
    for block in blocks:
        print(f"   [{block.label}]: {block.value[:150] if block.value else '(empty)'}...")
    
    print("\n" + "=" * 60)
    print(f"TURNAROUND TIME: {elapsed}s" if memory_updated else "TIMEOUT")
    print("=" * 60)


if __name__ == "__main__":
    main()
