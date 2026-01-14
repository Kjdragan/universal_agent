#!/usr/bin/env python
"""
Test Script: Composio Search Tools - Mixed Task Planning

This script tests COMPOSIO_SEARCH_TOOLS ability to generate plans that include:
1. Composio-native tools (Gmail, search, calendar, etc.)
2. Non-Composio tasks described with rich detail (write poem, analyze, etc.)
3. Mixed sequences where both types are interleaved

The hypothesis: COMPOSIO_SEARCH_TOOLS can serve as a macro-level task planner,
returning structured plans even for tasks without Composio tools.

Usage:
    cd /home/kjdragan/lrepos/universal_agent
    uv run python scripts/test_composio_mixed_planning.py
"""

import json

# =============================================================================
# TEST CASE 1: Pure Composio Tasks
# =============================================================================
PURE_COMPOSIO = {
    "name": "Pure Composio Tasks",
    "description": "All tasks have Composio tool equivalents",
    "queries": [
        {
            "use_case": "Search news for recent AI developments in January 2025",
            "known_fields": "timeframe: last 7 days, topic: artificial intelligence, source: tech news"
        },
        {
            "use_case": "Search Google Scholar for academic papers on large language models",
            "known_fields": "year: 2024-2025, filter: peer-reviewed, max_results: 10"
        },
        {
            "use_case": "Send email via Gmail with research summary attached",
            "known_fields": "recipient: team@company.com, attachment: pdf file, format: professional"
        },
        {
            "use_case": "Create calendar event for team review meeting",
            "known_fields": "duration: 1 hour, attendees: research team, platform: Google Calendar"
        }
    ]
}

# =============================================================================
# TEST CASE 2: Pure Non-Composio Tasks (Creative/Analytical)
# =============================================================================
PURE_NON_COMPOSIO = {
    "name": "Pure Non-Composio Tasks (Creative/Analytical)",
    "description": "Tasks that have NO Composio tools - testing if CSTOOL handles gracefully",
    "queries": [
        {
            "use_case": "Write a professional haiku poem about artificial intelligence and consciousness",
            "known_fields": "style: traditional 5-7-5 syllable pattern, tone: philosophical, include: nature metaphor"
        },
        {
            "use_case": "Analyze the market positioning of quantum computing startups based on research data",
            "known_fields": "methodology: SWOT analysis, format: executive summary with bullet points, length: 500 words"
        },
        {
            "use_case": "Create a detailed outline for a technical blog post about transformer architectures",
            "known_fields": "sections: 5 main sections, audience: intermediate developers, include: code examples placeholder"
        },
        {
            "use_case": "Draft an investor pitch summary for a SaaS product", 
            "known_fields": "format: one-page, include: problem, solution, market size, traction, team, ask"
        }
    ]
}

# =============================================================================
# TEST CASE 3: Mixed Interleaved Tasks (The Real Test)
# =============================================================================
MIXED_INTERLEAVED = {
    "name": "Mixed Interleaved Tasks",
    "description": "Real-world workflow mixing Composio and non-Composio tasks",
    "queries": [
        # Step 1: Composio - Search for information
        {
            "use_case": "Search web for top 5 quantum computing companies and their recent funding rounds",
            "known_fields": "timeframe: 2024-2025, depth: comprehensive, include: company names, funding amounts, investors"
        },
        # Step 2: Non-Composio - Analyze (requires Claude thinking, not a tool)
        {
            "use_case": "Analyze search results and create a ranked comparison table of companies",
            "known_fields": "criteria: funding raised, technology focus, team size, market position; output: markdown table"
        },
        # Step 3: Non-Composio - Write (creative synthesis)
        {
            "use_case": "Write executive summary paragraph synthesizing the competitive landscape",
            "known_fields": "length: 200 words, tone: professional, highlight: top 3 leaders and emerging players"
        },
        # Step 4: Non-Composio - Structure document
        {
            "use_case": "Format findings into a professional market research report",
            "known_fields": "sections: intro, methodology, findings, conclusions, appendix; format: markdown"
        },
        # Step 5: Composio - Convert format
        {
            "use_case": "Convert markdown report to PDF document",
            "known_fields": "engine: browser or tool-based, output: formatted PDF with headers"
        },
        # Step 6: Composio - Deliver
        {
            "use_case": "Send final report via email to stakeholders",
            "known_fields": "recipients: team@company.com, subject: Market Research Report, attachment: pdf"
        }
    ]
}

# =============================================================================
# TEST CASE 4: Complex Multi-Phase Mission (Stress Test)
# =============================================================================
COMPLEX_MISSION = {
    "name": "Complex Multi-Phase Mission",
    "description": "A 24-hour mission with many phases - testing decomposition limits",
    "queries": [
        # Phase 1: Research
        {"use_case": "Search for breaking news on AI regulation in the US and EU", 
         "known_fields": "timeframe: last 30 days, sources: news, government sites"},
        {"use_case": "Search Google Scholar for policy papers on AI governance",
         "known_fields": "year: 2023-2025, topic: AI regulation, ethics, safety"},
        
        # Phase 2: Analysis (no tools - Claude thinking)
        {"use_case": "Compare and contrast US vs EU approaches to AI regulation",
         "known_fields": "format: structured analysis, aspects: scope, enforcement, penalties, timeline"},
        {"use_case": "Identify top 10 implications for AI startups",
         "known_fields": "categorize: legal, technical, operational, financial"},
        
        # Phase 3: Content Creation (no direct tools)
        {"use_case": "Write a detailed white paper on AI compliance strategies for startups",
         "known_fields": "length: 3000 words, sections: executive summary, regulatory landscape, compliance framework, implementation roadmap"},
        {"use_case": "Create an infographic outline showing regulatory timeline",
         "known_fields": "format: timeline with key dates, milestones, deadlines"},
        
        # Phase 4: Formatting
        {"use_case": "Format white paper with professional styling and table of contents",
         "known_fields": "style: corporate, include: page numbers, headers, citations"},
        {"use_case": "Generate PDF version of the white paper",
         "known_fields": "format: A4, margins: 1 inch, font: professional"},
        
        # Phase 5: Distribution
        {"use_case": "Upload white paper to Google Drive shared folder",
         "known_fields": "folder: Q1 Research, access: team-wide"},
        {"use_case": "Send announcement email to mailing list with Drive link",
         "known_fields": "list: stakeholders, include: executive summary preview"}
    ]
}

# =============================================================================
# TEST CASE 5: Single Complex Request (Can CSTOOL decompose?)
# =============================================================================
SINGLE_COMPLEX_REQUEST = {
    "name": "Single Complex Request",
    "description": "One massive natural language request - see if CSTOOL breaks it down",
    "queries": [
        {
            "use_case": """
Research quantum computing companies, analyze their market positioning, 
write a comprehensive report with executive summary and detailed findings,
include visual aids like comparison tables, convert to PDF format,
upload to shared Google Drive, and send notification email to the research team
with a preview of key findings.
            """.strip(),
            "known_fields": "deadline: end of day, priority: high, deliverables: PDF report, email notification"
        }
    ]
}


def print_test_case(test_case: dict):
    """Print a test case in a format ready to copy-paste."""
    print(f"\n{'='*80}")
    print(f"TEST: {test_case['name']}")
    print(f"{'='*80}")
    print(f"Description: {test_case['description']}")
    print(f"\nNumber of queries: {len(test_case['queries'])}")
    print("\n--- COMPOSIO_SEARCH_TOOLS Payload ---")
    payload = {
        "queries": test_case["queries"],
        "session": {"generate_id": True}
    }
    print(json.dumps(payload, indent=2))


def main():
    print("=" * 80)
    print("COMPOSIO SEARCH TOOLS - MIXED PLANNING TEST SUITE")
    print("=" * 80)
    
    print("""
OBJECTIVE: Test whether COMPOSIO_SEARCH_TOOLS can serve as a macro-level
task decomposition engine for our long-running harness.

KEY QUESTIONS:
1. How does it handle non-Composio tasks (creative, analytical)?
2. Does it return a structured plan even without matching tools?
3. Can it process a single complex request and break it down?
4. What's the quality of tool recommendations vs. "no tool needed" markers?

HOW TO RUN THESE TESTS:
1. Start the Universal Agent in interactive mode
2. Copy-paste each payload below as a COMPOSIO_SEARCH_TOOLS call
3. Analyze the response for plan quality
""")
    
    # Print all test cases
    test_cases = [
        PURE_COMPOSIO,
        PURE_NON_COMPOSIO,
        MIXED_INTERLEAVED,
        COMPLEX_MISSION,
        SINGLE_COMPLEX_REQUEST
    ]
    
    for tc in test_cases:
        print_test_case(tc)
    
    print("\n" + "=" * 80)
    print("EVALUATION CRITERIA")
    print("=" * 80)
    print("""
For each test response, evaluate:

1. STRUCTURE
   - Does it return a list of items matching the queries?
   - Are they in sequential order (respecting dependencies)?

2. TOOL MATCHING
   - For Composio tasks: Does it identify correct tool slugs?
   - For non-Composio tasks: Does it indicate "no tool" or provide guidance?

3. ACTIONABILITY  
   - Can we use this output as a task queue for the harness?
   - Is there enough detail to construct agent prompts?

4. EDGE CASES
   - Single complex request: Does it decompose or treat as one item?
   - Mixed sequences: Does it maintain logical ordering?
""")


if __name__ == "__main__":
    main()
