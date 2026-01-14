#!/usr/bin/env python
"""
Direct Composio Search Tools Test Runner

This script calls COMPOSIO_SEARCH_TOOLS directly via the MCP server to evaluate
its planning capabilities for task decomposition.
"""

import asyncio
import json
import os
import sys

# Add src to path
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))


async def call_composio_search_tools(queries: list) -> dict:
    """Call COMPOSIO_SEARCH_TOOLS via the Composio SDK."""
    try:
        from composio import ComposioToolSet
        
        toolset = ComposioToolSet()
        
        # Call the search tools action
        result = toolset.execute_action(
            action="COMPOSIO_SEARCH_TOOLS",
            params={
                "queries": queries,
                "session": {"generate_id": True}
            }
        )
        return result
    except Exception as e:
        return {"error": str(e)}


async def run_test(name: str, queries: list):
    """Run a single test case and print results."""
    print(f"\n{'='*80}")
    print(f"TEST: {name}")
    print(f"{'='*80}")
    print(f"Queries: {len(queries)}")
    
    result = await call_composio_search_tools(queries)
    
    print("\n--- RESULT ---")
    print(json.dumps(result, indent=2, default=str)[:3000])
    if len(str(result)) > 3000:
        print("... (truncated)")
    
    return result


async def main():
    print("COMPOSIO SEARCH TOOLS - LIVE TESTING")
    print("=" * 80)
    
    # Test 1: Mixed Interleaved (most important)
    mixed_queries = [
        {
            "use_case": "Search web for top 5 quantum computing companies and their recent funding rounds",
            "known_fields": "timeframe: 2024-2025, depth: comprehensive"
        },
        {
            "use_case": "Analyze search results and create a ranked comparison table of companies",
            "known_fields": "criteria: funding raised, technology focus; output: markdown table"
        },
        {
            "use_case": "Write executive summary paragraph synthesizing the competitive landscape",
            "known_fields": "length: 200 words, tone: professional"
        },
        {
            "use_case": "Convert markdown report to PDF document",
            "known_fields": "engine: browser or tool-based"
        },
        {
            "use_case": "Send final report via email to stakeholders",
            "known_fields": "recipients: team@company.com, attachment: pdf"
        }
    ]
    
    await run_test("Mixed Interleaved (Composio + Non-Composio)", mixed_queries)
    
    # Test 2: Pure Non-Composio (creative tasks)
    creative_queries = [
        {
            "use_case": "Write a professional haiku poem about AI",
            "known_fields": "style: traditional 5-7-5, tone: philosophical"
        },
        {
            "use_case": "Analyze market positioning using SWOT framework",
            "known_fields": "format: executive summary, length: 500 words"
        }
    ]
    
    await run_test("Pure Non-Composio (Creative)", creative_queries)
    
    # Test 3: EXTREME - Vague requests
    extreme_queries = [
        {"use_case": "Go viral on social media", "known_fields": ""},
        {"use_case": "Make me rich", "known_fields": ""},
        {"use_case": "Write the next great American novel", "known_fields": ""},
        {"use_case": "Build a startup", "known_fields": ""},
    ]
    
    await run_test("EXTREME - Vague Requests (No Details)", extreme_queries)
    
    # Test 4: Single mega-request
    mega_query = [
        {
            "use_case": "Research quantum computing, write report, convert to PDF, send via email",
            "known_fields": ""
        }
    ]
    
    await run_test("Single Mega-Request (See if it decomposes)", mega_query)
    
    print("\n" + "=" * 80)
    print("TESTING COMPLETE")
    print("=" * 80)


if __name__ == "__main__":
    asyncio.run(main())
