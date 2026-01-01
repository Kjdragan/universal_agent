# Universal Agent

A powerful, self-hosted autonomous agent built on the **Claude Agent SDK**, **Composio Tool Router**, and **Letta Memory System**. This agent is designed for deep research, complex task execution, and persistent long-term memory, deployable anywhere via Docker (optimized for Railway).

## 🚀 Key Capabilities

-   **🧠 Advanced Brain**: Powered by **Claude Sonnet 4** using the native Anthropic Agent SDK.
-   **🔌 Universal Integrations**: Uses **Composio Tool Router** to access 500+ tools (Gmail, SERP, Slack, GitHub) without building custom auth flows.
-   **📚 Long-Term Memory**: Implements **Letta (MemGPT)** concepts with persistent memory blocks (Human, Persona, System Rules) that the agent edits and consults.
-   **🕷️ High-Performance Research**: Built-in **Crawl4AI** integration for parallel web scraping and "LLM-ready" markdown extraction.
-   **🤖 Dual Interfaces**:
    -   **Telegram Bot**: Rich, interactive chat with execution stats, timing, and direct Logfire trace links.
    -   **CLI**: Full-featured local terminal interface for development and debugging.
-   **🔄 Session Continuity**: Persistent agent context (Actor Model) allows natural multi-turn conversations without losing history.
-   **📊 Full Observability**: Deep tracing with **Pydantic Logfire** for every tool call and thought process.
-   **🎓 Agent College (Sidecar)**: Background service that analyzes execution traces to provide feedback and critiques (experimental).

## 🏗️ Architecture

```mermaid
graph TD
    User[User] -->|Telegram / CLI| Bot[Universal Agent Bot]
    Bot -->|Async Queue| Actor[Agent Actor (Context)]
    
    subgraph "Agent Brain (Main Process)"
        Actor -->|Think| Claude[Claude Sonnet 4]
        Actor -->|Execute| Router{Tool Router}
    end
    
    subgraph "Capabilities"
        Router -->|Remote Apps| Composio[Composio SDK]
        Router -->|Local Tools| MCP[Local MCP Server]
        Router -->|Recall| Letta[Letta Memory]
    end
    
    subgraph "Local Tools"
        MCP -->|Scrape| C4AI[Crawl4AI]
        MCP -->|Files| FS[FileSystem]
    end
    
    subgraph "Sidecar"
        College[Agent College] -.->|Analyze| Logfire[Logfire Traces]
    end
    
    Composio --> GitHub/Slack/Gmail
```

## 🛠️ Setup & Installation

### Prerequisites
-   Python 3.12+ via `uv` (recommended) or `pip`.
-   **API Keys**: Anthropic, Composio, Telegram Bot Token.

### 1. Installation
```bash
git clone https://github.com/Kjdragan/universal_agent.git
cd universal_agent
uv sync
```

### 2. Environment Configuration
Create a `.env` file based on `.env.example` (if available) or required keys:
```bash
ANTHROPIC_API_KEY=sk-...
COMPOSIO_API_KEY=...
COMPOSIO_USER_ID=...          # Your generic user ID for integrations
TELEGRAM_BOT_TOKEN=...
WEBHOOK_SECRET=...            # Secure token for Telegram webhooks
LOGFIRE_TOKEN=...             # Optional: For tracing
```

### 3. Running Locally

**CLI Agent + Agent College (Recommended):**
```bash
./local_dev.sh
```

**CLI Only (Manual):**
```bash
PYTHONPATH=src uv run python -m universal_agent.main
```

**Telegram Bot (Local with ngrok):**
```bash
# Terminal 1: Start ngrok
ngrok http 8080

# Terminal 2: Set WEBHOOK_URL in .env, then:
PYTHONPATH=src uv run uvicorn universal_agent.bot.main:app --host 0.0.0.0 --port 8080
```

## 🚢 Deployment (Railway)

The project is optimized for **Railway** deployment via Docker.

1.  **Repo Structure**: Monorepo-style with `src/`, `AgentCollege/`, and `Memory_System/`.
2.  **Dockerfile**: Installs system dependencies (Chrome for crawling, ffmpeg) and builds python env.
3.  **Start Command**: `start.sh` launches both the **Agent College** (background) and **Telegram Bot** (foreground).
4.  **Health Check**: `/health` endpoint configured for Railway.

## 📂 Project Structure

-   `src/universal_agent/`: Core agent logic.
    -   `main.py`: The "Brain" and CLI entry point.
    -   `bot/`: Agent Adapter, Telegram Handlers, and API server.
-   `src/mcp_server.py`: Local tools implementation (FileSystem, Crawl4AI wrapper).
-   `Memory_System/`: Letta-style memory management logic.
-   `AgentCollege/`: Subsystem for trace analysis and critique.
-   `AI_DOCS/`: Context documentation for the agent (git-ignored locally).

## 🛡️ Security

-   **History Sanitized**: Sensitive documentation (`AI_DOCS/`) is strictly ignored and removed from git history.
-   **Secrets Management**: Enforces environment variables for all credentials.
-   **Sandboxing**: Code execution via Composio Remote Workbench (Dockerized) or strictly controlled local tools.
