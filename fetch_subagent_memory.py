import asyncio
import os
import sys

# Load .env manually to ensure keys are present
env_path = os.path.join(os.getcwd(), ".env")
if os.path.exists(env_path):
    with open(env_path, "r") as f:
        for line in f:
            line = line.strip()
            if not line or line.startswith("#"):
                continue
            if "=" in line:
                key, value = line.split("=", 1)
                # handle quotes
                value = value.strip()
                if (value.startswith('"') and value.endswith('"')) or (value.startswith("'") and value.endswith("'")):
                    value = value[1:-1]
                os.environ[key.strip()] = value

# Try to import agentic_learning
try:
    from agentic_learning import AsyncAgenticLearning
except ImportError:
    print("Error: agentic_learning not installed.")
    sys.exit(1)

async def main():
    try:
        client = AsyncAgenticLearning()
        agent_name = "universal_agent report-creation-expert"
        
        print(f"Fetching memory context for agent: '{agent_name}'...")
        
        # Ensure agent exists (optional check, but good for robustness)
        # In main.py: await _ensure_letta_agent(agent_name)
        # We'll just try to retrieve context directly.
        
        context = await client.memory.context.retrieve(agent=agent_name)
        
        print("\n=== START SUBAGENT CONTEXT ===")
        print(context)
        print("=== END SUBAGENT CONTEXT ===")
        
    except Exception as e:
        print(f"Error fetching subagent memory: {e}")

if __name__ == "__main__":
    asyncio.run(main())
