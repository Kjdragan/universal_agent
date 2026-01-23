# 000: Current Project Context & Handoff

**Date**: January 23, 2026  
**Focus**: MCP Visibility Refactor & Architectural Stabilization

---

## 🎯 Recent Session Summary

This session achieved a breakthrough in **Execution Disclosure** by migrating from subprocess-based MCP tools to native **In-Process SDK Tools**.

### 🟢 Completed Work

#### 1. "Vocal" MCP Refactor
- **Migrated `run_research_pipeline` & `crawl_parallel`**: These critical long-running tools now run inside the main process.
- **Real-Time Logging**: Implemented `mcp_log` with `UA_LOG_LEVEL` (INFO/DEBUG) control. User now receives step-by-step progress during 2+ minute operations.
- **Bridge Architecture**: Created `research_bridge.py` as the clean interface between the core algorithms and the Claude Agent SDK.

#### 2. Architectural Clarity (Source of Truth Docs)
- **008: Evolution of MCP Visibility**: Explains the shift from "Silent Subprocesses" to native tools.
- **009: MCP Visibility Architecture**: A Decision Record (ADR) on why we use both In-Process and Subprocess styles.
- **010: Clawdbot Integration Roadmap**: Records the completion of Phase 1 (Skills) and the roadmap for Phase 2 (Event Bus).

#### 3. Execution Success
- Verified a complex "Search → Research → PDF → Email" run with only **8 tool calls**.
- Confirmed "Full Disclosure" logging works exactly as expected in the terminal.

---

## ⚠️ Immediate Next Steps

### 1. Verification of UI & Harness
We need to verify if the architectural shifts (moving tools in-process, refactoring `main.py`) have impacted the other two entry points:
- **Web UI**: Check if `npm run dev` in `web-ui/` still interacts correctly with the backend.
- **URW Harness**: Verify that the long-running task wrapper still breaks down phases correctly using the improved execution engine.

### 2. Phase 2 Planning (The Event Bus)
- Decoupling `server.py` from the agent loop via an `AsyncEventEmitter`.
- Implementing formal sub-agent registration for lifecycle tracking.

---

## 🛠️ Key Files to Review

| Component | File Path |
| :--- | :--- |
| **Main Engine** | [main.py](file:///home/kjdragan/lrepos/universal_agent/src/universal_agent/main.py) |
| **In-Process Bridge** | [research_bridge.py](file:///home/kjdragan/lrepos/universal_agent/src/universal_agent/tools/research_bridge.py) |
| **MCP Definitions** | [mcp_server.py](file:///home/kjdragan/lrepos/universal_agent/src/mcp_server.py) |
| **Visibility ADR** | [009_mcp_visibility_architecture.md](file:///home/kjdragan/lrepos/universal_agent/Project_Documentation/009_mcp_visibility_architecture.md) |
| **Clawdbot Phases** | [010_clawdbot_integration_phasing.md](file:///home/kjdragan/lrepos/universal_agent/Project_Documentation/010_clawdbot_integration_phasing.md) |

---
