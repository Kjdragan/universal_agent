# 000: Current Project Context

> [!IMPORTANT]
> **For New AI Agents**: Read this document first to understand the current state of the project.
> This is a living document that tracks where we are and where we're going.

**Last Updated**: 2025-12-21 12:10 CST

---

## 🎯 Project Overview

This is a **Composio Standalone Agent** project that demonstrates:
- Claude Agent SDK integration with Composio Tool Router
- MCP (Model Context Protocol) based tool execution
- Observability via Logfire tracing
- Artifact saving and workspace management

**Main Entry Point**: `src/composio_agent/main.py`

---

## 📍 Current State

### What's Working
- ✅ Claude Agent SDK with Composio MCP Tool Router
- ✅ Full tool execution flow (SERP API, Gmail, web search, etc.)
- ✅ Logfire tracing with deep links
- ✅ Session workspace creation (`AGENT_RUN_WORKSPACES/session_*/`)
- ✅ Query complexity classification (SIMPLE vs COMPLEX paths)
- ✅ **Observer Pattern** for async SERP artifact saving
- ✅ Cleaned search results saved to `search_results/` directory
- ✅ System prompt configured to proceed without confirmation prompts

### Architecture
```
User Query → ClaudeSDKClient → MCP Server (Composio Cloud) → Tool Execution
                    ↓
            Tool Results → Observer (async) → Save cleaned artifacts
                    ↓
            Claude processes → Response to user
```

### Key Insight Discovered
Composio hooks (`@before_execute`, `@after_execute`) **do not work** in MCP mode because execution happens on the remote server. We use the **Observer Pattern** instead - processing results after they return, asynchronously.

---

## 🚀 Where We're Going (Next Steps)

### Immediate Priorities
1. [ ] Explore Composio **Triggers** for event-driven workflows
2. [ ] Consider hybrid Native/MCP mode for tools needing pre-processing
3. [ ] Expand Observer Pattern to other tool types beyond SERP

### Future Considerations
- [ ] Implement response compression hooks (reduce context window usage)
- [ ] Add caching layer for repeated queries
- [ ] Explore `composio-anthropic` Native Tool Mode for full hook support

---

## 📚 Required Reading Before Coding

### Critical Files (Read These First)
| Priority | File | Purpose |
|----------|------|---------|
| 1 | `docs/010_LESSONS_LEARNED.md` | Project-specific gotchas and patterns (9 lessons) |
| 2 | `docs/004_HOOKS_ARCHITECTURE.md` | Hooks, MCP mode, Observer Pattern |
| 3 | `src/composio_agent/main.py` | Main agent implementation |
| 4 | `docs/COMPOSIO_DOCUMENTATION/ComposioPythonSDK.md` | Composio SDK reference |

### Additional Context
| File/Folder | What's There |
|-------------|--------------|
| `tests/test_native_tool_hooks.py` | Working example of Native Mode + hooks |
| `AGENT_RUN_WORKSPACES/` | Sample session outputs |
| `pyproject.toml` | Dependencies and project config |

---

## 🔧 Development Environment

### Running the Agent
```bash
cd /home/kjdragan/lrepos/claudemultiagent/composio-standalone-example
uv run src/composio_agent/main.py
```

### Required Environment Variables
- `COMPOSIO_API_KEY` - Composio authentication
- `ZAI_API_KEY` - ZAI endpoint (Anthropic API emulation)
- `ZAI_BASE_URL` - `https://api.z.ai/api/anthropic`
- `LOGFIRE_TOKEN` - Logfire tracing

### Key Dependencies
- `claude-agent-sdk` - Claude agentic framework
- `composio` - Tool router SDK (v0.10.1)
- `composio-anthropic` - Native mode provider (for hooks)
- `logfire` - Observability

---

## 🧠 Key Concepts to Understand

### 1. MCP Mode (Current)
We use Composio's MCP server for tool routing. Tools execute on Composio's cloud, not locally.

### 2. Observer Pattern
Since hooks don't work in MCP mode, we observe tool results after they return and process them asynchronously:
```python
asyncio.create_task(observe_and_save_search_results(...))
```

### 3. Workspace Structure
Each session creates:
```
AGENT_RUN_WORKSPACES/session_YYYYMMDD_HHMMSS/
├── run.log           # Full console output
├── summary.txt       # Brief summary
├── trace.json        # Tool call/result trace
└── search_results/   # Cleaned SERP artifacts
```

### 4. ZAI Endpoint
We use ZAI (`api.z.ai`) which emulates the Anthropic API. Configure clients with:
```python
anthropic.Anthropic(
    api_key=os.environ["ZAI_API_KEY"],
    base_url="https://api.z.ai/api/anthropic"
)
```

### 5. No Confirmation Prompts
System prompt is configured to proceed without asking "Should I proceed?" for emails/actions.

---

## 📝 Recent Changes Log

| Date | Change | Files Affected |
|------|--------|----------------|
| 2025-12-21 | Added no-confirmation system prompt | `main.py` |
| 2025-12-21 | Cleaned up startup banners (removed test language) | `main.py` |
| 2025-12-21 | Implemented Observer Pattern for SERP artifacts | `main.py` |
| 2025-12-21 | Added Native Tool Mode test | `tests/test_native_tool_hooks.py` |
| 2025-12-21 | Created lessons learned doc (9 lessons) | `docs/010_LESSONS_LEARNED.md` |
| 2025-12-21 | Updated hooks documentation | `docs/004_HOOKS_ARCHITECTURE.md` |

---

## ⚠️ Known Issues & Gotchas

1. **Hooks don't fire in MCP mode** - Use Observer Pattern instead
2. **MULTI_EXECUTE structure is deeply nested** - See Lesson 3 in lessons learned
3. **MCP content is string repr, not JSON** - Use `ast.literal_eval` to parse
4. **Agent sometimes chooses Exa over SERP** - Be explicit in prompts if needed
5. **Claude may ask for confirmation** - Fixed via system prompt (Lesson 9)

---

*Update this document whenever significant progress is made or context changes.*
