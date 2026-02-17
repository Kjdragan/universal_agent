# Universal Agent

A powerful, self-hosted **Autonomous Execution System** designed for deep research, complex task execution, and persistent long-term memory.

Unlike simple coding assistants, this system is built on two pillars:
1.  **The Execution Engine (Multi-Agent System)**: Powered by Claude Sonnet 4, capable of reasoning, tool usage (Composio, Crawl4AI), and subjective quality evaluation via an LLM Judge.
2.  **The Harness (URW)**: The "Universal Ralph Wrapper" that provides durability, context hygiene, and task orchestration for missions spanning 24+ hours.

## 📚 Documentation
> **[Start Here: Introduction](Project_Documentation/01_Introduction.md)**

*   **[Getting Started](Project_Documentation/02_Getting_Started.md)**: Setup guide (Python 3.13, `uv`).
*   **[System Architecture](Project_Documentation/03_Architecture/)**: Deep dives into the core design.
*   **Subsystems**:
    *   [Universal Ralph Wrapper (URW)](Project_Documentation/04_Subsystems/URW_Wrapper.md)
    *   [Multi-Agent Execution Engine](Project_Documentation/04_Subsystems/Multi_Agent_System.md)
    *   [Composio Integration](Project_Documentation/04_Subsystems/Composio_Integration.md)

## 🚀 Key Capabilities

-   **🧠 Advanced Brain**: Native Anthropic Agent SDK integration.
-   **🔌 Universal Integrations**: **Composio** for 500+ authorized tools (GitHub, Slack, etc.).
-   **📚 Long-Term Memory**: **Letta**-style persistent memory blocks.
-   **🕷️ High-Performance Research**: **Crawl4AI** for "LLM-ready" web scraping.
-   **📊 Observability**: Deep tracing with **Pydantic Logfire**.

## 🏗️ Quick Start

### Prerequisites
*   Python 3.12+ (3.13 recommended)
*   `uv` package manager

### Installation
```bash
git clone https://github.com/Kjdragan/universal_agent.git
cd universal_agent
uv sync
cp .env.sample .env  # Configure your API keys
```

### Running Locally
```bash
# Standard local dev loop (recommended)
./start_cli_dev.sh

# Production-like stack (Gateway + API + Web UI)
./start_gateway.sh

# Or direct invocation via uv
uv run python src/universal_agent/main.py --task "Research quantum computing trends"
```

For detailed usage, see **[Getting Started](Project_Documentation/02_Getting_Started.md)**.

## 📂 Project Structure

-   `src/universal_agent/`: Core agent logic (Brain & Harness).
-   `src/universal_agent/utils/`: Shared utility modules (including AST-based Python parsing helpers in `python_parser.py`).
-   `Project_Documentation/`: **The primary source of truth for docs.**
-   `AgentCollege/`: Sidecar service for trace analysis.
-   `Memory_System/`: Database and memory management.

## 🛡️ Security
-   **Context Isolation**: The URW ensures fresh context windows to prevent "prompt injection via history".
-   **Secrets**: All credentials managed via `.env`.
-   **Sandboxing**: Code execution is isolated.

## ✅ Todoist Task System (v1)

Universal Agent now uses **Todoist** as the primary task/brainstorm backend.

- Required env var: `TODOIST_API_TOKEN`
- Taxonomy bootstrap + task/idea flows are available through the internal Todoist service and CLI.
- Daily operator runbook: `OFFICIAL_PROJECT_DOCUMENTATION/03_Operations/41_Todoist_Heartbeat_And_Triage_Operational_Runbook_2026-02-16.md`

### Quick Todoist CLI examples

```bash
# Bootstrap projects/sections/labels (idempotent)
uv run python -m universal_agent.cli.todoist_cli setup

# List actionable tasks
uv run python -m universal_agent.cli.todoist_cli tasks

# Capture an idea into brainstorm inbox
uv run python -m universal_agent.cli.todoist_cli idea "Investigate retry backoff policy" --dedupe-key retry-backoff

# Optional live Todoist integration test (guarded)
RUN_TODOIST_LIVE_TESTS=1 TODOIST_API_TOKEN=<token> uv run pytest tests/integration/test_todoist_live_guarded.py -q
```
