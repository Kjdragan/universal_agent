# 020 - Startup Scripts Guide

> A comprehensive guide to all startup scripts and how to choose the right one for your use case.

---

## Quick Reference Table

| Script | Use Case | Terminals Needed | Ports Used |
|--------|----------|------------------|------------|
| `./start_gateway.sh` | **🎯 RECOMMENDED: Gateway Mode (CLI + Web UI)** | 1 | 8002, 8001, 3000 |
| `./start_cli_dev.sh` | Fast CLI dev/testing (direct mode) | 1 | None |
| `./start_ui.sh` | Web UI only (direct mode, no gateway) | 1 | 8001, 3000 |
| `./start_terminal.sh` | CLI + Agent College | 1 | 8001 |
| `./start_local.sh` | Multi-mode launcher | 1 | Varies |
| `./start_telegram_bot.sh` | Telegram Bot (Docker) | 1 | 8000 |
| `./start.sh` | Railway Deployment | N/A | 8000 |

---

## 🎯 Recommended Scripts by Use Case

### For Gateway Mode (Production-Like) — RECOMMENDED
```bash
./start_gateway.sh
```
**Why:** Runs the canonical execution engine. Both CLI and Web UI use the same gateway server. This is the unified architecture.

**What it starts:**
- Gateway Server (port 8002) — canonical execution engine
- API Server (port 8001) — forwards to gateway
- Web UI (port 3000) — React frontend

**To also run the CLI client (separate terminal):**
```bash
UA_GATEWAY_URL=http://localhost:8002 ./start_cli_dev.sh
```

### For Quick CLI Testing (Direct Mode)
```bash
./start_cli_dev.sh
```
**Why:** Fastest startup, no gateway overhead, runs `process_turn()` directly in-process. Good for rapid iteration.

### For Web UI Only (Direct Mode, Legacy)
```bash
./start_ui.sh
```
**Why:** Runs Web UI without the gateway. The API server runs the agent in-process. **Note:** This does NOT use the gateway and may have different behavior than CLI.

---

## Detailed Script Descriptions

### 1. `start_cli_dev.sh` — Fast CLI Development

**Purpose:** Quickest way to run the agent for development and testing.

**What it does:**
- Runs `main.py` directly (in-process gateway)
- No background services
- Supports three modes:
  - Interactive: `./start_cli_dev.sh`
  - Single query: `./start_cli_dev.sh "your prompt"`
  - Harness mode: `./start_cli_dev.sh --harness "objective"`

**When to use:**
- Rapid iteration on agent logic
- Testing prompts quickly
- Debugging execution flow

**Architecture:** Single process, in-process gateway.

---

### 2. `start_ui.sh` — Web UI Stack

**Purpose:** Run the full Web UI development stack.

**What it does:**
1. Kills any existing processes on ports 8001 and 3000
2. Starts API server (`universal_agent.api.server`) on port **8001** (background)
3. Starts React frontend (`web-ui/`) on port **3000** (foreground)
4. Cleans up API server on exit

**When to use:**
- Developing or testing the Web UI
- Visual interaction with the agent
- Debugging frontend-backend integration

**URLs:**
- Web UI: http://localhost:3000
- API: http://localhost:8001

**Architecture:** API server + React frontend (2 processes managed by script).

---

### 3. `start_terminal.sh` — CLI + Agent College

**Purpose:** Run CLI with the Agent College sidecar for trace analysis.

**What it does:**
1. Starts Agent College (`AgentCollege.logfire_fetch`) on port **8001** (background)
2. Changes interrupt key to `Ctrl+X` (so `Ctrl+C` doesn't kill everything)
3. Starts CLI in foreground
4. Restores `Ctrl+C` and cleans up on exit

**When to use:**
- When you need Agent College trace analysis alongside CLI
- Debugging complex multi-turn executions

**Architecture:** CLI + Agent College sidecar (2 processes).

---

### 4. `start_local.sh` — Multi-Mode Launcher

**Purpose:** Swiss-army-knife launcher with multiple modes.

**Modes:**
```bash
./start_local.sh         # Default: cli mode
./start_local.sh cli     # Interactive CLI
./start_local.sh bot     # Telegram webhook server (port 8000)
./start_local.sh worker  # Agent College worker only
./start_local.sh full    # CLI + Agent College (background)
```

**What it does:**
- Validates `.env` file and required environment variables
- Activates virtual environment
- Runs the selected mode

**When to use:**
- When you need env validation before startup
- Running Telegram bot locally
- Running the full system with Agent College

**Note:** More validation overhead than `start_cli_dev.sh`.

---

### 5. `start_gateway.sh` — Gateway Mode (RECOMMENDED)

**Purpose:** Run the unified gateway architecture where CLI and Web UI share the same execution engine.

**Usage:**
```bash
./start_gateway.sh              # Full stack: Gateway + API + Web UI
./start_gateway.sh --server     # Gateway server only
./start_gateway.sh --ui         # Web UI only (assumes gateway running)
```

**What it starts (full mode):**
1. **Gateway Server** (port **8002**) — canonical execution engine
2. **API Server** (port **8001**) — forwards requests to gateway
3. **Web UI** (port **3000**) — React frontend

**To run CLI client alongside (separate terminal):**
```bash
UA_GATEWAY_URL=http://localhost:8002 ./start_cli_dev.sh
```

**When to use:**
- **This is the recommended mode for testing**
- When you need both Web UI and CLI to use the same execution engine
- Production-like testing
- Multi-client scenarios

**Architecture:** Client-server with unified gateway backend.

---

### 6. `start_telegram_bot.sh` — Telegram Bot (Docker)

**Purpose:** Full Telegram bot deployment with ngrok tunnel.

**What it does:**
1. Starts ngrok tunnel on port 8000
2. Updates `.env` with new webhook URL
3. Starts Docker container (`docker-compose up`)
4. Registers webhook with Telegram API

**When to use:**
- Running the Telegram bot locally
- Testing webhook integration

**Requirements:**
- Docker and docker-compose
- ngrok installed and configured
- Telegram bot token in `.env`

**Architecture:** Docker container + ngrok tunnel.

---

### 7. `start.sh` — Railway Deployment

**Purpose:** Production entrypoint for Railway.app deployment.

**What it does:**
1. Fixes permissions for Railway volumes
2. Runs network diagnostics
3. Starts Agent College on port 8000 (internal)
4. Starts Telegram bot as main process

**When to use:**
- **Never run locally** — this is for Railway deployment only

**Architecture:** Docker/Railway production environment.

---

## Architecture Comparison

### 🎯 Unified Gateway Mode (RECOMMENDED)
```
┌──────────────────┐                    ┌──────────────────┐
│   CLI Client     │──── WebSocket ────►│                  │
│   (terminal)     │    (port 8002)     │  Gateway Server  │
└──────────────────┘                    │   (port 8002)    │
                                        │                  │
┌──────────────────┐     HTTP/WS        │  - Sessions      │
│   React UI       │◄──────────────────►│  - Composio      │
│   (port 3000)    │                    │  - process_turn  │
│                  │    ┌───────────┐   │    (execution)   │
│  - Browser UI    │────│ API Server│───►                  │
└──────────────────┘    │ (8001)    │   └──────────────────┘
                        └───────────┘
```
**Script:** `./start_gateway.sh` — Both CLI and Web UI use the SAME execution engine.

### Direct Mode (CLI Only, Fast Dev)
```
┌─────────────────────────────────────┐
│           CLI Process               │
│  ┌─────────────────────────────┐   │
│  │     InProcessGateway        │   │
│  │  ┌───────────────────────┐  │   │
│  │  │    process_turn()     │  │   │
│  │  │    (execution)        │  │   │
│  │  └───────────────────────┘  │   │
│  └─────────────────────────────┘   │
└─────────────────────────────────────┘
```
**Scripts:** `./start_cli_dev.sh`, `./start_local.sh cli`

### Direct Mode Web UI (Legacy)
```
┌──────────────────┐     HTTP/WS        ┌──────────────────┐
│   React UI       │◄──────────────────►│   API Server     │
│   (port 3000)    │    (port 8001)     │   (in-process)   │
│                  │                    │                  │
│  - Browser UI    │                    │  - UniversalAgent│
└──────────────────┘                    └──────────────────┘
```
**Scripts:** `./start_ui.sh` — ⚠️ Does NOT use gateway, may differ from CLI behavior.

---

## Troubleshooting

### Port Already in Use
```bash
# Find what's using a port
lsof -i :8001

# Kill it
fuser -k 8001/tcp
```

### Database Locked Error
When running external gateway mode, ensure only one process writes to the database. The gateway server should be the sole writer.

### Missing Environment Variables
Use `start_local.sh` for automatic validation, or manually check:
```bash
source .env
echo $ANTHROPIC_API_KEY
```

---

## Summary

| I want to... | Use this |
|--------------|----------|
| **Run with unified gateway (CLI + Web UI)** | `./start_gateway.sh` |
| Quickly test CLI prompts (direct mode) | `./start_cli_dev.sh` |
| Run CLI alongside gateway Web UI | `UA_GATEWAY_URL=http://localhost:8002 ./start_cli_dev.sh` |
| Run Telegram bot locally | `./start_telegram_bot.sh` |
| Run CLI with trace analysis | `./start_terminal.sh` |

---

*Last updated: 2026-01-27*
