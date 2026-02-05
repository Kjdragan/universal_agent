
# Heartbeat System Comparison: Universal Agent vs. Clawdbot

## Executive Summary

**Verdict**: The Universal Agent implementation (`heartbeat_service.py`) achieves full functional parity with Clawdbot (`heartbeat.ts`), and in some areas (Configuration), exceeds it. All "driving elements" are present.

## Detailed Feature Matrix

| Feature | Clawdbot (TypeScript) | Universal Agent (Python) | Status |
| :--- | :--- | :--- | :--- |
| **Scheduler** | Driven by external cron or internal tick. | Internal `_scheduler_loop` (asyncio) with configurable tick. | ✅ **Parity** |
| **Wake Coalescer** | Prevents spam by coalescing wake signals. | `wake_sessions` & `wake_next_sessions` sets for batching. | ✅ **Parity** |
| **Orchestration** | Single-threaded Node event loop. | `asyncio` event loop. Handles `busy_sessions` to avoid conflicts during active chat. | ✅ **Parity** |
| **Suppression** | Strips `HEARTBEAT_OK` tokens. | `_strip_heartbeat_tokens` with regex normalization. | ✅ **Parity** |
| **Deduplication** | Time-window based dedupe of repeated alerts. | `dedupe_window_seconds` & content hashing. | ✅ **Parity** |
| **Checklist Parsing** | Checks if `HEARTBEAT.md` is "effectively empty". | `_is_effectively_empty` (ignores comments/whitespace). | ✅ **Parity** |
| **Delivery** | Message sending logic. | `connection_manager.broadcast` (Abstracted). | ✅ **Parity** |

## "Driving Elements" Audit

### 1. The Clock (Driving Element)

- **Problem**: How does it verify time passed?
- **UA Implementation**: `_scheduler_loop` runs forever, sleeping for short ticks (~5s). It checks `time.time() - state.last_run >= interval` for every active session.
- **Verification**: Confirmed in lines 552-581 of `heartbeat_service.py`.

### 2. The Decision Maker (Driving Element)

- **Problem**: How does it decide to speak?
- **UA Implementation**:
    1. Checks if `HEARTBEAT.md` has content (`_is_effectively_empty`).
    2. Sends prompt to Agent.
    3. Parses response.
    4. If response is just "HEARTBEAT_OK", it suppresses the message (unless configured otherwise).
- **Verification**: Confirmed in lines 646-768 of `heartbeat_service.py`.

### 3. The Deliveryman (Driving Element)

- **Problem**: How does the message get to the user?
- **UA Implementation**: Uses an abstracted `connection_manager`.
  - **Web/Gateway**: Uses `ConnectionManager` (WebSockets).
  - **Telegram**: Uses `BotConnectionAdapter` (API Calls) - *This was the missing piece we built today.*
- **Verification**: Confirmed in lines 803-863.

## Conclusion

We have all the necessary components. The system effectively replicates the proactive behavior of the original Clawdbot but adapts it to the Python/Asyncio ecosystem of Universal Agent.
