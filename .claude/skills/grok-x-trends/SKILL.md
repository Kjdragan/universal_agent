---
name: grok-x-trends
description: |
  Get "what's trending" on X (Twitter) for a given query using Grok/xAI's `x_search` tool via the xAI Responses API.
  Use when the user asks for trending topics, hot takes, or high-engagement posts on X about a topic, and Composio X/Twitter tooling is unavailable or unreliable.
---

# Grok X Trends (x_search)

This skill provides a repeatable workflow (and a small script) for discovering "what's trending on X" *for a specific query/topic*, using Grok via xAI's Responses API with the `x_search` tool.

Key implementation detail: the script requests **native JSON output** from the Responses API (`response_format: json_object`) so downstream parsing can be done with `json.loads()` instead of regex extraction.

In Universal Agent runs, prefer calling the internal MCP tool `mcp__internal__x_trends_posts` for the same capability (evidence posts only). This script is the fallback for environments where only shell execution is available.

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
   - If results are unexpectedly empty, first try `--depth deep`. If still empty, widen the query or time window (or run `--global` to sanity-check X search is returning data at all).
5. Recommended architecture when your Primary Agent is not Grok (e.g., ZAI/Anthropic-emulated):
   - Use this skill in `--posts-only --json` mode to fetch evidence (posts).
   - Have the Primary Agent infer themes, summarize, and write the final answer using those posts as citations/evidence.

## Script Usage

The script prints Markdown by default; pass `--json` for machine-readable output.

```bash
uv run .claude/skills/grok-x-trends/scripts/grok_x_trends.py --query "bitcoin" --days 1
```

More examples:

```bash
# Broad/global trends (inferred by sampling multiple x_search queries)
uv run .claude/skills/grok-x-trends/scripts/grok_x_trends.py --global --region US --days 1

# Restrict to (or exclude) specific handles (max 10)
uv run .claude/skills/grok-x-trends/scripts/grok_x_trends.py --query "xAI" --days 2 --allow-handles "elonmusk,xai"
uv run .claude/skills/grok-x-trends/scripts/grok_x_trends.py --query "xAI" --days 2 --exclude-handles "elonmusk"

# Enable media understanding (images/videos) for richer summaries when posts include media
uv run .claude/skills/grok-x-trends/scripts/grok_x_trends.py --query "wildfire" --days 1 --image-understanding
uv run .claude/skills/grok-x-trends/scripts/grok_x_trends.py --query "game trailer" --days 1 --video-understanding

# Deeper pull (more posts)
uv run .claude/skills/grok-x-trends/scripts/grok_x_trends.py --query "NVIDIA earnings" --days 2 --depth deep

# Evidence-only mode (best when your primary LLM is not Grok)
uv run .claude/skills/grok-x-trends/scripts/grok_x_trends.py --query "OpenAI" --days 1 --depth quick --posts-only --json > /tmp/x_posts.json

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
- Spend guardrail: the script sets a depth-based `max_tool_calls` cap in the Responses API request to reduce runaway tool fan-out. If you need more coverage, prefer increasing `--days` or `--depth` rather than looping retries.
