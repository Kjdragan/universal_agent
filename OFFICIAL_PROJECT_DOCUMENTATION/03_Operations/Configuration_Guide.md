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

## 4. Best Practices for Junior Developers

- **Use a `.env` file**: Create a `.env` in the root of the project to manage secrets localy.
- **Watch the Logs**: Enable Logfire to see the sequence of tool calls and thinking turns in real-time.
- **Start Small**: Use `UA_ENABLE_MEMORY_INDEX=off` while debugging new tool logic to reduce noise.
