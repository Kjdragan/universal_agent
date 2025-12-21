# 000: Current Project Context

> [!IMPORTANT]
> **For New AI Agents**: Read this document first to understand the current state of the project.
> This is a living document that tracks where we are and where we're going.

**Last Updated**: 2025-12-21 12:36 CST

---

## 🎯 Project Overview

**Universal Agent** is a standalone agent using Claude Agent SDK with Composio Tool Router integration.

**Features**:
- Claude Agent SDK for agentic workflows
- Composio Tool Router for 500+ tool integrations (Gmail, SERP, Slack, etc.)
- Logfire tracing for observability
- Automatic workspace and artifact management
- Observer pattern for async result processing

**Main Entry Point**: `src/universal_agent/main.py`

---

## 📍 Current State

### What's Working
- ✅ Claude Agent SDK with Composio MCP Tool Router
- ✅ Full tool execution flow (SERP API, Gmail, web search, etc.)
- ✅ Logfire tracing with deep links
- ✅ Session workspace creation (`AGENT_RUN_WORKSPACES/session_*/`)
- ✅ Query complexity classification (SIMPLE vs COMPLEX paths)
- ✅ Observer Pattern for async SERP artifact saving
- ✅ System prompt configured to proceed without confirmation prompts
- ✅ prompt_toolkit for better terminal input editing

### Architecture
```
User Query → ClaudeSDKClient → MCP Server (Composio Cloud) → Tool Execution
                    ↓
            Tool Results → Observer (async) → Save cleaned artifacts
                    ↓
            Claude processes → Response to user
```

### Key Insight
Composio hooks (`@before_execute`, `@after_execute`) **do not work** in MCP mode because execution happens on the remote server. We use the **Observer Pattern** instead.

---

## 🚀 Where We're Going (Next Steps)

### Immediate Priorities
1. [ ] Test and verify the agent runs correctly from new repository
2. [ ] Explore Composio **Triggers** for event-driven workflows
3. [ ] Expand Observer Pattern to other tool types beyond SERP

### Future Considerations
- [ ] Implement response compression (reduce context window usage)
- [ ] Add caching layer for repeated queries
- [ ] Explore Native Tool Mode for full hook support

---

## 📚 Required Reading Before Coding

### Critical Files (Read These First)
| Priority | File | Purpose |
|----------|------|---------|
| 1 | `docs/010_LESSONS_LEARNED.md` | Project-specific gotchas and patterns (9 lessons) |
| 2 | `docs/004_HOOKS_ARCHITECTURE.md` | Hooks, MCP mode, Observer Pattern |
| 3 | `src/universal_agent/main.py` | Main agent implementation |

---

## 🔧 Development Environment

### Running the Agent
```bash
cd /home/kjdragan/lrepos/universal_agent
uv sync
uv run src/universal_agent/main.py
```

### Required Environment Variables
Create `.env` from `.env.example`:
- `COMPOSIO_API_KEY` - Composio authentication
- `ZAI_API_KEY` - ZAI endpoint (Anthropic API emulation)
- `ZAI_BASE_URL` - `https://api.z.ai/api/anthropic`
- `LOGFIRE_TOKEN` - Logfire tracing (optional)

### Key Dependencies
- `claude-agent-sdk` - Claude agentic framework
- `composio` - Tool router SDK
- `logfire` - Observability
- `prompt-toolkit` - Better terminal input

---

## 🧠 Key Concepts

### 1. MCP Mode
We use Composio's MCP server for tool routing. Tools execute on Composio's cloud, not locally.

### 2. Observer Pattern
Since hooks don't work in MCP mode, we observe tool results after they return:
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
We use ZAI (`api.z.ai`) which emulates the Anthropic API:
```python
anthropic.Anthropic(
    api_key=os.environ["ZAI_API_KEY"],
    base_url="https://api.z.ai/api/anthropic"
)
```

---

## 📁 Project Structure

```
universal_agent/
├── src/universal_agent/
│   ├── __init__.py
│   └── main.py              # Main agent implementation
├── docs/
│   ├── 000_CURRENT_CONTEXT.md  # This file
│   ├── 004_HOOKS_ARCHITECTURE.md
│   └── 010_LESSONS_LEARNED.md
├── tests/                   # Test files
├── AGENT_RUN_WORKSPACES/    # Runtime session artifacts (gitignored)
├── pyproject.toml           # Dependencies
├── .env                     # Environment variables (gitignored)
├── .env.example             # Environment template
└── README.md
```

---

## ⚠️ Known Issues & Gotchas

1. **Hooks don't fire in MCP mode** - Use Observer Pattern instead
2. **MULTI_EXECUTE structure is deeply nested** - See Lesson 3 in lessons learned
3. **MCP content is string repr, not JSON** - Use `ast.literal_eval` to parse
4. **Agent sometimes chooses Exa over SERP** - Be explicit in prompts if needed

---

*Update this document whenever significant progress is made or context changes.*
