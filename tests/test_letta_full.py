"""
Test Letta Learning SDK with proper environment setup.
Tests memory persistence and the learning() context.

Run: uv run python tests/test_letta_full.py
"""

import os
from dotenv import load_dotenv

# Load environment FIRST
load_dotenv()

import os
import sys

repo_root = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
if repo_root not in sys.path:
    sys.path.insert(0, repo_root)

import sitecustomize  # noqa: F401

# Check Anthropic configuration
print("=" * 60)
print("Environment Check")
print("=" * 60)
print(f"LETTA_API_KEY: {'✅ Set' if os.getenv('LETTA_API_KEY') else '❌ Missing'}")
print(f"ANTHROPIC_API_KEY: {'✅ Set' if os.getenv('ANTHROPIC_API_KEY') else '❌ Missing'}")
print(f"ANTHROPIC_BASE_URL: {os.getenv('ANTHROPIC_BASE_URL', 'default')}")
print()

from agentic_learning import learning, AgenticLearning
import anthropic

AGENT_NAME = "test_memory_agent"

def test_1_create_agent():
    """Test 1: Create or retrieve agent."""
    print("=" * 60)
    print("Test 1: Create/Retrieve Agent")
    print("=" * 60)
    
    client = AgenticLearning()
    
    try:
        # Try to retrieve first
        agent = client.agents.retrieve(AGENT_NAME)
        if agent:
            print(f"✅ Agent exists: {agent.name}")
            return True
    except Exception:
        pass
    
    # Create new agent
    try:
        agent = client.agents.create(
            agent=AGENT_NAME,
            memory=["human", "preferences"],
            model="anthropic/claude-sonnet-4-20250514"
        )
        print(f"✅ Agent created: {agent.name}")
        return True
    except Exception as e:
        print(f"❌ Failed to create agent: {e}")
        return False


def test_2_memory_blocks():
    """Test 2: Create and read memory blocks."""
    print("\n" + "=" * 60)
    print("Test 2: Memory Block Operations")
    print("=" * 60)
    
    client = AgenticLearning()
    
    # Test memory create/upsert
    try:
        # Create a block
        block = client.memory.upsert(
            agent=AGENT_NAME,
            label="human",
            value="Name: Kevin\nRole: Developer\nPreferences: TypeScript, uv"
        )
        print(f"✅ Memory upsert succeeded")
    except Exception as e:
        print(f"⚠️ Memory upsert issue: {e}")
    
    # Read memory blocks
    try:
        blocks = client.memory.list(agent=AGENT_NAME)
        print(f"📋 Found {len(blocks)} memory blocks:")
        for block in blocks:
            preview = block.value[:60].replace('\n', ' ') if block.value else "(empty)"
            print(f"   - {block.label}: {preview}...")
        return True
    except Exception as e:
        print(f"❌ Failed to list blocks: {e}")
        return False


def test_3_learning_context():
    """Test 3: Test learning() context with Anthropic API."""
    print("\n" + "=" * 60)
    print("Test 3: Learning Context with Anthropic")
    print("=" * 60)
    
    # Create Anthropic client (will use env vars for base_url if set)
    anthropic_client = anthropic.Anthropic()
    
    print(f"Anthropic client base_url: {anthropic_client.base_url}")
    
    try:
        # First call - provide information
        print("\n📤 Sending: 'My favorite color is blue and I work at TechCorp'")
        
        with learning(agent=AGENT_NAME, memory=["human", "preferences"]):
            response1 = anthropic_client.messages.create(
                model="claude-sonnet-4-20250514",
                max_tokens=300,
                messages=[
                    {"role": "user", "content": "My favorite color is blue and I work at TechCorp. Please remember this."}
                ]
            )
            print(f"📥 Response: {response1.content[0].text[:150]}...")
        
        print("\n✅ First call succeeded")
        
        # Second call - test retrieval
        print("\n📤 Sending: 'What's my favorite color and where do I work?'")
        
        with learning(agent=AGENT_NAME, memory=["human", "preferences"]):
            response2 = anthropic_client.messages.create(
                model="claude-sonnet-4-20250514",
                max_tokens=300,
                messages=[
                    {"role": "user", "content": "What's my favorite color and where do I work?"}
                ]
            )
            print(f"📥 Response: {response2.content[0].text[:200]}...")
        
        # Check if it remembered
        response_text = response2.content[0].text.lower()
        if "blue" in response_text or "techcorp" in response_text:
            print("\n✅ Memory retrieval WORKED - agent remembered info!")
            return True
        else:
            print("\n⚠️ Memory retrieval may not have worked - check response")
            return False
            
    except Exception as e:
        print(f"\n❌ Learning context test failed: {e}")
        import traceback
        traceback.print_exc()
        return False


def test_4_message_history():
    """Test 4: Check message history in Letta."""
    print("\n" + "=" * 60)
    print("Test 4: Message History")
    print("=" * 60)
    
    client = AgenticLearning()
    
    try:
        messages = client.messages.list(agent=AGENT_NAME)
        print(f"📜 Found {len(messages)} messages in history")
        
        # Show last few messages
        for msg in messages[-4:]:
            role = msg.role if hasattr(msg, 'role') else 'unknown'
            content = str(msg.content)[:80] if hasattr(msg, 'content') else str(msg)[:80]
            print(f"   [{role}]: {content}...")
        
        return True
    except Exception as e:
        print(f"❌ Failed to list messages: {e}")
        return False


def main():
    print("\n" + "=" * 60)
    print("LETTA LEARNING SDK FULL TEST SUITE")
    print("=" * 60 + "\n")
    
    results = []
    
    results.append(("Create Agent", test_1_create_agent()))
    results.append(("Memory Blocks", test_2_memory_blocks()))
    results.append(("Learning Context", test_3_learning_context()))
    results.append(("Message History", test_4_message_history()))
    
    print("\n" + "=" * 60)
    print("TEST RESULTS SUMMARY")
    print("=" * 60)
    
    passed = 0
    for name, result in results:
        status = "✅ PASS" if result else "❌ FAIL"
        print(f"  {status}: {name}")
        if result:
            passed += 1
    
    print(f"\n{passed}/{len(results)} tests passed")
    print("=" * 60)


if __name__ == "__main__":
    main()
