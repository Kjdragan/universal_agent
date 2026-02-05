# 011: Clawdbot vs Universal Agent Architecture Comparison

**Date:** 2026-01-23
**Status:** Analysis Complete
**Author:** Antigravity

## 1. Executive Summary

This document provides a comprehensive architectural comparison between `Clawdbot` and the current `Universal Agent` (UA). The goal is to identify patterns from Clawdbot's mature, multi-interface design that can be adopted to unify UA's currently bifurcated (CLI vs. Web) architecture.

**Key Finding:** Clawdbot utilizes a **"Gateway" architecture** where the core agent logic is an isolated service that communicates strictly via an Event Bus. This allows the Terminal, Web UI, Slack, and Telegram to all be treated as equal "Channels." In contrast, Universal Agent currently employs a **"Dual Entry" architecture**, where `main.py` (CLI) and `server.py` (Web) are separate orchestrators that invoke the core agent logic directly, leading to duplicated plumbing and inconsistent behavior.

---

## 2. High-Level Architecture Comparison

| Feature | Clawdbot (Reference) | Universal Agent (Current) |
| :--- | :--- | :--- |
| **Core Interaction Model** | **Event-Driven:** The Agent emits events (Thought, Tool Call, Text). Channels listen and render. | **Request-Response / Loop:** The loop runs, returning results or streaming partials directly to the caller. |
| **Interface Handling** | **Plugin-based Channels:** `src/channels/telegram`, `src/channels/slack`, `src/terminal`. | **Distinct Entry Points:** `main.py` (Interactive Loop) vs `api/server.py` (API/WebSocket). |
| **Session State** | **Centralized Registry:** `SessionStore` tracks active sessions across all channels. | **Distributed:** CLI runs in specific dirs; Web manages sessions via `agent_bridge`. |
| **Process Model** | **Service & Workers:** Main process executes a "Gateway", agent runs can be spawned or in-process. | **Single Process:** CLI runs as a script. Web runs as a server spawning tasks. |

### 2.1 Clawdbot Anatomy: The Gateway Pattern
Clawdbot strictly separates the "Brain" from the "Mouth".
*   **Gateway (`src/gateway/`)**: The central hub. It receives normalized messages from *any* channel and routes them to the correct Agent Session.
*   **Channels (`src/channels/`)**: Adapters that translate external protocols (Telegram API, Slack Events, Stdin/Stdout) into internal Gateway Events.
*   **Agents (`src/commands/agent.ts`)**: The execution units. They don't know who they are talking to; they just output events to the Gateway.

### 2.2 Universal Agent Anatomy: Direct Invocation
Universal Agent connects the "Brain" directly to the "Mouth".
*   **CLI (`main.py`)**: Instantiates `UniversalAgent`, calls `process_turn`, and prints output with `rich`. It *owns* the loop.
*   **Web (`api/server.py`)**: Instantiates `UniversalAgent`, hooks into `stdout`/callbacks to capture output, and pushes to generic WebSocket events. It *emulates* the loop.

---

## 3. Deep Dive: Key Technical Differences

### 3.1 Event System vs. Direct Output
*   **Clawdbot (`server-node-events.ts`)**: The core logic doesn't `print`. It emits structured events:
    *   `agent.thought`: "I am searching for X..."
    *   `agent.tool_call`: "Calling google_search..."
    *   `chat.message`: "Here is what I found."
    *   **Implication:** A Telegram bot can decide to ignore "thoughts" and only show "messages", while the Web UI shows everything.
*   **Universal Agent**:
    *   The `UniversalAgent` class returns execution results.
    *   `main.py` prints these results to the console.
    *   `agent_bridge.py` tries to capture these results and convert them to JSON for the frontend.
    *   **Friction:** Adding a new output type (e.g., a "Confidence Score" gauge) requires updating `process_turn`, `main.py`, `agent_bridge.py`, and `server.py`.

### 3.2 Channel Abstraction
*   **Clawdbot**: All interfaces are **Channels**.
    *   `src/entry.ts`: Bootstraps the Gateway.
    *   `src/channels/terminal`: Just a plugin that reads stdin and POSTs to the Gateway.
    *   `src/channels/telegram`: A plugin that polls Telegram and POSTs to the Gateway.
*   **Universal Agent**:
    *   CLI is the "Main App".
    *   Web is a "Bolt-on" server.
    *   **Friction:** To add Telegram, we'd have to write a *third* entry point (`bot.py`) that duplicates the initialization logic of `main.py`.

### 3.3 Session Management
*   **Clawdbot**: uses a `session-utils.ts` and `SessionStore` to map `session_key` (e.g., `telegram:12345` or `terminal:default`) to a persistent workspace and execution context.
*   **Universal Agent**: Uses `durable/` for state, but the mapping of "User Identity" to "Session" is loosely defined in `agent_bridge` for Web and implicit in CLI (cwd).

---

## 4. Reusable Patterns for Universal Agent

We can evolve Universal Agent towards this unified architecture without rewriting the core logic (which is excellent).

### Recommendation 1: implementations of `AgentEventBus`
Instead of `main.py` driving the loop, we should introduce an asynchronous Event Bus.
```python
# Conceptual Architecture
class AgentEventBus:
    async def emit(self, event: AgentEvent): ...
    async def subscribe(self, topic: str, callback): ...

# Core Agent changes
class UniversalAgent:
    def __init__(self, bus: AgentEventBus):
        self.bus = bus
    
    async def run(self, input):
        await self.bus.emit(AgentEvent(type="thought", content="Processing..."))
        # ... execution ...
        await self.bus.emit(AgentEvent(type="message", content="Done."))
```

### Recommendation 2: "Unified Host/Server" Entry Point
Refactor `main.py` to be a lightweight shim.
1.  **`core/host.py`**: Initializes the Agent, Database, and Event Bus.
2.  **`interfaces/cli.py`**: Starts the Host, subscribes to Bus, renders events to `rich` console, sends stdin to Host.
3.  **`interfaces/web.py`**: Starts the Host, subscribes to Bus, forwards events to WebSocket, sends HTTP requests to Host.
4.  **`interfaces/telegram.py`**: Starts the Host, bridges Telegram API to Bus.

### Recommendation 3: Standardized "Session Keys"
Adopt Clawdbot's session key strategy to unify how we identify runs.
*   `cli:local` (Current terminal usage)
*   `web:session_uuid` (Current web usage)
*   `telegram:chat_id` (Future)

This allows the backend to be agnostic about *who* is talking to it.

---

## 5. Conclusion & Next Steps

The `universal_agent` has a stronger execution engine (Claude SDK + durable + logfire), but `clawdbot` has a superior "plumbing" architecture.

**The Strategy:**
Don't rewrite the agent. Rewrite the **harness**.
1.  **Phase 1 (Refactor)**: Extract `AgentEventBus` functionality. Make `UniversalAgent` event-driven.
2.  **Phase 2 (Unify)**: Create a `UnifiedHost` class that `main.py` and `server.py` both use.
3.  **Phase 3 (Expand)**: Add `Telegram` interface as a proof-of-concept for the new architecture.

This aligns perfectly with our roadmap in `010_clawdbot_integration_phasing.md` (Phase 2: Plumbing & Event Bus).
