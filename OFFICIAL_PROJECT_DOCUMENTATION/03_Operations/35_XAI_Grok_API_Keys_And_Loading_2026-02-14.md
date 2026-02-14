# 35. xAI/Grok API Keys and Loading (2026-02-14)

## Purpose
Prevent sidetracks caused by missing X/Grok API keys during runs (for example: `{"error":"XAI_API_KEY not found"}`).

## Key Names (Aliases)
We treat these as aliases for the same xAI key:

- Preferred in this repo: `GROK_API_KEY`
- Compatibility alias: `XAI_API_KEY`

The runtime now normalizes these automatically:

- If `GROK_API_KEY` is set and `XAI_API_KEY` is not, we set `XAI_API_KEY=GROK_API_KEY`.
- If `XAI_API_KEY` is set and `GROK_API_KEY` is not, we set `GROK_API_KEY=XAI_API_KEY`.

Implementation: `src/universal_agent/utils/env_aliases.py`

## Where Keys Live

### Local development (repo)
- Keys live in `universal_agent/.env`.
- Gateway/API processes load `.env` on startup.

### VPS deployment
- Keys live in `/opt/universal_agent/.env`.
- Services load `.env` on startup.

## Tooling Policy (Avoid Composio X/Twitter)
- Do not use Composio Twitter/X tools.
- Use Grok/xAI `x_search` evidence fetch:
  - Preferred: `mcp__internal__x_trends_posts`
  - Fallback: `.claude/skills/grok-x-trends` (`--posts-only --json`)

## Quick Verification
From the running process environment (or a shell that inherits it):

```bash
env | rg '^(GROK_API_KEY|XAI_API_KEY)='
```

Expected: at least one is set; after normalization, usually both are set.

