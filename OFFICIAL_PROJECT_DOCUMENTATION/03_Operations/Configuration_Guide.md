# Configuration Guide

The Universal Agent is highly configurable using environment variables. This allows for flexible deployments (CLI, Railway, Docker) without changing core code.

## 1. Feature Flags

Feature flags (defined in `src/universal_agent/feature_flags.py`) allow you to toggle major subsystems at runtime.

| Flag | Env Variable | Description | Default |
| --- | --- | --- | --- |
| **Heartbeat** | `UA_ENABLE_HEARTBEAT` | Enable/Disable the autonomic agent loop. | `False` |
| **Memory Index** | `UA_ENABLE_MEMORY_INDEX` | Toggle semantic memory indexing. | `False` |
| **Cron** | `UA_ENABLE_CRON` | Enable scheduled tasks via cron service. | `False` |
| **Logfire** | `UA_DISABLE_LOGFIRE` | Disable Pydantic Logfire tracing. | `False` |

## 2. Environment Variables

### Core Configuration

- **`ANTHROPIC_API_KEY`**: (Required) Your Anthropic API key.
- **`GEMINI_API_KEY`**: (Optional) For skills that use Gemini (like `nano-banana-pro`).
- **`USER_TIMEZONE`**: Set your local timezone (e.g., `America/Chicago`) for heartbeat scheduling.
- **`UA_SYSTEM_PROMPT_MODE`**: Controls how `ClaudeAgentOptions.system_prompt` is built.
  - `claude_code_append` (default): use Claude Code preset prompt and append UA prompt builder output.
  - `custom_only`: use only UA prompt builder output as the full system prompt.

### Memory & Search

- **`UA_MEMORY_INDEX`**: Modes: `off`, `json`, `vector`, `fts`.
- **`UA_MEMORY_BACKEND`**: Backends: `chromadb`, `lancedb`, `sqlite`.
- **`UA_EMBEDDING_PROVIDER`**: Providers: `sentence-transformers`, `openai`.
- **`UA_EMBEDDING_MODEL`**: The specific model to use (e.g., `all-MiniLM-L6-v2` or `text-embedding-3-small`).
- **`UA_MEMORY_MAX_TOKENS`**: Maximum memory to inject into context (Default: `800`).

### Observability

- **`LOGFIRE_TOKEN`**: Your Pydantic Logfire token for centralized tracing.
- **`UA_LOG_LEVEL`**: Logging verbosity (`DEBUG`, `INFO`, `WARNING`).

## 3. Override Configuration (`heartbeat_config.json`)

For session-specific behavior, you can place a `heartbeat_config.json` in the workspace directory. This file supports overrides for:

- `schedule`: Interval and active hours.
- `visibility`: Hidden thoughts vs visible alerts.
- `delivery`: Which session IDs receive system events.

---

## 4. Process Heartbeat vs Heartbeat Service

> **These are two completely different systems.** Confusing them will lead to misconfiguration.

| | **Process Heartbeat** | **UA Heartbeat Service** |
|---|---|---|
| **Purpose** | OS-level liveness signal ("is the gateway process alive?") | Application-level proactive agent scheduler |
| **Implementation** | Daemon **thread** writes timestamp to file every 10s | Async **task** runs agent sessions every ~30 min |
| **Event loop** | **Independent** — works even when event loop is blocked | Runs **on** the event loop |
| **Consumer** | `vps_service_watchdog.sh` (systemd timer) | Internal: drives HEARTBEAT.md checks, Todoist, briefings |
| **Env prefix** | `UA_PROCESS_HEARTBEAT_*` | `UA_HEARTBEAT_*` / `UA_HB_*` |
| **Source** | `src/universal_agent/process_heartbeat.py` | `src/universal_agent/heartbeat_service.py` |

### Process Heartbeat

The gateway process runs a background thread that writes the current Unix timestamp to a file every 10 seconds. The VPS service watchdog reads this file instead of making an HTTP health check to determine if the gateway is alive. This prevents false-positive restarts during long-running LLM calls that temporarily block the async event loop.

**Key env vars:**
- `UA_PROCESS_HEARTBEAT_FILE` — File path (default: `/var/lib/universal-agent/heartbeat/gateway.heartbeat`)
- `UA_PROCESS_HEARTBEAT_INTERVAL_SECONDS` — Write interval (default: `10`)
- `UA_WATCHDOG_HEARTBEAT_STALE_SECONDS` — Staleness threshold before watchdog considers process dead (default: `300`)

**Directory requirement:** `/var/lib/universal-agent/heartbeat` must exist and be owned by the `ua` service user. This is handled automatically by `deploy_vps.sh`.

### UA Heartbeat Service

The Heartbeat Service is the proactive agent scheduler that periodically wakes sessions to check HEARTBEAT.md, process Todoist tasks, run system health checks, and perform autonomous briefings. It has no relation to the process liveness mechanism above.

**Key env vars:** `UA_ENABLE_HEARTBEAT`, `UA_HEARTBEAT_EVERY`, `UA_HEARTBEAT_ACTIVE_HOURS`, etc.

---

## 5. Best Practices for Junior Developers

- **Use a `.env` file**: Create a `.env` in the root of the project to manage secrets localy.
- **Watch the Logs**: Enable Logfire to see the sequence of tool calls and thinking turns in real-time.
- **Start Small**: Use `UA_ENABLE_MEMORY_INDEX=off` while debugging new tool logic to reduce noise.
