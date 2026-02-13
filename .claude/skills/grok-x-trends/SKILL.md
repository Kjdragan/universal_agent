---
name: grok-x-trends
description: |
  Get "what's trending" on X (Twitter) for a given query using Grok/xAI's `x_search` tool via the xAI Responses API.
  Use when the user asks for trending topics, hot takes, or high-engagement posts on X about a topic, and Composio X/Twitter tooling is unavailable or unreliable.
---

# Grok X Trends (x_search)

This skill provides a repeatable workflow (and a small script) for discovering "what's trending on X" *for a specific query/topic*, using Grok via xAI's Responses API with the `x_search` tool.

## Requirements

- `.env` contains `GROK_API_KEY` (preferred). This skill also accepts `XAI_API_KEY` as a fallback.
- Network access to `https://api.x.ai/v1/responses`.
- Default model is `grok-4-1-fast` (the grok-4 family is required for `x_search`).

## Core Workflow

1. Choose a query/topic:
   - Good: `\"bitcoin ETF\"`, `\"OpenAI o4\"`, `\"Taylor Swift Grammys\"`
   - Bad: `\"what is trending\"` (too meta; you'll mostly get people talking about trends)
2. Choose a time window:
   - Default: last 1 day.
   - For fast-moving events, consider last 1-3 days, then compare.
3. Run the script to fetch high-engagement posts and an inferred "trending themes" breakdown.
4. When reporting back:
   - Include top themes with 1-3 representative post URLs each.
   - Call out uncertainty: X does not expose a canonical "Trends list" via `x_search`; this is an inference from search results.

## Script Usage

The script prints Markdown by default; pass `--json` for machine-readable output.

```bash
uv run .claude/skills/grok-x-trends/scripts/grok_x_trends.py --query "bitcoin" --days 1
```

More examples:

```bash
# Broad/global trends (inferred by sampling multiple x_search queries)
uv run .claude/skills/grok-x-trends/scripts/grok_x_trends.py --global --region US --days 1

# Deeper pull (more posts)
uv run .claude/skills/grok-x-trends/scripts/grok_x_trends.py --query "NVIDIA earnings" --days 2 --depth deep

# Exact date range (YYYY-MM-DD)
uv run .claude/skills/grok-x-trends/scripts/grok_x_trends.py --query "Super Bowl ads" --from 2026-02-10 --to 2026-02-13

# JSON output for downstream processing
uv run .claude/skills/grok-x-trends/scripts/grok_x_trends.py --query "Apple Vision Pro" --days 1 --json > /tmp/x_trends.json
```

## Output Notes

- The script returns:
  - `themes`: inferred trending themes/angles (labels + keywords + example URLs)
  - `posts`: high-engagement posts with basic engagement stats when available
- Engagement metrics may be missing depending on what `x_search` returns.
