# Telegram Integration Comparison: Clawdbot vs. Universal Agent

**Date:** 2026-02-04
**Status:** Analysis & Recommendation

## 1. Executive Summary

This document compares the Telegram implementation in **Clawdbot** (Node.js/TypeScript) with the current implementation in **Universal Agent** (Python). The goal is to identify gaps and define a migration path to bring Universal Agent's Telegram bot up to parity with Clawdbot's robust, extensible architecture.

**Key Finding:** Universal Agent uses a monolithic, command-centric approach ideal for simple tasks, while Clawdbot employs a robust **Middleware & Plugin Architecture** that supports scalability, normalized message handling, and complex multi-turn flows.

---

## 2. Architecture Comparison

| Feature | Clawdbot (Node.js/Grammy) | Universal Agent (Python/PTB) |
| :--- | :--- | :--- |
| **Framework** | **Grammy** (Middleware-based) | **python-telegram-bot** (Callback-based) |
| **Structure** | **Modular Plugins** (onboarding, actions, etc.) in `src/channels/plugins/` | **Monolithic** `main.py` + `telegram_handlers.py` |
| **Execution Model** | **Concurrent Sequential Processing** (`grammy/runner`) with deduplication | **Job Queue** (`TaskManager`) with simple concurrency limit |
| **State Management** | **Persistent Session Store** (File/JSON based) | **Ephemeral In-Memory** (`TaskManager` dictionary) |
| **Message Handling** | **Normalized** (converts TG msg to generic `IMessage`) | **Raw Updates** (passes `Update` object directly to handlers) |
| **Throttling** | **Built-in** (`transformer-throttler`) | **None** (relies on manual `sleep`) |
| **Topic/Forum** | **Native Support** (resolves thread IDs dynamically) | **Basic Support** (treats threads as generic chats) |

### 2.1 Clawdbot Approach (The Target)

Clawdbot treats Telegram as just one "channel" that pipes data into a generic agent runtime.

* **Pipeline:** `Update` -> `Dedupe` -> `Throttler` -> `Normalization` -> `Agent Runtime` -> `Action`
* **Plugins:** Features like "onboarding" (welcome messages) or "actions" (sending typing indicators) are separate modules.
* **Normalization:** It converts `Context` into a simplified object, decoupling the bot logic from the Telegram API details.

### 2.2 Universal Agent Approach (Current)

Universal Agent treats the bot as a remote control for the `Gateway`.

* **Pipeline:** `Update` -> `CommandHandler` -> `TaskManager` -> `AgentAdapter` -> `Gateway`
* **Coupling:** The `AgentAdapter` is tightly coupled to `Gateway` logic.
* **State:** Task state is lost on restart (in-memory `TaskManager`).

---

## 3. Key Differences & Gaps

### A. Middleware vs. Handlers

* **Clawdbot:** Uses a middleware chain. You can plug in a "logger", then a "crash handler", then "session resolver", then actual logic.
* **UA:** Uses explicit handler registration (`add_handler`). Harder to inspect or intercept global flow.

### B. Message Normalization

* **Clawdbot:** Normalizes everything to strict types (`User`, `Message`, `File`) before processing.
* **UA:** Handlers accept raw `telegram.Update` objects, leading to scattered `update.effective_message.text` checks.

### C. Sequentialization & Concurrency

* **Clawdbot:** Uses `grammy/runner` to ensure messages from the *same chat* are processed in order, while different chats run in parallel.
* **UA:** Launches an async task for every request. No guarantee of order if a user spams messages.

### D. Session & Context

* **Clawdbot:** Resolves a `SessionKey` (e.g., `agent:default:telegram:group:123`) and loads config/memory for that specific slice.
* **UA:** Uses `session_id = f"tg_{user_id}"`. Less flexible support for Group Chats or Topics where context is shared.

---

## 4. Recommendations for Migration

To achieve "Clawdbot Parity" in Python, we do not need to switch languages, but we should adopt the **Design Patterns**:

1. **Adopt a Middleware Pattern:** Even in `python-telegram-bot` (PTB), we can structure code as a pipeline of "Processors" rather than loose handlers.
2. **Implement Normalization:** Create a translation layer (`TelegramToAgentEvent`) that converts TG updates into our standard `AgentEvent` or `GatewayRequest` *before* hitting business logic.
3. **Replace TaskManager with Async Queue/Runner:** Use a proper simplified runner that guarantees sequential execution per-chat.
4. **Persistent Session Store:** Store `chat_id` -> `session_id` mappings in a file/DB so sessions survive reboots.
5. **Modular Feature Folders:** Split `telegram_handlers.py` into `plugins/onboarding.py`, `plugins/commands.py`, `plugins/media.py`.

## 5. Next Steps

1. **Approval:** Review this comparison.
2. **Implementation Plan:** I will generate a detailed plan to refactor `src/universal_agent/bot/` into a modular package.
3. **Execution:** We will rebuild the bot step-by-step, starting with the Message Normalizer.
