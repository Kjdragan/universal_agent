# 001 SDK Concurrency & Parallelism Research

**Date:** 2026-01-15
**Topic:** Concurrency capabilities of the Claude Agent SDK (Python)

## Executive Summary

The Claude Agent SDK ("Quad Agent SDK") natively supports advanced concurrency patterns. Contrary to a simple sequential model, the SDK is "async-first" and designed to enable **Parallel Sub-Agents** and **Background Execution**.

## Key Capabilities

### 1. Parallel Sub-Agents
The SDK allows a Primary Agent to spawn specific sub-agents to handle focused subtasks concurrently.
- **Use Case:** Dispatching a `style-checker`, `security-scanner`, and `test-runner` simultaneously during a review.
- **Mechanism:** The Main Agent acts as an orchestrator. It can issue multiple tool calls (or sub-agent invocations) which the SDK runtime processes in parallel.

### 2. Independent "Background" Execution
Sub-agents can run in the "background" while the main agent continues other work.
- **"Wake Up" Functionality:** Results from these background agents are surfaced upon completion, effectively "waking up" or interrupting the primary agent's flow with new information.
- **Context Isolation:** Each sub-agent maintains its own isolated context window, preventing the main agent's context from being polluted with raw logs or intermediate steps.

### 3. Async Hooks & Tools
- **Async Tools:** Custom Python tools can be `async def`, allowing the runtime to `await` them concurrently.
- **Hooks:** The SDK provides lifecycle hooks (e.g., `PreToolUse`) which can be asynchronous, enabling parallel validation or logging steps without blocking the main thread.

## Implementation Guide

To leverage this in our Universal Agent:

1.  **Parallel Dispatch:** Ensure the Main Agent's prompt encourages emitting multiple sub-agent calls in a single turn (e.g., `<tool_code>call_agent_A(); call_agent_B()</tool_code>` equivalent).
2.  **Async Hooks:** Utilize `AsyncHookJSONOutput` (found in `types.py`) to defer hook execution or run checks in the background.
3.  **Orchestration Pattern:** Move from "Linear Handoff" (Main -> Sub -> Main) to "Hub and Spoke" (Main -> [Sub A, Sub B] -> Main).

## Conclusion
The SDK is not limited to sequential operations. It enables a **Concurrent Actor Model** where the specific limit is defined by the underlying API concurrency (ZAI: 5 connections) and the orchestrator's logic.
