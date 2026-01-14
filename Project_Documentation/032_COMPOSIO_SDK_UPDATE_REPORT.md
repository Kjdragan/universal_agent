# 032: Composio SDK Update Report

**Date:** January 13, 2026  
**Research Scope:** Changes since December 15, 2025  
**Current SDK Version:** `>=0.10.1,<1.0.0`  
**Latest SDK Version:** `0.10.6`

---

## Executive Summary

The Composio SDK has had significant updates since December 15, 2025, including:

1. **New Claude Agent SDK Provider** - Native integration for Claude Code Agents (PR #2285)
2. **Tool Router Enhancements** - Better session management and modifiers
3. **File Handling Flag** - `auto_upload_download_files` option
4. **Deprecated Toolkits** - Several toolkits removed

> [!IMPORTANT]
> The releases are **backward compatible** - no breaking changes requiring immediate migration. However, the **Claude Agent SDK Provider** is potentially game-changing for our implementation.

---

## Release Timeline

| Version | Date | Key Changes |
|---------|------|-------------|
| 0.10.1 | ~Dec 15 | Baseline (our current minimum) |
| 0.10.4 | Dec 22-27 | Claude Agent SDK Provider, Tool Router improvements, deprecated toolkits |
| 0.10.5 | Jan 6-9 | Tool Router session API, `auto_upload_download_files`, performance fixes |
| 0.10.6 | Jan 10-13 | Cloudflare Workers support, webhook verification, docs updates |

---

## Detailed Changes

### 1. 🆕 Claude Agent SDK Provider (v0.10.4)

**PR:** [#2285](https://github.com/ComposioHQ/composio/pull/2285) (Dec 17, 2025)

This is the most significant change for our project. Composio now has a **native provider for Claude Code Agents** (the SDK we use).

**What it means:**
- Instead of our current MCP-based integration, we could potentially use Composio's native provider
- May simplify our tooling integration
- Could provide better type safety and error handling

**Current approach (MCP HTTP):**
```python
mcp_servers={
    "composio": {
        "type": "http",
        "url": session.mcp.url,
        "headers": {"x-api-key": os.environ["COMPOSIO_API_KEY"]},
    }
}
```

**New native approach (potential):**
```python
# TBD - need to investigate the exact API
from composio import ClaudeCodeAgentProvider
provider = ClaudeCodeAgentProvider(...)
```

> [!CAUTION]
> **Investigation Required:** We need to verify if this provider offers advantages over our MCP integration before adopting it.

---

### 2. Tool Router Session Improvements (v0.10.4-0.10.5)

#### a) `waitForConnections` Property
Allows sessions to wait for users to complete authentication before proceeding.

```python
session = tool_router.create(
    user_id="user_123",
    manage_connections={
        "enable": True,
        "callback_url": "https://example.com/callback",
        "wait_for_connections": True  # NEW
    }
)
```

**Impact:** Could improve our auth flow by blocking until OAuth is complete.

#### b) Session-Specific Modifier Types
New types for better session-based tool execution:
- `SessionExecuteMetaModifiers`
- `SessionMetaToolOptions`

```python
from composio.core.models import before_execute_meta, after_execute_meta

@before_execute_meta
def before_modifier(tool, toolkit, session_id, params):
    return params

@after_execute_meta
def after_modifier(tool, toolkit, session_id, response):
    return response

tools = session.tools(modifiers=[before_modifier, after_modifier])
```

**Impact:** This is similar to our PreToolUse/PostToolUse hooks. Could consolidate hook logic.

#### c) `getRawToolRouterMetaTools` Method
Dedicated method for fetching meta tools directly from a tool router session.

```python
meta_tools = tools_model.get_raw_tool_router_meta_tools(
    session_id="session_123",
    modifiers=[schema_modifier]
)
```

---

### 3. `auto_upload_download_files` Flag (v0.10.5-0.10.6)

**PR:** [#2334](https://github.com/ComposioHQ/composio/pull/2334)

New boolean flag for `Composio()` initialization:

```python
composio = Composio(
    api_key=...,
    file_download_dir=...,
    auto_upload_download_files=True  # NEW
)
```

**Impact:** Could simplify our file handling for tool executions that involve file attachments.

---

### 4. Deprecated/Removed Toolkits (v0.10.4)

Several toolkits have been deprecated:
- Changelogs added for `is_local_toolkit` deprecation
- `is_local` field removed
- Some empty toolkits deprecated

**Our relevant toolkits:**
- `firecrawl` - We already disable this
- `exa` - We already disable this

**Impact:** Low - we're already excluding the problematic toolkits.

---

### 5. Dedicated Tool Router API Endpoint (v0.10.5)

**PR:** [#2368](https://github.com/ComposioHQ/composio/pull/2368)

Tool router sessions now fetch tools directly from the session API endpoint instead of using tool slugs.

**Impact:** Performance improvement - fewer API calls, better consistency.

---

### 6. MCP API Key Enforcement (v0.10.4)

**PR:** [#2315](https://github.com/ComposioHQ/composio/pull/2315)

MCP endpoints now enforce API key authentication more strictly.

**Impact:** Our current implementation already passes the API key in headers, so no changes needed.

---

## Impact Analysis on Our Implementation

### Current Integration Points

| Component | File | Method | Impact |
|-----------|------|--------|--------|
| Client init | `main.py:5635` | `Composio(api_key=..., file_download_dir=...)` | Add `auto_upload_download_files` flag |
| Session create | `main.py:5654` | `composio.create(user_id=..., toolkits=...)` | Can add `wait_for_connections` |
| MCP setup | `agent_core.py:798` | HTTP MCP server config | Consider native provider |
| Discovery | `composio_discovery.py` | `connected_accounts.list()` | No changes needed |
| Tool hooks | `agent_core.py` | PreToolUse/PostToolUse | Could use session modifiers |

### Recommendations

| Priority | Change | Effort | Benefit |
|----------|--------|--------|---------|
| **HIGH** | Investigate Claude Agent SDK Provider | Medium | Potential simplification |
| **MEDIUM** | Add `auto_upload_download_files=True` | Low | Better file handling |
| **MEDIUM** | Add `wait_for_connections` for auth | Low | Better auth flow |
| **LOW** | Migrate hooks to session modifiers | Medium | Consistency with SDK |
| **LOW** | Pin to `>=0.10.6` | Trivial | Get latest fixes |

---

## Questions for Investigation

1. **Claude Agent SDK Provider:**
   - What's the exact API?
   - Does it support MCP tools?
   - Does it replace or complement our current setup?

2. **Session Modifiers:**
   - Can they replace our PreToolUse/PostToolUse hooks?
   - Are they more reliable than MCP hooks?

3. **File Handling:**
   - What does `auto_upload_download_files` actually do?
   - Does it help with Gmail attachments?

---

## Next Steps

1. **Update pyproject.toml** to allow `>=0.10.6`
2. **Test basic compatibility** with current code
3. **Research Claude Agent SDK Provider** for potential adoption
4. **Create implementation plan** for SDK integration improvements

---

## 🚀 NEW: `composio-claude-code-agents` Package

**Discovery:** There is now a **dedicated provider package** for Claude Code Agents!

**Installation:**
```bash
pip install composio-claude-code-agents==0.10.2
```

**New Provider Pattern:**
```python
from composio import Composio
from composio_claude_code_agents import ClaudeCodeAgentsProvider

# Initialize with the Claude Code Agents Provider
composio = Composio(provider=ClaudeCodeAgentsProvider())

user_id = "user@acme.org"
tools = composio.tools.get(user_id=user_id, toolkits=["HACKERNEWS"])

# These tools integrate directly with Claude Code Agent SDK
```

> [!IMPORTANT]
> This provider pattern significantly simplifies integration. Instead of manually setting up MCP HTTP servers, the provider handles the tool schema and execution natively.

**Impact on Our System:**
- Could replace our manual MCP HTTP configuration
- Native type safety for Claude Agent SDK
- Better error handling at the SDK level
- Potentially simpler hook integration

---

## References

- [Composio Releases](https://github.com/ComposioHQ/composio/releases)
- [PR #2285: Claude Code Agents Provider](https://github.com/ComposioHQ/composio/pull/2285)
- [PR #2334: auto_upload_download_files](https://github.com/ComposioHQ/composio/pull/2334)
- [v0.10.4...v0.10.6 Changelog](https://github.com/ComposioHQ/composio/compare/v0.10.4...v0.10.6)
