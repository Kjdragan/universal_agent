# Telegram Bot Integration - Overview

## Introduction
The Universal Agent includes a Telegram Bot interface (`src/universal_agent/bot/`) that allows you to trigger agent tasks and receive results remotely. This system is designed to be:
1.  **Secure**: Uses a secret webhook token and an allowlist of User IDs.
2.  **Asynchronous**: Tasks are queued and processed one by one without blocking the bot.
3.  **Containerized**: The entire stack runs in Docker for consistency.

## Architecture
```mermaid
graph LR
    User[Telegram User] -- Message --> TG[Telegram Server]
    TG -- Webhook (HTTPS) --> Ngrok[Ngrok Tunnel]
    Ngrok -- Forward (HTTP) --> Bot[Bot (FastAPI)]
    Bot -- Add Task --> Queue[Task Manager]
    Queue -- Pick Task --> Adapter[Agent Adapter]
    Adapter -- Execute --> Agent[Universal Agent]
    Agent -- Result --> Bot
    Bot -- Reply --> User
```

## Folder Structure
- `src/universal_agent/bot/`: Contains all bot-specific code.
    - `main.py`: Entry point for the FastAPI server.
    - `telegram_handlers.py`: Logic for `/start`, `/agent`, etc.
    - `agent_adapter.py`: Bridge that calls `setup_session` and `process_turn`.
- `Dockerfile` & `docker-compose.yml`: For building the container environment.

## Prerequisites
1.  **Telegram Account**: To talk to the bot.
2.  **Ngrok Account**: To expose your local bot to the internet.
3.  **Docker**: To run the application isolated.
4.  **Hardware**: A Linux machine (or WSL) with ~16GB RAM for the agent.

## Next Steps
Please follow the numbered guides in this folder to set up your environment:
1.  `01_TELEGRAM_BOT_SETUP.md`: Create your bot and get credentials.
2.  `02_NGROK_SETUP.md`: Setup the tunneling service.
3.  `03_DOCKER_DEPLOYMENT.md`: Build and run the system.
4.  `04_USAGE_GUIDE.md`: How to use the bot.
