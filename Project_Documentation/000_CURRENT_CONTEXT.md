# 000: Current Project Context

> [!IMPORTANT]
> **For New AI Agents**: Read this document first.

**Last Updated**: 2026-01-13
**Current Focus**: Harness Stability & Sequential Execution

---

## 🎯 Project Overview

**Universal Agent** is a standalone agent using Claude Agent SDK with Composio Tool Router integration.

**Core Capabilities**:
- **Two-Phase Sub-Agent Architecture** (Context Refresh) for massive reports.
- **Logfire Tracing** for full observability of sub-agent spans.
- **Composio Tool Router** (500+ integrations).
- **Harness V2** for long-running, multi-phase missions (Currently Debugging).

**Main Entry Point**: `src/universal_agent/main.py`

---

## 🟢 IMPLEMENTED ARCHITECTURE: Context Refresh Strategy

We solved "Context Exhaustion" (Zero-Byte Writes) by splitting monolithic tasks into specialized sub-agents with a hard context reset.

### Phase 1: The Gatherer (`research-specialist`)
*   **Role:** Search, Crawl, Filter.
*   **Action:** Runs searches, crawls URLs, processes data.
*   **Output:** Creates `tasks/<id>/research_overview.md` and `filtered_corpus/`.
*   **Context:** Dumps all raw crawl data upon exit.

### Phase 2: The Author (`report-writer`)
*   **Role:** Synthesis & Writing.
*   **Context:** **FRESH START**.
*   **Action:** Reads `research_overview.md`, then selectively reads corpus files (RAG-style).
*   **Result:** Writes massive reports (30KB+) without context overflow.

---

## 🔴 CURRENT FOCUS: Harness Stability

The verification run (`session_...161008`) proved the agent works, but the **Harness Tooling** had some issues.

### ✅ FIXED: Stdout Race Condition
*   **Symptom**: Agent prints `<promise>TASK_COMPLETE</promise>`, but Harness reads empty string (`''`) and restarts the run.
*   **Fix (2026-01-13)**: Implemented fallback log capture in `on_agent_stop()` that reads from `run.log` when `result.response_text` is empty.

### Known Issue: Sequential Injection
*   **Symptom**: Agent sees all tasks in `mission.json` and tries to execute them in parallel.
*   **Status**: **Planned**. Need to refactor `process_harness_phase` to inject *only* the current active task.

---

## 🏗️ Project Structure

```
universal_agent/
├── src/
│   ├── universal_agent/
│   │   ├── main.py                 # Core Loop (Fixed Double Session Bug)
│   │   ├── agent_core.py           # Sub-Agent Delegation Logic
│   │   ├── harness/                # Verifier & Runner (NEEDS FIXING)
│   └── mcp_server.py
├── .claude/
│   └── agents/
│       ├── research-specialist.md  # Gatherer Configuration
│       └── report-writer.md        # Author Configuration
├── Project_Documentation/
│   ├── 030_CONTEXT_EXHAUSTION_FIX_SUMMARY.md  # Details of current architecture
│   └── 000_CURRENT_CONTEXT.md      # This file
└── AGENT_RUN_WORKSPACES/
    └── session_*/                  # Artifacts & Logs
```

---

## 🎯 Immediate Next Steps

1.  **Fix Harness Output Capture**: Debug `src/universal_agent/harness/verifier.py`.
2.  **Implement Sequential Prompting**: Update `main.py` harness loop to hide future tasks.
3.  **Investigate Tool Blocking**: Remove the "Hard Override" for `Task`/`Bash` tools by finding the root cause of the `PreToolUse` denial.
