#!/usr/bin/env python3
"""
Massive Compaction Test Script

Runs the user's specific massive research query to test compaction limits.
"""

import asyncio
import os
import sys
from pathlib import Path
from datetime import datetime

# Add src to path
sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from dotenv import load_dotenv
load_dotenv()


async def run_massive_task():
    """Run the massive research task."""
    from universal_agent.agent_core import UniversalAgent, get_compaction_stats
    
    # Create workspace
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    workspace = f"AGENT_RUN_WORKSPACES/massive_compaction_{timestamp}"
    os.makedirs(workspace, exist_ok=True)
    
    print("=" * 70)
    print("MASSIVE COMPACTION TEST")
    print("=" * 70)
    print(f"Workspace: {workspace}")
    print()
    
    # Initialize agent
    agent = UniversalAgent(workspace_dir=workspace)
    await agent.initialize()
    
    # User's massive query
    prompt = """Research developments in the AI Industry in 2025. 
I want an understanding of developments form multiple perspectives:
1) Major research papers released
2) Discussion of Model releases nad their key defining charecteristics and improvements over prior version
3) Leading lab activities
4) Funding activity and vlauation discussions among private company leaders
5) regulatory developments

This requires significant research. Make sure you gather at least 15 research articles for each area of query for your sourcing. 
Generate a detailed comprehensive report about the topics. 
Save that fiunal report as a pdf and then gmail it to me."""
    
    print("Sending prompt to agent...")
    print("-" * 70)
    print(prompt)
    print("-" * 70)
    print()
    
    iteration_count = 0
    last_event_type = None
    
    try:
        async for event in agent.run_query(prompt):
            # Track iterations
            if event.type.value == "iteration_end":
                iteration_count += 1
                print(f"\n[Iteration {iteration_count} complete]")
                
                # Check compaction stats
                stats = get_compaction_stats()
                if stats["total_compactions"] > 0:
                    print(f"\n📦 COMPACTION HAS OCCURRED! Count: {stats['total_compactions']}")
                    for log in stats["compaction_log"]:
                        print(f"   - {log}")
            
            # Print feedback
            if event.type.value != last_event_type:
                if event.type.value == "text":
                    text_len = len(event.data.get("text", ""))
                    print(f"[TEXT: {text_len} chars]", end=" ", flush=True)
                elif event.type.value == "tool_call":
                    tool = event.data.get("tool_name", "unknown")
                    print(f"\n[TOOL: {tool}]", end=" ", flush=True)
                elif event.type.value == "tool_result":
                    result_len = len(str(event.data.get("result", "")))
                    print(f"[RESULT: {result_len} chars]", end=" ", flush=True)
                elif event.type.value == "thinking":
                    print(f"[THINKING...]", end=" ", flush=True)
                    
                last_event_type = event.type.value
                
    except KeyboardInterrupt:
        print("\n\nTest interrupted by user")
    except Exception as e:
        print(f"\n\nError during test: {e}")
        import traceback
        traceback.print_exc()
    
    print("\n")
    print("=" * 70)
    print("TEST COMPLETE")
    print("=" * 70)
    
    # Final stats
    stats = get_compaction_stats()
    print(f"\nTotal compactions: {stats['total_compactions']}")
    
    if stats["compaction_log"]:
        print("\nCompaction events:")
        for log in stats["compaction_log"]:
            print(f"  {log}")
    else:
        print("\nNo compaction triggered.")
    
    print(f"\nWorkspace: {workspace}")


def main():
    print("\nStarting Massive Compaction Test\n")
    if not os.getenv("ANTHROPIC_AUTH_TOKEN") and not os.getenv("ANTHROPIC_API_KEY"):
        print("ERROR: No ANTHROPIC credentials found")
        sys.exit(1)
    
    asyncio.run(run_massive_task())


if __name__ == "__main__":
    main()
