---
name: reddit-intel
description: |
  Fetch compact, structured Reddit evidence (top posts / engagement) and save it as an interim work product
  inside the current session workspace for downstream agent reuse.
allowed-tools: Bash
---

# Reddit Intel (Session-Captured)

Primary usage in Universal Agent runs:

- Prefer the internal MCP tool `mcp__internal__reddit_top_posts` (Composio-backed) which returns a compact JSON object and best-effort writes an interim work product under the session workspace.

## Interim Work Product Schema (Session Workspace)

Canonical location:

- `$CURRENT_SESSION_WORKSPACE/work_products/social/reddit/top_posts/<run_slug>__<YYYYMMDD_HHMMSS>/`

Files:

- `request.json`: the inputs (subreddit, t, limit, etc.)
- `result.json`: compact structured results:
  - `posts[]` items include `rank`, `title`, `score`, `num_comments`, `author`, `permalink`, `url`, `created_utc`
- `manifest.json`: minimal metadata and relative paths

## Recommended Calls

This tool call keeps the payload small and avoids needing remote parsing of large Reddit Listing JSON.

Example (weekly top posts):

```bash
# In chat: call the internal tool.
# mcp__internal__reddit_top_posts { "subreddit": "artificial", "t": "week", "limit": 10 }
```

Notes:

- The tool defaults to `save_to_workspace=true`. Set `save_to_workspace=false` only if you explicitly do not want session file outputs.
- If Reddit connectivity is not active for the current Composio user-id, you'll need to complete Composio OAuth for the `reddit` toolkit first.

