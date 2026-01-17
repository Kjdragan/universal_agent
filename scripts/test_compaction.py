#!/usr/bin/env python3
"""
Compaction Test Script

This script runs an agent task designed to fill context and trigger compaction.
It helps us observe:
1. When auto-compaction triggers
2. What the PreCompact hook captures
3. What Claude's summary looks like post-compaction

Run with: uv run python scripts/test_compaction.py
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


async def run_context_filling_task():
    """
    Run a task that will fill context and hopefully trigger compaction.
    
    Strategy: Ask for multiple verbose research iterations that accumulate context.
    """
    from universal_agent.agent_core import UniversalAgent, get_compaction_stats
    
    # Create workspace
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    workspace = f"AGENT_RUN_WORKSPACES/compaction_test_{timestamp}"
    os.makedirs(workspace, exist_ok=True)
    
    print("=" * 70)
    print("COMPACTION TEST")
    print("=" * 70)
    print(f"Workspace: {workspace}")
    print()
    print("Goal: Fill context to trigger auto-compaction and observe behavior")
    print("-" * 70)
    print()
    
    # Initialize agent
    agent = UniversalAgent(workspace_dir=workspace)
    await agent.initialize()
    
    # The prompt is designed to generate lots of context:
    # 1. Multiple search queries → lots of results in context
    # 2. Verbose analysis → agent generates lots of text
    # 3. Multiple tool calls → accumulated tool results
    
    prompt = """
    I need you to do extensive research that will fill up your context window. 
    Please execute the following steps, being VERY verbose in your analysis:
    
    1. Search for news about "AI artificial intelligence breakthroughs 2024 2025"
    2. Search for news about "climate change policy updates 2025"  
    3. Search for news about "quantum computing advances 2025"
    4. Search for news about "space exploration missions 2025"
    5. Search for news about "renewable energy developments 2025"
    
    For EACH search result set, provide a DETAILED analysis of:
    - The main themes and patterns you observe
    - Key players and organizations mentioned
    - Important dates and timelines
    - Potential implications and future outlook
    - Connections between different topics
    
    Write extensively about each topic. The goal is to generate a lot of content.
    After analyzing all searches, write a comprehensive synthesis report covering
    all five topics and their interconnections.
    
    Be as verbose as possible in all your responses.
    """
    
    print("Sending prompt to agent...")
    print()
    
    iteration_count = 0
    last_event_type = None
    
    try:
        async for event in agent.run_query(prompt):
            # Track iterations
            if event.type.value == "iteration_end":
                iteration_count += 1
                print(f"\n[Iteration {iteration_count} complete]")
                
                # Check compaction stats after each iteration
                stats = get_compaction_stats()
                if stats["total_compactions"] > 0:
                    print(f"📦 COMPACTION HAS OCCURRED! Count: {stats['total_compactions']}")
                    for log in stats["compaction_log"]:
                        print(f"   - {log}")
            
            # Print summary of events (not full content to avoid console spam)
            if event.type.value != last_event_type:
                if event.type.value == "text":
                    text_len = len(event.data.get("text", ""))
                    print(f"[TEXT: {text_len} chars]", end=" ", flush=True)
                elif event.type.value == "tool_call":
                    tool = event.data.get("name", "unknown")
                    print(f"\n[TOOL: {tool}]", end=" ", flush=True)
                elif event.type.value == "tool_result":
                    # Use content_preview if available, or just size
                    preview = event.data.get("content_preview", "")
                    size = event.data.get("content_size", 0)
                    print(f"[RESULT: {size} chars]", end=" ", flush=True)
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
    
    # Final compaction stats
    stats = get_compaction_stats()
    print(f"\nTotal compactions during run: {stats['total_compactions']}")
    
    if stats["compaction_log"]:
        print("\nCompaction events:")
        for log in stats["compaction_log"]:
            print(f"  #{log['count']} - Trigger: {log['trigger']}")
            print(f"       Session: {log['session_id']}")
            print(f"       Time: {log['timestamp']}")
            if "transcript_size_bytes" in log:
                print(f"       Size: {log['transcript_size_bytes']:,} bytes")
            print()
    else:
        print("\nNo compaction events occurred.")
        print("The context may not have filled enough to trigger auto-compaction.")
        print("Consider running a longer or more verbose task.")
    
    print(f"\nWorkspace: {workspace}")
    print("Check workspace for transcript and trace files.")


def main():
    print("\n🧪 Starting Compaction Test\n")
    
    # Check for API key
    if not os.getenv("ANTHROPIC_AUTH_TOKEN") and not os.getenv("ANTHROPIC_API_KEY"):
        print("ERROR: No ANTHROPIC_AUTH_TOKEN or ANTHROPIC_API_KEY found")
        print("Set environment variables or use .env file")
        sys.exit(1)
    
    if not os.getenv("COMPOSIO_API_KEY"):
        print("ERROR: No COMPOSIO_API_KEY found")
        sys.exit(1)
    
    asyncio.run(run_context_filling_task())


if __name__ == "__main__":
    main()
