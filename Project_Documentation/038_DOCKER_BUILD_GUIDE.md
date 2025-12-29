# Docker Build & Deployment Guide

## Overview

This document covers Docker fundamentals for the Universal Agent Telegram bot, including common pitfalls and solutions.

---

## Build Commands Quick Reference

| Command | Purpose |
|---------|---------|
| `docker-compose up -d` | Start containers (uses cached image) |
| `docker-compose up -d --build` | Rebuild changed layers only |
| `docker-compose build --no-cache` | Full rebuild (ignores cache) |
| `docker-compose down` | Stop and remove containers/networks |

---

## Understanding Docker Layers & Caching

Docker builds images in **layers**. Each `RUN`, `COPY`, or `ADD` instruction creates a layer:

```dockerfile
RUN pip install uv              # Layer 1
COPY pyproject.toml .           # Layer 2  
RUN uv sync --frozen            # Layer 3
RUN uv pip install aiohttp      # Layer 4  ← If you add this
```

### How Caching Works

1. Docker checks if each layer has changed
2. If a layer is unchanged, it uses the **cached** version
3. If a layer changes, **all subsequent layers rebuild**

### The Problem We Hit

When we added `aiohttp` to an existing `RUN pip install` line:
```dockerfile
# Before
RUN uv pip install uvicorn python-telegram-bot nest_asyncio

# After  
RUN uv pip install uvicorn python-telegram-bot nest_asyncio aiohttp
```

Docker sometimes uses the cached layer if:
- The base image hash matches
- File timestamps haven't changed

### Solution: Force Full Rebuild

```bash
docker-compose build --no-cache agent-bot
```

---

## Network Errors

### "Network needs to be recreated"

```
ERROR: Network "universal_agent_default" needs to be recreated
```

**Cause:** Docker network settings changed.

**Fix:** 
```bash
docker-compose down && docker-compose up -d
```

The `down` removes old networks; `up` creates fresh ones.

---

## Multi-Service Architecture

Our setup uses **two containers**:

```yaml
services:
  crawl4ai:          # Pre-built image (unclecode/crawl4ai)
    image: unclecode/crawl4ai:basic-amd64
    
  agent-bot:         # Our custom build
    build: .         # ← Uses ./Dockerfile
```

### Internal Networking

Containers communicate via Docker's internal DNS:
- `http://crawl4ai:11235` - Agent-bot talks to crawl4ai
- Services reference each other by **service name**, not localhost

---

## Adding New Dependencies

### Method 1: Add to pip install line
```dockerfile
RUN uv pip install uvicorn python-telegram-bot aiohttp NEW_PACKAGE
```

### Method 2: Add to pyproject.toml
```toml
[project.dependencies]
new-package = "^1.0"
```
Then update lock: `uv lock` locally before rebuilding.

### After Adding Dependencies

Always force rebuild:
```bash
docker-compose down
docker-compose build --no-cache agent-bot
docker-compose up -d
```

---

## Debugging

### View logs
```bash
docker logs -f universal_agent_bot         # Follow logs
docker logs --tail 50 universal_agent_bot  # Last 50 lines
```

### Check if package installed
```bash
docker exec universal_agent_bot pip show aiohttp
```

### Enter container shell
```bash
docker exec -it universal_agent_bot bash
```

### Check container status
```bash
docker-compose ps
docker ps -a  # Includes stopped containers
```

---

## Best Practices

1. **Always use `--no-cache` when adding new dependencies**
2. **Run `docker-compose down` first** if you see network errors
3. **Check logs immediately** after starting: `docker logs -f <container>`
4. **Verify packages installed** with `docker exec ... pip show <package>`

---

## Startup Script

The `start_telegram_bot.sh` handles:
1. Kills old ngrok
2. Starts new ngrok tunnel
3. Updates `.env` with webhook URL
4. Restarts Docker containers
5. Registers webhook with Telegram

To start everything:
```bash
./start_telegram_bot.sh
```
