# Composio Tool Router Integration Guide
**Document ID:** 002  
**Date:** 2026-01-22  
**Status:** Source of Truth

---

## Executive Summary

This document explains how the Universal Agent integrates with Composio's **Tool Router** via an **MCP (Model Context Protocol) server**. It clarifies the role of **meta-tools** like `COMPOSIO_SEARCH_TOOLS` and `COMPOSIO_MULTI_EXECUTE_TOOL`, and documents a previous misdiagnosis that should not be repeated.

---

## 1. Architecture Overview

```
┌─────────────────────────────────────────────────────────────────────┐
│                        Claude Agent SDK                             │
│  (claude-agent-sdk / ClaudeSDKClient)                               │
└───────────────────────────────┬─────────────────────────────────────┘
                                │
                                ▼
┌─────────────────────────────────────────────────────────────────────┐
│                     MCP Server Configuration                        │
│  (ClaudeAgentOptions.mcp_servers)                                   │
│                                                                     │
│  ┌──────────────────┐  ┌───────────────────┐  ┌──────────────────┐ │
│  │  composio (HTTP) │  │ local_toolkit     │  │ edgartools, etc. │ │
│  │  session.mcp.url │  │ (stdio)           │  │ (stdio)          │ │
│  └────────┬─────────┘  └───────────────────┘  └──────────────────┘ │
└───────────┼─────────────────────────────────────────────────────────┘
            │
            ▼
┌─────────────────────────────────────────────────────────────────────┐
│                   Composio Tool Router                              │
│  (Cloud-hosted MCP server at session.mcp.url)                       │
│                                                                     │
│  ┌─────────────────────────┐  ┌────────────────────────────────┐   │
│  │ COMPOSIO_SEARCH_TOOLS   │  │ COMPOSIO_MULTI_EXECUTE_TOOL    │   │
│  │ (Meta-tool for search)  │  │ (Meta-tool for batch execute)  │   │
│  └─────────────────────────┘  └────────────────────────────────┘   │
│                                                                     │
│  ┌──────────────────────────────────────────────────────────────┐  │
│  │ Actual Tools: GMAIL_SEND_EMAIL, GITHUB_CREATE_ISSUE, etc.    │  │
│  └──────────────────────────────────────────────────────────────┘  │
└─────────────────────────────────────────────────────────────────────┘
```

---

## 2. Meta-Tools Explained

The Composio Tool Router provides **meta-tools** that help the agent discover and execute the underlying atomic tools.

### 2.1 `COMPOSIO_SEARCH_TOOLS`

**Purpose:** Semantic search to find the right tool for a task.

**How it works:**
1. Agent calls `COMPOSIO_SEARCH_TOOLS` with a natural language `use_case` query.
2. The Tool Router searches its index of 1000+ integrations.
3. Returns a `recommended_plan_steps` and the relevant tool schemas.

**Example from run log:**
```json
{
  "queries": [
    {
      "use_case": "send an email to someone",
      "known_fields": "recipient_name: me, subject: Russia-Ukraine War Report, attachment: PDF report"
    }
  ],
  "session": { "generate_id": true }
}
```

### 2.2 `COMPOSIO_MULTI_EXECUTE_TOOL`

**Purpose:** Execute one or more tools in a single batched call.

**How it works:**
1. Agent provides a list of `tools`, each with `tool_slug` and `arguments`.
2. Tool Router executes them sequentially, handling auth automatically.
3. Returns aggregated results.

**Example from run log:**
```json
{
  "tools": [
    {
      "tool_slug": "GMAIL_SEND_EMAIL",
      "arguments": {
        "recipient_email": "me",
        "subject": "Russia-Ukraine War Report",
        "body": "...",
        "attachment": {
          "name": "report.pdf",
          "mimetype": "application/pdf",
          "s3key": "..."
        }
      }
    }
  ],
  "session_id": "duck",
  "current_step": "SENDING_EMAIL_WITH_REPORT_ATTACHMENT"
}
```

**Result:**
```json
{
  "successful": true,
  "data": {
    "results": [
      {
        "response": {
          "successful": true,
          "data": { "id": "19be73ee770f0aae", "labelIds": ["SENT", "INBOX"] }
        },
        "tool_slug": "GMAIL_SEND_EMAIL"
      }
    ],
    "success_count": 1
  }
}
```

---

## 3. How Our Agent Uses This

### Initialization (`agent_setup.py`)

```python
# Create Tool Router session
self._composio = Composio(api_key=os.environ.get("COMPOSIO_API_KEY", ""))
self._session = await self._composio.create(user_id=self.user_id, ...)

# Configure MCP server in ClaudeAgentOptions
mcp_servers = {
    "composio": {
        "type": "http",
        "url": self._session.mcp.url,
        "headers": {"x-api-key": os.environ.get("COMPOSIO_API_KEY", "")},
    },
    # ... other MCP servers
}
```

### Runtime Flow

1. **User Query:** "Send me a report on Russia-Ukraine war"
2. **Agent calls** `COMPOSIO_SEARCH_TOOLS` to find `GMAIL_SEND_EMAIL`
3. **Agent prepares** attachment via `upload_to_composio` (local toolkit)
4. **Agent calls** `COMPOSIO_MULTI_EXECUTE_TOOL` with the email parameters
5. **Email sent** ✅

---

## 4. What I Got Wrong (Lessons Learned)

### The Misdiagnosis

On 2026-01-22, I incorrectly diagnosed `COMPOSIO_MULTI_EXECUTE_TOOL` as a **hallucinated tool** and removed references to it from prompts/hooks.

### Why I Was Wrong

1. **I searched the local Python SDK** for the tool definition and didn't find it.
2. **Incorrect conclusion:** "The tool doesn't exist, the agent is hallucinating."
3. **Reality:** Meta-tools are **dynamically served by the API**, not hardcoded in the SDK.

### The Actual Problem

The agent was failing because of a **completely different issue** (likely API connectivity, session setup, or a transient error), NOT because the tool didn't exist.

### How to Avoid This Mistake

1. **Check the run logs first.** If the tool appears in successful tool calls (as it did in the user's latest run), it exists.
2. **Meta-tools are API-side.** Don't expect to find them in `pip show composio` output.
3. **Use Logfire traces** to verify tool availability at runtime.

---

## 5. Known Issues (Directory Duplication)

### Problem

Files are being saved to two locations:
- **Root:** `/home/kjdragan/lrepos/universal_agent/tasks/`
- **Session:** `/AGENT_RUN_WORKSPACES/session_XXX/tasks/`

### Root Cause

The agent (or sub-agent) is passing the **wrong `session_dir`** to `crawl_parallel` and `finalize_research`. From the run log:

```json
{
  "session_dir": "/home/kjdragan/lrepos/universal_agent",  // ❌ WRONG
  "urls": [...]
}
```

Should be:
```json
{
  "session_dir": "/home/kjdragan/lrepos/universal_agent/AGENT_RUN_WORKSPACES/session_20260122_134107",  // ✅ CORRECT
}
```

### Fix Required

The system prompt or sub-agent delegation must ensure `CURRENT_SESSION_WORKSPACE` is correctly passed. See separate fix task.

---

## 6. Quick Reference

| Tool | Type | Purpose |
|------|------|---------|
| `COMPOSIO_SEARCH_TOOLS` | Meta | Find correct tool for a use case |
| `COMPOSIO_MULTI_EXECUTE_TOOL` | Meta | Batch execute one or more tools |
| `GMAIL_SEND_EMAIL` | Atomic | Send email via Gmail API |
| `GITHUB_CREATE_ISSUE` | Atomic | Create GitHub issue |
| `upload_to_composio` | Local | Upload file for attachment (returns s3key) |

---

*End of Document*
