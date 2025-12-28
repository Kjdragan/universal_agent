# Docker Deployment Guide

## Overview
We use Docker to package the Universal Agent and its dependencies into a "container". This ensures it runs exactly the same on your machine as it does on a server, avoiding "it works on my machine" issues.

## 1. Files
The deployment relies on two files in the project root:
- `Dockerfile`: Recipe for building the agent image (installing Python, ffmpeg, etc.).
- `docker-compose.yml`: Configuration for running the service (ports, environment variables, volumes).

## 2. Configuration
Ensure your `.env` file is populated (see `01_TELEGRAM_BOT_SETUP.md` and `02_NGROK_SETUP.md`). The `docker-compose.yml` automatically reads these values.

## 3. Build and Run
Open a terminal in the project root (`/home/kjdragan/lrepos/universal_agent`) and run:

```bash
docker-compose up -d --build
```
- `-d`: Detached mode (runs in background).
- `--build`: Rebuilds the image if code changed.

## 4. Managing the Bot
- **Check Status**: `docker ps`
- **View Logs**: `docker logs -f universal_agent_bot`
- **Stop Bot**: `docker-compose down`
- **Restart**: `docker-compose restart`

## 5. Register Webhook
Every time you change the `WEBHOOK_URL` (e.g., restarting Ngrok), you must tell Telegram the new address:

```bash
python register_webhook.py
```
(This script reads from your `.env` file automatically).
