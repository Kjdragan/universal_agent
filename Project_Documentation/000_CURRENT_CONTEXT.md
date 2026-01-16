# 000: Current Project Context

> [!IMPORTANT]
> **For New AI Agents**: Read this document first.

**Last Updated**: 2026-01-15
**Current Focus**: Report Writer MCP Tools Pipeline (Complete) → Harness Verification

---

## 🎯 Project Overview

**Universal Agent** is a standalone agent using Claude Agent SDK with Composio Tool Router integration.

**Core Capabilities**:
- **Two-Phase Sub-Agent Architecture** (Context Refresh) for massive reports.
- **Thick Tools, Thin Agents Pattern** - Complex logic in Python MCP tools.
- **Logfire Tracing** for full observability of sub-agent spans.
- **Composio Tool Router** (500+ integrations).
- **Harness V2** for long-running, multi-phase missions.

**Main Entry Point**: `src/universal_agent/main.py`

---

## 🟢 IMPLEMENTED ARCHITECTURE: Report Generation Pipeline

### Phase 1: The Gatherer (`research-specialist`)
*   **Role:** Search, Crawl, Filter.
*   **Action:** Runs searches via Composio, crawls URLs, processes data.
*   **Output:** Creates `tasks/<id>/refined_corpus.md` and `filtered_corpus/`.

### Phase 2: The Author (`report-writer`)
*   **Role:** Synthesis & Writing using MCP Tools.
*   **Tools:** `draft_report_parallel`, `compile_report`
*   **Workflow:**
    1. Read corpus → Create `outline.json`
    2. Call `draft_report_parallel()` → Parallel section generation
    3. Call `compile_report()` → HTML assembly
*   **Output:** `work_products/report.html`

### Key Design Decision: "Thick Tools, Thin Agents"
Complex logic (parallel API calls, file assembly) lives in Python MCP tools.
Agent focuses on planning and coordination.

---

## ✅ FIXED THIS SESSION (2026-01-15)

See `061_REPORT_WRITER_MCP_TOOLS_PIPELINE.md` for full details.

| Issue | Status |
|-------|--------|
| `CURRENT_SESSION_WORKSPACE` not propagating to MCP subprocess | ✅ Fixed |
| `PROJECT_ROOT` undefined in MCP tools | ✅ Fixed |
| Report sections in wrong order | ✅ Fixed |
| Dangerous `getcwd()` fallbacks | ✅ Removed |
| Report writer prompt unclear | ✅ Rewritten |

**Test Suite:** 9 tests in `tests/test_workspace_environment.py` (all passing)

---

## 🏗️ Project Structure

```
universal_agent/
├── src/
│   ├── universal_agent/
│   │   ├── main.py                 # Core Loop + Session Setup
│   │   ├── agent_core.py           # Sub-Agent Delegation & Prompts
│   │   ├── harness/                # Verifier & Runner
│   │   └── scripts/
│   │       ├── parallel_draft.py   # Parallel section generation
│   │       └── compile_report.py   # HTML assembly
│   └── mcp_server.py               # Local MCP tools
├── .claude/
│   └── agents/
│       ├── research-specialist.md  # Gatherer Configuration
│       └── report-writer.md        # Author Configuration
├── Project_Documentation/
│   ├── 000_CURRENT_CONTEXT.md      # This file
│   ├── 030_CONTEXT_EXHAUSTION_FIX_SUMMARY.md
│   └── 061_REPORT_WRITER_MCP_TOOLS_PIPELINE.md  # NEW
├── tests/
│   └── test_workspace_environment.py  # Workspace propagation tests
└── AGENT_RUN_WORKSPACES/
    └── session_*/                  # Artifacts & Logs
```

---

## 🎯 Immediate Next Steps

1.  **Verify in Harness:** Run the report pipeline through the full harness system.
2.  **Monitor Section Ordering:** Confirm sections appear in correct order in HTML.
3.  **Expand Themes:** Add more report styling options.

---

## 🔗 Key Documentation

| Doc | Purpose |
|-----|---------|
| `061_REPORT_WRITER_MCP_TOOLS_PIPELINE.md` | This session's fixes |
| `030_CONTEXT_EXHAUSTION_FIX_SUMMARY.md` | Two-phase architecture |
| `060_ATOMIC_RESEARCH_TASKS.md` | Research pipeline design |
| `031_LONG_RUNNING_HARNESS_ARCHITECTURE.md` | Harness system |
