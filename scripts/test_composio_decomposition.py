#!/usr/bin/env python
"""
Test Script: Composio Search Tools for Task Decomposition

This script explores the limits of COMPOSIO_SEARCH_TOOLS to understand:
1. How many use cases we can batch in a single call
2. Quality of tool recommendations for complex tasks
3. Whether it can serve as a task decomposition mechanism

Usage:
    cd /home/kjdragan/lrepos/universal_agent
    uv run python scripts/test_composio_decomposition.py
"""

import asyncio
import json
import os
from typing import Any

# Simple test cases to start
SIMPLE_USE_CASES = [
    "search for news articles about AI",
    "send an email via Gmail",
    "search the web for quantum computing companies",
]

# Complex multi-phase request - can Composio break this down?
COMPLEX_USE_CASE = """
Research the top 10 quantum computing companies, gather their latest 
funding rounds, write a comprehensive market analysis report, 
convert it to PDF, and send it to the team via email.
"""

# Decomposed version - we break it down into phases
DECOMPOSED_PHASES = [
    {"use_case": "search news for quantum computing companies funding 2024-2025", 
     "known_fields": "timeframe: recent, category: technology"},
    {"use_case": "search web for company financial information and market analysis",
     "known_fields": "depth: comprehensive"},
    {"use_case": "create or write documents and reports",
     "known_fields": "format: markdown, pdf"},
    {"use_case": "convert documents to PDF format",
     "known_fields": "input: markdown, output: pdf"},
    {"use_case": "send email with file attachments",
     "known_fields": "provider: gmail, with_attachments: true"},
]

# Stress test - large batch of use cases
STRESS_TEST_USE_CASES = [
    {"use_case": "web scraping and content extraction", "known_fields": ""},
    {"use_case": "image generation and editing", "known_fields": ""},
    {"use_case": "social media posting (Twitter, LinkedIn)", "known_fields": ""},
    {"use_case": "calendar management and scheduling", "known_fields": ""},
    {"use_case": "file storage and management (Google Drive, Dropbox)", "known_fields": ""},
    {"use_case": "payment processing (Stripe)", "known_fields": ""},
    {"use_case": "CRM operations (Salesforce, HubSpot)", "known_fields": ""},
    {"use_case": "code repository operations (GitHub)", "known_fields": ""},
    {"use_case": "database queries and operations", "known_fields": ""},
    {"use_case": "translate text between languages", "known_fields": ""},
    {"use_case": "audio transcription (speech to text)", "known_fields": ""},
    {"use_case": "video processing and editing", "known_fields": ""},
    {"use_case": "send SMS messages", "known_fields": ""},
    {"use_case": "weather data retrieval", "known_fields": ""},
    {"use_case": "stock market and financial data", "known_fields": ""},
]


async def test_composio_search_tools():
    """
    This function would call COMPOSIO_SEARCH_TOOLS and analyze results.
    
    For now, we output what the test cases would be so the human
    can run them manually through the agent.
    """
    print("=" * 80)
    print("COMPOSIO SEARCH TOOLS - DECOMPOSITION TEST CASES")
    print("=" * 80)
    
    print("\n## TEST 1: Simple Use Cases (Sanity Check)")
    print("Send these queries individually to COMPOSIO_SEARCH_TOOLS:")
    for uc in SIMPLE_USE_CASES:
        print(f"  - {uc}")
    
    print("\n## TEST 2: Complex Multi-Phase Request (Raw)")
    print("Send the entire complex request as a single use_case:")
    print(f"  use_case: \"{COMPLEX_USE_CASE.strip()}\"")
    print("  known_fields: \"\"")
    print("\n  Question: Does Composio understand and return multiple relevant tools?")
    
    print("\n## TEST 3: Pre-Decomposed Phases (Recommended Approach)")
    print("Send as queries array with multiple use cases:")
    print("  COMPOSIO_SEARCH_TOOLS({queries: [")
    for i, phase in enumerate(DECOMPOSED_PHASES):
        print(f"    {i+1}. {{use_case: \"{phase['use_case']}\", known_fields: \"{phase['known_fields']}\"}}")
    print("  ]})")
    print("\n  Hypothesis: This should return structured tool recommendations per phase.")
    
    print("\n## TEST 4: Stress Test (15 Use Cases)")
    print("Send a large batch to test limits:")
    print(f"  {len(STRESS_TEST_USE_CASES)} simultaneous queries")
    print("  Checking: Rate limits, response quality, context handling")
    
    print("\n" + "=" * 80)
    print("EXPECTED OUTPUTS TO ANALYZE")
    print("=" * 80)
    
    print("""
For each test, evaluate:

1. **Tool Relevance**: Are the recommended tools appropriate for the use case?
2. **Completeness**: Does it cover all necessary steps?
3. **Pitfalls/Notes**: Does Composio provide any warnings or constraints?
4. **Schema Quality**: Are the tool schemas actionable?
5. **Response Time**: How fast is it for different batch sizes?

Key Questions:
- Can Composio replace LLM-based decomposition?
- Should we send raw complex requests or pre-decompose?
- What's the optimal batch size for queries?
""")
    
    # Build the actual test payload
    print("\n" + "=" * 80)
    print("READY-TO-RUN COMPOSIO CALLS")
    print("=" * 80)
    
    print("\n### Test 3 Payload (Copy-Paste for Agent):")
    test3_payload = {
        "queries": DECOMPOSED_PHASES,
        "session": {"generate_id": True}
    }
    print(json.dumps(test3_payload, indent=2))
    
    print("\n### Test 4 Payload (Stress Test):")
    test4_payload = {
        "queries": STRESS_TEST_USE_CASES,
        "session": {"generate_id": True}
    }
    print(json.dumps(test4_payload, indent=2))


if __name__ == "__main__":
    asyncio.run(test_composio_search_tools())
