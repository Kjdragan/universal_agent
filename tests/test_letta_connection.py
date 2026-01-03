"""
Test Letta Learning SDK connection.
Run: uv run python tests/test_letta_connection.py
"""

import os
from dotenv import load_dotenv

# Load environment
load_dotenv()

# Verify API key is set
api_key = os.getenv("LETTA_API_KEY")
project_id = os.getenv("LETTA_PROJECT_ID") or os.getenv("lETTA_PROJECT_ID")

print(f"LETTA_API_KEY: {'✅ Set' if api_key else '❌ Missing'}")
print(f"LETTA_PROJECT_ID: {'✅ Set' if project_id else '❌ Missing'}")

if not api_key:
    print("\n❌ LETTA_API_KEY not found in environment!")
    exit(1)

# Test connection
from agentic_learning import AgenticLearning

try:
    client = AgenticLearning()
    print("\n✅ Letta client initialized successfully!")
    
    # List existing agents
    agents = client.agents.list()
    print(f"📋 Found {len(agents)} existing agents")
    
    for agent in agents[:5]:  # Show first 5
        print(f"   - {agent.name}")
    
    print("\n✅ Letta connection test PASSED!")
    
except Exception as e:
    print(f"\n❌ Letta connection failed: {e}")
    import traceback
    traceback.print_exc()
    exit(1)
