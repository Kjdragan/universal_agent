# 009: MCP Vision - Vocal vs. Silent Architectures

**Date:** 2026-01-23  
**Status:** Architectural Decision Record (ADR)

## Executive Summary

The Universal Agent has transitioned from a **Subprocess-based (Silent)** MCP architecture to a **Native In-Process (Vocal)** architecture for critical long-running research tools. This document records the rationale, the trade-offs, and our long-term strategy for tool integration style.

---

## 🏗️ Architectural Styles

### 1. The "Vocal" Style (Native In-Process)
**Implementation:** Tools are defined with the `@tool` decorator and registered via `create_sdk_mcp_server` directly in the agent process.
*   **Best For:** Long-running internal pipelines (Research, Drafting, Compilation).
*   **Key Advantage:** Full terminal disclosure and real-time logging.

### 2. The "Silent" Style (External Subprocess)
**Implementation:** Tools are registered via the `stdio` transport, running in a separate process/server file.
*   **Best For:** Lightweight utility tools, third-party integrations, or risky/unstable scripts.
*   **Key Advantage:** Process isolation and independent dependency management.

---

## ⚖️ Trade-off Analysis

| Feature | Vocal (In-Process) | Silent (Subprocess) |
| :--- | :--- | :--- |
| **Visibility** | **High**: Real-time logs and progress markers. | **Low**: Output is buffered and only shown after completion. |
| **Performance** | **Fast**: Zero IPC overhead; shared memory. | **Moderate**: Overhead of process spawning and JSON-RPC over pipes. |
| **Isolation** | **Low**: A crash in a tool can crash the whole agent. | **High**: Tools run in their own "sandbox" process. |
| **Dependencies** | **Coupled**: Agent environment must include all tool libs (e.g., Crawl4AI). | **Decoupled**: Tools can run in their own venvs or Docker containers. |
| **Complexity** | **High**: Requires careful async management to avoid blocking the main loop. | **Low**: Standard MCP protocol keeps them separate. |

---

## 🛠️ The "Universal" Debate: One Style or Both?

The question was raised: **Should we convert ALL tools to the new Vocal style?**

### The Argument for One Style (Universal Vocal):
*   **Consistency**: Developers only need to learn one way to write tools.
*   **Visibility by Default**: No more "guessing" what a tool is doing.
*   **Simpler Deployment**: Fewer moving parts/processes to manage.

### The Argument for Two Styles (Hybrid):
*   **Security & Safety**: Some tools (like 3rd party MCP servers) shouldn't have access to the agent's internal objects or memory.
*   **Environment Isolation**: If a tool requires a conflicting library version (e.g., an older `pydantic`), running it in a separate process is the only solution.
*   **Non-Blocking Logic**: Native tools MUST be async. Legacy synchronous tools are safer to run as subprocesses where they won't block the agent's event loop.

---

## 🎯 The Decision: The "Hybrid Guard" Strategy

We will maintain **Both Styles**, but with a strict classification policy:

1.  **"Vocal" Internal Tools (Standard)**: All tools developed specifically for the Universal Agent's core workflows (Research, Reporting, Memory) must be **In-Process** to ensure full user disclosure.
2.  **"Vocal" Wrapper Pattern**: For external long-running tools that we *cannot* move in-process (like a 3rd party search API), we will write an **In-Process Wrapper** that handles the logging/progress for the user while calling the external service.
3.  **"Silent" Legacy/Third-Party**: We will keep the `stdio` server capability for quick testing of external MCP servers (Composio, community tools) where visibility is less critical than rapid integration.

### ✅ Verification Note
When implementing a new tool, ask: *"Will this run for more than 5 seconds?"*
*   If **YES**: It must be **Vocal (In-Process)**.
*   If **NO**: It can be **Silent (Subprocess)**.

---
