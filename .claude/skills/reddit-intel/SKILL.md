---
name: reddit-intel
description: >
  Fetch compact, structured Reddit intelligence — top posts with engagement metrics (score,
  comments, author, permalink) — and save as an interim work product in the current session
  workspace for downstream agent reuse.
  USE when you need to understand what the Reddit community is saying about a topic, check
  trending discussion in a subreddit, or gather social signals for a research task.
  Trigger phrases: "what's trending on Reddit", "check Reddit for", "top Reddit posts about",
  "Reddit sentiment on", "what's r/X saying about", "check r/MachineLearning", "get Reddit
  intel on", "pull Reddit data for", "what's popular in this subreddit".
allowed-tools: Bash
---

# Reddit Intel (Session-Captured)

Fetch the top posts from any subreddit with optional time windowing, automatically saving
a compact result JSON to the session workspace for downstream agent use.

---

## Quick Start

```
mcp__internal__reddit_top_posts {
  "subreddit": "artificial",
  "t": "week",
  "limit": 10
}
```

The tool auto-saves to the session workspace (`save_to_workspace=true` by default).

---

## Tool Reference: `mcp__internal__reddit_top_posts`

This is an internal MCP tool backed by Composio's `REDDIT_GET_R_TOP` action.

### Parameters

| Parameter | Type | Required | Default | Notes |
|-----------|------|----------|---------|-------|
| `subreddit` | string | ✅ | — | Name without `r/` prefix (e.g. `"artificial"`, `"MachineLearning"`) |
| `t` | string | | `"week"` | Time window: `"hour"`, `"day"`, `"week"`, `"month"`, `"year"`, `"all"` |
| `limit` | int | | `10` | Number of posts to request from API (clamped to 1–50) |
| `max_posts` | int | | = limit | Hard cap on posts returned in output (clamped to 1–50) |
| `include_nsfw` | bool | | `false` | Whether to include over-18 posts in results |
| `save_to_workspace` | bool | | `true` | Write `request.json`, `result.json`, `manifest.json` to session workspace |

### Return Shape

Returns a compact JSON object — not the raw Reddit Listing:

```json
{
  "subreddit": "artificial",
  "t": "week",
  "limit": 10,
  "after": "t3_abc123",
  "posts": [
    {
      "rank": 1,
      "id": "xyz789",
      "title": "GPT-5 announced with new reasoning capabilities",
      "score": 4821,
      "num_comments": 632,
      "author": "u/techuser42",
      "created_utc": 1709500000.0,
      "permalink": "https://www.reddit.com/r/artificial/comments/xyz789/...",
      "url": "https://openai.com/blog/gpt-5",
      "is_self": false,
      "domain": "openai.com"
    }
  ]
}
```

### Post fields

| Field | Meaning |
|-------|---------|
| `rank` | Position (1-indexed) in the sorted top list |
| `id` | Reddit post ID |
| `title` | Post title |
| `score` | Upvote score (upvotes minus downvotes) |
| `num_comments` | Total comment count |
| `author` | Reddit username (prefixed with `u/`) |
| `created_utc` | Unix timestamp of post creation (UTC) |
| `permalink` | Full `https://www.reddit.com/...` URL to the post |
| `url` | Link URL (= permalink for self-posts; external URL for link posts) |
| `is_self` | `true` = text post; `false` = link post |
| `domain` | Domain of the linked URL (or `"self.subreddit"` for text posts) |

---

## Run Workspace Output

When `save_to_workspace=true`, files are written to:

```
$CURRENT_RUN_WORKSPACE/work_products/social/reddit/top_posts/r_<subreddit>_<t>__<YYYYMMDD_HHMMSS>/
```

| File | Contents |
|------|----------|
| `request.json` | Tool name + call arguments |
| `result.json` | Compact result (same shape as return value above) |
| `manifest.json` | Metadata: domain, source, kind, relative paths, retention=session |

Downstream agents can read `result.json` directly without re-fetching.

---

## Error Conditions

| Error | Cause | Resolution |
|-------|-------|-----------|
| `error: subreddit is required` | `subreddit` param was empty | Provide a valid subreddit name |
| `error: missing COMPOSIO_API_KEY` | `COMPOSIO_API_KEY` env var not set | Verify the env var is configured in the UA runtime |
| `error: failed to execute REDDIT_GET_R_TOP via Composio` | Composio API call failed | Check connectivity; if Reddit OAuth is expired, re-authenticate |
| `error: could not parse Reddit Listing (listing_not_found)` | Unexpected Composio response shape | May indicate Reddit API rate limit or malformed response; retry |
| `error: could not parse Reddit Listing (response_not_dict)` | Composio returned non-dict response | Likely infrastructure issue; check Composio status |

### Reddit OAuth Prerequisite

The tool requires active Reddit connectivity for the current Composio user-id.

If you see a Composio execution failure mentioning OAuth or authorization:

1. The user needs to complete Composio OAuth for the `reddit` toolkit.
2. This is a one-time per-account step — once done, all future calls work without re-auth.
3. Direct the user to the Composio OAuth flow for the Reddit integration.

---

## Usage Patterns

### Trending topics in a community (default)

```
mcp__internal__reddit_top_posts { "subreddit": "MachineLearning", "t": "week", "limit": 15 }
```

### Fast pulse check (last 24 hours)

```
mcp__internal__reddit_top_posts { "subreddit": "artificial", "t": "day", "limit": 5 }
```

### Broader signal (past month)

```
mcp__internal__reddit_top_posts { "subreddit": "LocalLLaMA", "t": "month", "limit": 20 }
```

### Skip session workspace save (inline-only, no file I/O)

```
mcp__internal__reddit_top_posts { "subreddit": "programming", "t": "week", "save_to_workspace": false }
```

---

## Downstream Use

After calling the tool, subsequent agents or steps can:

1. Read `result.json` from the session workspace path without re-fetching Reddit.
2. Use the `posts[]` array for ranking, filtering by `score`, `num_comments`, or `domain`.
3. Follow `permalink` fields to read full post discussions if deeper analysis is needed.
4. Use `created_utc` timestamps to filter for posts within a specific time range.
