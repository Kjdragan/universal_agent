# CLAUDE.md - Context & Rules for Claude (Universal Agent)

## Project Overview
This is the **Universal Agent** project. 
- **Gateway**: FastAPI-based server (`src/universal_agent/gateway_server.py`)
- **Telegram Bot**: (`src/universal_agent/bot/main.py`)
- **CLI**: (`src/universal_agent/main.py`)

## Core Principles
1.  **Source of Truth**: Always read the code in `src/` to understand behavior. Do not rely on old docs.
2.  **Tool Usage**: Use `uv run` for all python commands.
3.  **Testing**: Use `uv run pytest`.

## Key Architectures
- **Memory**: Hindsight system (JSON/Files).
- **Heartbeat**: Periodic wake-ups checked against `memory/HEARTBEAT.md`.
