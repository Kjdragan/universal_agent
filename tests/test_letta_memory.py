"""
Test Letta Learning SDK with a simple memory workflow.
Run: uv run python tests/test_letta_memory.py
"""

import os
from dotenv import load_dotenv

# Load environment
load_dotenv()

from agentic_learning import learning, AgenticLearning
import anthropic

AGENT_NAME = "universal_agent_test"

def test_memory_workflow():
    """Test the complete memory workflow."""
    
    print("=" * 60)
    print("Testing Letta Learning SDK Memory Workflow")
    print("=" * 60)
    
    # Initialize client
    client = AgenticLearning()
    
    # Create or get agent
    print(f"\n1. Creating/retrieving agent '{AGENT_NAME}'...")
    try:
        agent = client.agents.create(
            agent=AGENT_NAME,
            memory=["human", "system_rules", "project_context"],
            model="anthropic/claude-sonnet-4-20250514"
        )
        print(f"   ✅ Agent created: {agent.name}")
    except Exception as e:
        print(f"   ℹ️ Agent may already exist: {e}")
    
    # Setup memory blocks
    print("\n2. Setting up memory blocks...")
    try:
        client.memory.upsert(
            agent=AGENT_NAME,
            label="human",
            value="Name: Kevin\nPreferences: TypeScript, uv package manager"
        )
        client.memory.upsert(
            agent=AGENT_NAME,
            label="system_rules", 
            value="Package Manager: uv (use `uv add`, not pip)\nOS: Linux"
        )
        client.memory.upsert(
            agent=AGENT_NAME,
            label="project_context",
            value="Working on Universal Agent project with Letta memory integration."
        )
        print("   ✅ Memory blocks created/updated")
    except Exception as e:
        print(f"   ⚠️ Memory setup issue: {e}")
    
    # List memory blocks
    print("\n3. Listing memory blocks...")
    try:
        blocks = client.memory.list(agent=AGENT_NAME)
        for block in blocks:
            print(f"   📦 {block.label}: {block.value[:50]}...")
    except Exception as e:
        print(f"   ⚠️ Could not list blocks: {e}")
    
    # Test the learning() context with Anthropic
    print("\n4. Testing learning() context with Anthropic API...")
    try:
        anthropic_client = anthropic.Anthropic()
        
        with learning(agent=AGENT_NAME, memory=["human", "system_rules"]):
            response = anthropic_client.messages.create(
                model="claude-sonnet-4-20250514",
                max_tokens=500,
                messages=[
                    {"role": "user", "content": "What's my name and preferred package manager?"}
                ]
            )
            
            print(f"   ✅ Response: {response.content[0].text[:200]}...")
            
    except Exception as e:
        print(f"   ⚠️ Learning context test failed: {e}")
        import traceback
        traceback.print_exc()
    
    print("\n" + "=" * 60)
    print("Memory workflow test complete!")
    print("=" * 60)

if __name__ == "__main__":
    test_memory_workflow()
