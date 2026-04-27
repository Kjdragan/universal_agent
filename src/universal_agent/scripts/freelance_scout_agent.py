"""Freelance Opportunity Scout Agent.

Runs on a schedule (every 6 hours) to:
- Search Upwork, Freelancer.com, n8n Community, OnlineJobs.ph for AI automation, n8n, and agent development opportunities
- Filter for postings less than 24 hours old
- Score by win probability x revenue / effort
- Deliver top 5 opportunities via AgentMail digest to Kevin

Output: work_products/freelance_opportunities_{date}.md
Email: kevinjdragan@gmail.com
"""

import asyncio
from datetime import datetime, timezone
import json
import logging
import os
from pathlib import Path
import sys
import uuid

from universal_agent.infisical_loader import initialize_runtime_secrets

logger = logging.getLogger(__name__)

# Target email for digests
RECIPIENT_EMAIL = "kevinjdragan@gmail.com"

# Search configuration
SEARCH_PLATFORMS = [
    "Upwork",
    "Freelancer.com",
    "n8n Community",
    "OnlineJobs.ph",
]

SEARCH_KEYWORDS = [
    "n8n automation",
    "AI agent development",
    "LLM integration",
    "Claude API",
    "OpenAI automation",
    "workflow automation",
    "AI automation",
    "agent development",
    "autonomous agents",
    "RAG implementation",
]


async def main():
    # 1. Initialize runtime secrets via Infisical (allowing dotenv fallback)
    initialize_runtime_secrets(profile="local_workstation")
    logging.basicConfig(level=logging.INFO)

    artifacts_dir = os.getenv("UA_ARTIFACTS_DIR", "").strip() or "/home/kjdragan/lrepos/universal_agent/artifacts"
    work_products_dir = Path("/home/kjdragan/lrepos/universal_agent/work_products")
    work_products_dir.mkdir(parents=True, exist_ok=True)

    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    output_file = work_products_dir / f"freelance_opportunities_{today}.md"

    # Build the comprehensive search objective
    keywords_str = ", ".join(SEARCH_KEYWORDS)
    platforms_str = ", ".join(SEARCH_PLATFORMS)

    objective = f"""You are the Freelance Opportunity Scout. Your mission is to find the best freelance opportunities for Kevin.

## Search Targets
Search these platforms: {platforms_str}

## Keywords to Search
Focus on: {keywords_str}

## Your Task
1. Search each platform for opportunities matching these keywords
2. Filter to only postings from the last 24 hours (critical - freshness is key)
3. For each opportunity, gather:
   - Job title
   - Platform
   - Direct link (must be clickable)
   - Posted time
   - Estimated revenue/budget
   - Brief description

## Scoring Formula
Score each opportunity as: **win_probability x revenue / effort**

Where:
- win_probability (0.0-1.0): How well Kevin's skills match the requirements
- revenue ($): Estimated project value or hourly rate x estimated hours
- effort (1-10): Complexity and time investment required

## Kevin's Strengths (for win probability assessment)
- n8n workflow automation expert
- AI/LLM integration specialist (Claude, OpenAI)
- Python backend development
- Agent architecture and autonomous systems
- RAG implementations
- API integrations and webhooks

## Output Requirements
1. Write a markdown report to: {output_file}

2. Include the TOP 5 opportunities sorted by score with:
   - Rank and score
   - Job title (linked)
   - Platform
   - Estimated revenue
   - Win probability (with reasoning)
   - Effort level
   - 1-sentence why it's a good fit for Kevin

3. Also include a summary section with:
   - Total opportunities found
   - Platforms searched
   - Search timestamp

4. After writing the file, send an email digest to {RECIPIENT_EMAIL} with:
   - Subject: "Daily Freelance Scout Digest - {today}"
   - A brief intro
   - The top 5 opportunities in a clean, scannable format
   - Clickable links for each opportunity
   - A link to the full report file

Use the AgentMail service to send the email. If AgentMail is not available or fails, note this in your completion message.

## Important Notes
- Prioritize quality over quantity
- Be realistic about win probability
- Flag any opportunities requiring immediate action (e.g., "apply by" deadlines)
- If no good opportunities are found, report that honestly rather than forcing low-quality results
"""

    logger.info("Dispatching freelance scout mission to vp.general.primary...")

    from universal_agent.tools.vp_orchestration import dispatch_vp_mission

    try:
        await dispatch_vp_mission(
            objective=objective,
            mission_type="freelance_scout",
            idempotency_key=f"freelance-scout-{today}-{datetime.now(timezone.utc).strftime('%H')}",
            source_session_id="cron_freelance_scout",
        )
    except RuntimeError as exc:
        logger.error(f"Dispatch failed: {exc}")
        sys.exit(1)


if __name__ == "__main__":
    asyncio.run(main())
