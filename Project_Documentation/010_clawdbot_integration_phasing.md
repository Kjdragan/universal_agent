# 010: Clawdbot Integration Roadmap & Phasing

**Date:** 2026-01-23  
**Status:** In Progress / Partially Completed

## Overview

This document records the strategy for porting high-impact architectural patterns from the **Clawdbot** reference implementation into the **Universal Agent**. We categorized the work into three distinct phases.

---

## ✅ Phase 1: Skills Infrastructure (COMPLETE)

**Goal:** Transform monolithic tools into modular, declarative "Skills".

### Accomplishments:
1.  **Standardized Skill Format**: Adopted the [SKILL.md](file:///home/kjdragan/lrepos/universal_agent/.agent/workflows/skills/skill-creator/SKILL.md) structure (YAML frontmatter + Markdown instructions).
2.  **Capability Gating**: Implemented the `shutil.which` based dependency check. If a skill requires a binary (e.g., `gh`, `tmux`) that is missing from the host, the skill is automatically hidden from the agent's context.
3.  **Progressive Disclosure**: Built the loader to only inject skill names/descriptions into the initial prompt, requiring the agent to `read_file` the full documentation only when needed.
4.  **Ported Core Skills**:
    *   `github`: Managing issues/PRs.
    *   `slack/discord`: Messaging integrations.
    *   `tmux`: Long-running session management.
    *   `summarize`: Large context processing.

---

## 🕒 Phase 2: Plumbing & Event Bus (ROADMAP)

**Goal:** Decouple the UI from the Agent Logic using a "Gateway" pattern.

### Current Problem:
The `server.py` (Web UI) is tightly coupled to the execution loop. If the WebSocket disconnects, tracking the agent's internal thought state is difficult.

### Planned Objectives:
1.  **`AgentEventBus`**: Implement an internal `asyncio.Queue` based event bus. The Agent will emit events (Thought, ToolCall, ToolResult, Log) that any observer can subscribe to.
2.  **Concurrency Locking**: Port Clawdbot's "Lane" system (per-workspace queues) to ensure two sub-agents don't fight over the same file resources in parallel.
3.  **Durable Registry**: Add a `subagents` table to the SQLite database to track the lifecycle of child processes and ensure they are cleaned up or harvested upon task completion.

### Motivation:
Moving to an Event Bus allows the Universal Agent to support **Multi-Client Observation** (watching the same run from both the Terminal and a Browser) and improves resiliency against network/UI flakiness.

---

## 🚀 Phase 3: Advanced Memory (FUTURE)

**Goal:** Long-term "Coherence" across sessions.

### Planned Objectives:
1.  **Hybrid Search**: Integrate `sqlite-vec` (vector search) with SQLite FTS5 (keyword search) for better recall.
2.  **Knowledge Snapshots**: Automatically "harvest" successful research reports back into a persistent Knowledge Base that is discoverable via semantic search in future runs.

---

## 🔗 Key References
*   [004: Skills Infrastructure](file:///home/kjdragan/lrepos/universal_agent/Project_Documentation/004_skills_infrastructure.md)
*   [008: MCP Evolution](file:///home/kjdragan/lrepos/universal_agent/Project_Documentation/008_mcp_in_process_evolution.md)

---
