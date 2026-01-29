"""
Test Letta Learning SDK - focused on understanding the memory flow.
Run: uv run python tests/test_letta_memory_flow.py
"""

import os
import time
from dotenv import load_dotenv
load_dotenv()

from agentic_learning import learning, AgenticLearning
import anthropic

AGENT_NAME = "test_flow_agent"

def main():
    print("=" * 60)
    print("Letta Learning SDK - Memory Flow Test")
    print("=" * 60)
    
    client = AgenticLearning()
    anthropic_client = anthropic.Anthropic()
    
    # Step 1: Create fresh agent
    print("\n1. Creating fresh agent...")
    try:
        # Delete existing agent first
        try:
            client.agents.delete(AGENT_NAME)
            print("   Deleted existing agent")
        except:
            pass
        
        agent = client.agents.create(
            agent=AGENT_NAME,
            memory=["human", "context"],
            model="anthropic/claude-sonnet-4-20250514"
        )
        print(f"   ✅ Created agent: {agent.name}")
    except Exception as e:
        print(f"   ⚠️ {e}")
    
    # Step 2: Check initial memory
    print("\n2. Initial memory state...")
    blocks = client.memory.list(agent=AGENT_NAME)
    for block in blocks:
        value = block.value if hasattr(block, 'value') else str(block)
        print(f"   [{block.label}]: {value[:80]}...")
    
    # Step 3: Make a call with information
    print("\n3. Sending information (favorite color = purple, job = architect)...")
    with learning(agent=AGENT_NAME, memory=["human", "context"]):
        response = anthropic_client.messages.create(
            model="claude-sonnet-4-20250514",
            max_tokens=300,
            messages=[
                {"role": "user", "content": "My favorite color is purple and I'm a software architect. Please remember these facts."}
            ]
        )
        print(f"   Response: {response.content[0].text[:150]}...")
    
    # Step 4: Wait for sleeptime processing
    print("\n4. Waiting 5 seconds for sleeptime agent to process...")
    time.sleep(5)
    
    # Step 5: Check memory after the call
    print("\n5. Memory state after call + wait...")
    blocks = client.memory.list(agent=AGENT_NAME)
    for block in blocks:
        value = block.value if hasattr(block, 'value') else str(block)
        print(f"   [{block.label}]: {value[:120]}...")
    
    # Step 6: Check message history
    print("\n6. Message history...")
    messages = client.messages.list(agent=AGENT_NAME)
    print(f"   Found {len(messages)} messages")
    for msg in messages:
        content = str(msg.content)[:60] if hasattr(msg, 'content') else str(msg)[:60]
        print(f"   - {content}...")
    
    # Step 7: Make retrieval call
    print("\n7. Testing retrieval (asking about favorite color)...")
    with learning(agent=AGENT_NAME, memory=["human", "context"]):
        response = anthropic_client.messages.create(
            model="claude-sonnet-4-20250514",
            max_tokens=300,
            messages=[
                {"role": "user", "content": "What is my favorite color and what is my job?"}
            ]
        )
        print(f"   Response: {response.content[0].text}")
    
    # Step 8: Analyze
    print("\n" + "=" * 60)
    print("ANALYSIS")
    print("=" * 60)
    response_text = response.content[0].text.lower()
    if "purple" in response_text and ("architect" in response_text or "software" in response_text):
        print("✅ SUCCESS: Agent remembered both pieces of information!")
    elif "purple" in response_text or "architect" in response_text:
        print("⚠️ PARTIAL: Agent remembered some information")
    else:
        print("❌ FAIL: Agent did not retrieve memory")
        print("   Possible reasons:")
        print("   - Sleeptime agent hasn't processed yet")
        print("   - Memory context not being injected")
        print("   - Need longer wait time")


if __name__ == "__main__":
    main()
