# 004: Hooks Architecture - Computation vs Reasoning

## The Core Insight

Efficient agent systems separate **deterministic computation** from **LLM reasoning**. Tasks that can be described as pure functions (same input → same output) should execute as code, not consume LLM tokens.

| Task Type | Execute As | Examples |
|-----------|------------|----------|
| **Deterministic** | Python hook | Data cleaning, formatting, caching, validation |
| **Reasoning** | LLM inference | Decision-making, synthesis, creative generation |

---

## Composio Hooks

Composio provides three decorator-based hooks that intercept tool execution:

### `@before_execute`
Runs **before** the tool executes. Use for:
- Input validation
- Argument injection
- Rate limiting
- Logging/audit

```python
from composio import before_execute

@before_execute(tools=["GMAIL_SEND_EMAIL"])
def audit_emails(tool: str, toolkit: str, request: dict) -> dict:
    print(f"[AUDIT] Sending email to: {request['arguments']['recipient']}")
    return request
```

### `@after_execute`
Runs **after** the tool executes. Use for:
- Response transformation
- Data enrichment
- Artifact saving
- Error normalization

```python
from composio import after_execute

@after_execute(tools=["COMPOSIO_SEARCH_NEWS"])
def clean_and_save(tool: str, toolkit: str, result: dict) -> dict:
    if result["successful"]:
        # Transform
        cleaned = [{"title": a["title"], "url": a["link"]} 
                   for a in result["data"]["news_results"]]
        
        # Save artifact
        with open(f"serp_{datetime.now():%H%M%S}.json", "w") as f:
            json.dump(cleaned, f)
        
        result["data"] = cleaned
    return result
```

### `@schema_modifier`
Modifies tool schemas before they're presented to the agent.

---

## Filtering Hooks

Hooks can be scoped to specific tools or toolkits:

```python
@after_execute()                                    # ALL tools
@after_execute(tools=["GMAIL_SEND_EMAIL"])          # Specific tool
@after_execute(toolkits=["gmail", "outlook"])       # Multiple toolkits
```

---

## Performance Impact

| Approach | Latency | Token Cost | Reliability |
|----------|---------|------------|-------------|
| **Hook (Python)** | ~10 ms | 0 | 100% deterministic |
| **Agent inference** | 2-5 seconds | ~1,000 | ~95% |
| **Agent + Code Interpreter** | 5-8 seconds | ~2,000+ | ~90% |

The hook approach is **500-800x faster** for deterministic tasks.

---

## Claude Agent SDK Hooks

The Claude Agent SDK also supports hooks, but they operate **within the agentic loop**:

```python
from claude_agent_sdk import ClaudeSDKClient

options = ClaudeAgentOptions(
    hooks={
        "on_tool_start": my_before_handler,
        "on_tool_end": my_after_handler,
        "on_message": my_message_handler
    }
)
```

### Key Difference

| Aspect | Composio Hooks | Claude SDK Hooks |
|--------|----------------|------------------|
| **Layer** | Tool execution layer | Agent conversation layer |
| **When** | Before/after API calls | Before/after agent messages |
| **Affects** | Tool inputs/outputs only | Full conversation context |
| **Agent awareness** | Agent sees transformed data | Agent sees hook effects |

Both can perform transformations, but:
- **Composio hooks**: Transform data *before* it enters the agent's context
- **Claude SDK hooks**: Transform data *within* the agent's context window

---

## Practical Examples

### 1. Response Compression
Reduce token usage by keeping only essential fields:
```python
@after_execute(tools=["GITHUB_LIST_REPOS"])
def compress_repos(tool, toolkit, result):
    result["data"] = [{"name": r["name"], "url": r["html_url"]} 
                      for r in result["data"]["repositories"][:20]]
    return result
```

### 2. Auto-Pagination
Fetch all pages without agent involvement:
```python
@after_execute(tools=["SLACK_LIST_CHANNELS"])
def auto_paginate(tool, toolkit, result):
    channels = result["data"]["channels"]
    cursor = result["data"].get("next_cursor")
    while cursor:
        page = fetch_next_page(cursor)
        channels.extend(page["channels"])
        cursor = page.get("next_cursor")
    result["data"]["channels"] = channels
    return result
```

### 3. Caching
Avoid redundant API calls:
```python
CACHE = {}

@before_execute(tools=["COMPOSIO_SEARCH_WEB"])
def check_cache(tool, toolkit, request):
    key = request["arguments"]["query"]
    if key in CACHE and (time.time() - CACHE[key]["time"]) < 300:
        return {"_cached": True, "data": CACHE[key]["data"]}
    return request
```

### 4. Guardrails
Block dangerous operations:
```python
@before_execute(tools=["STRIPE_CREATE_CHARGE"])
def validate_charge(tool, toolkit, request):
    if request["arguments"]["amount"] > 10000:
        raise Exception("Charges over $100 require approval")
    return request
```

---

## MCP Mode: When Hooks Don't Work

> [!IMPORTANT]
> Composio hooks (`@before_execute`, `@after_execute`) only work in **Native Tool Mode**. 
> In **MCP Mode** (using `session.mcp.url`), tool execution happens on the remote Composio server, bypassing local Python hooks entirely.

| Mode | Hooks Fire? | How Tools Execute |
|------|-------------|-------------------|
| **Native Tool Mode** (`session.tools()`) | ✅ Yes | Local SDK executes via `handle_tool_calls()` |
| **MCP Mode** (`session.mcp.url`) | ❌ No | Remote server executes, bypasses local SDK |

---

## Observer Pattern (MCP Mode Alternative)

For MCP mode, use the **Observer Pattern** to process tool results after they return:

```python
async def observe_and_save_search_results(tool_name: str, content: str, workspace_dir: str):
    """Observer: Process tool results asynchronously after they return."""
    # Check if this is a SERP result
    if "news_results" not in content and "organic_results" not in content:
        return
    
    # Parse and clean the data
    data = json.loads(content)
    cleaned = {...}  # Transform data
    
    # Save artifact
    with open(f"{workspace_dir}/search_results/{tool_name}_{timestamp}.json", "w") as f:
        json.dump(cleaned, f)

# In your conversation loop:
async for msg in client.receive_response():
    if isinstance(msg, ToolResultBlock):
        # Fire-and-forget async save (zero latency impact)
        asyncio.create_task(
            observe_and_save_search_results(tool_name, content, workspace_dir)
        )
```

**Key characteristics**:
- Zero latency impact (async, non-blocking)
- Works with MCP mode
- Claude still sees raw data (no context reduction)
- Useful for artifact saving, logging, analytics

---

## Design Principle

```
┌─────────────────────────────────────────────────────┐
│  DETERMINISTIC LAYER (Hooks)                        │
│  Transform • Validate • Cache • Log • Save          │
│  Cost: Milliseconds, zero tokens                    │
└─────────────────────────────────────────────────────┘
                         │
                         ▼
┌─────────────────────────────────────────────────────┐
│  REASONING LAYER (Agent)                            │
│  Decide • Synthesize • Judge • Create               │
│  Cost: Seconds, thousands of tokens                 │
└─────────────────────────────────────────────────────┘
```

**Rule**: If the operation is a pure function, use a hook. If it requires understanding, use the agent.
