# How to Run the Universal Agent

This document consolidates all methods for running the Universal Agent, its sidecars, and the Telegram Bot.

## 🚀 Quick Start (Local Development)

The easiest way to develop locally is to run the **CLI Agent** (interactive) and **Agent College** (sidecar) simultaneously.

**Use the helper script:**
```bash
./local_dev.sh
```
*This starts Agent College in the background and the CLI in the foreground. It handles cleanup when you exit.*

---

## 🛠 Manual Commands

If you prefer to run components manually in separate terminals, use these commands.
**Note:** `PYTHONPATH=src` is required for local execution to resolve package imports correctly.

### 1. CLI Agent (Interactive Terminal)
To talk to the agent in your terminal:
```bash
PYTHONPATH=src uv run python -m universal_agent.main
```

### 2. Agent College (Sidecar)
Required for memory forming and critiques. Run this in a separate terminal:
```bash
PYTHONPATH=src uv run uvicorn AgentCollege.logfire_fetch.main:app --port 8001
```

### 3. Telegram Bot (Local Server)
To start the Telegram Bot locally (listens for webhooks/messages):
```bash
PYTHONPATH=src uv run uvicorn universal_agent.bot.main:app --host 0.0.0.0 --port 8000 --reload
```
*Note: This runs the server process. It does not provide an interactive terminal input.*

---

## ☁️ Production (Railway Deployment)

The **Telegram Bot** runs as a completely separate, always-on process in the cloud (Railway).

-   **It runs by itself**: It does not depend on your local machine.
-   **It uses `start.sh`**: This script launches both the Agent College (sidecar) and the Telegram Bot server inside the Railway container.
-   **Always On**: Unlike your local CLI which stops when you close the terminal, the Railway bot listens 24/7 for Telegram messages.

**Railway Command (Automatic):**
```bash
/app/start.sh
```
*(You do not run this manually; Railway runs it on deployment)*.
