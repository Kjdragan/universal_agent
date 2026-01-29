"""
Final verification - memory should now be retrievable.
Run: uv run python tests/test_letta_verify.py
"""

import os
from dotenv import load_dotenv
load_dotenv()

from agentic_learning import learning, AgenticLearning
import anthropic

AGENT_NAME = "test_flow_agent"

def main():
    print("=" * 60)
    print("Letta Memory Verification")
    print("=" * 60)
    
    client = AgenticLearning()
    anthropic_client = anthropic.Anthropic()
    
    # Check current memory
    print("\n1. Current memory state...")
    blocks = client.memory.list(agent=AGENT_NAME)
    for block in blocks:
        value = block.value if hasattr(block, 'value') else str(block)
        print(f"   [{block.label}]:")
        print(f"   {value}")
        print()
    
    # Now test retrieval
    print("2. Testing retrieval with learning() context...")
    with learning(agent=AGENT_NAME, memory=["human", "context"]):
        response = anthropic_client.messages.create(
            model="claude-sonnet-4-20250514",
            max_tokens=300,
            messages=[
                {"role": "user", "content": "Based on what you know about me, what's my favorite color and profession?"}
            ]
        )
        print(f"\n   Response: {response.content[0].text}")
    
    # Check if it worked
    response_text = response.content[0].text.lower()
    print("\n" + "=" * 60)
    if "purple" in response_text and ("architect" in response_text or "software" in response_text):
        print("✅ SUCCESS: Memory retrieval working!")
    else:
        print("❌ Memory not retrieved - may need more investigation")
    print("=" * 60)


if __name__ == "__main__":
    main()
