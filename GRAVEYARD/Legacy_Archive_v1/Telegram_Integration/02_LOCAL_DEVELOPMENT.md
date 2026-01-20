# Local Development & Testing

**Last Updated**: 2025-12-31

This guide explains how to run the Telegram bot on your local machine using **ngrok** to expose it to the internet.

---

## Prerequisites

1.  **Python 3.12+** and `uv` package manager.
2.  **Ngrok**: [Sign up for free](https://dashboard.ngrok.com/signup) and install.
3.  Completed steps in [01_SETUP_AND_CONFIG.md](./01_SETUP_AND_CONFIG.md).

---

## Step 1: Install and Configure Ngrok

Since Telegram needs to send HTTPS requests to your laptop, we use ngrok to create a secure tunnel.

```bash
# 1. Install (Linux/WSL)
curl -O https://bin.equinox.io/c/bNyj1mQVY4c/ngrok-v3-stable-linux-amd64.tgz
sudo tar xvzf ngrok-v3-stable-linux-amd64.tgz -C /usr/local/bin

# 2. Authenticate (Get token from dashboard.ngrok.com)
ngrok config add-authtoken <YOUR_TOKEN>
```

---

## Step 2: Start the Tunnel

Run this in a **separate terminal** and keep it open:

```bash
ngrok http 8000
```

Copy the forwarding URL (e.g., `https://a1b2-c3d4.ngrok-free.app`).

---

## Step 3: Configure `.env`

In your project root `.env` file, update the `WEBHOOK_URL` with your temporary ngrok address:

```bash
# .env
WEBHOOK_URL=https://a1b2-c3d4.ngrok-free.app/webhook
WEBHOOK_SECRET=my-local-secret
TELEGRAM_BOT_TOKEN=...
ALLOWED_USER_IDS=...
```

---

## Step 4: Run the Bot

```bash
# In your main terminal
cd /path/to/universal_agent

# Run with hot-reloading (add src to python path)
PYTHONPATH=src uv run uvicorn universal_agent.bot.main:app --host 0.0.0.0 --port 8000 --reload
```

### Running Agent College (Sidecar)

If you want the full experience (including the background critic), open a **second terminal** and run:

```bash
# Run on a different port (e.g., 8001) to avoid conflict
PYTHONPATH=src uv run uvicorn AgentCollege.logfire_fetch.main:app --host 0.0.0.0 --port 8001 --reload
```
(Ensure your `.env` is loaded in this terminal too).

You should see logs indicating the webhook was successfully registered:
> Info: Webhook set to https://a1b2-c3d4.ngrok-free.app/webhook

---

## Step 5: Verify & Test

1.  **Health Check**:
    ```bash
    curl http://localhost:8000/health
    # Output: {"status":"healthy",...}
    ```

2.  **Telegram Chat**:
    -   Open your bot.
    -   Send `/start`.
    -   Send `/agent Hello world`.
    -   Verify the bot replies.

---

## Troubleshooting

-   **"DNS FAILED"**: Check your internet connection.
-   **Webhook 404**: Ensure your `WEBHOOK_URL` in `.env` ends with `/webhook`.
-   **Tunnel Expired**: Free ngrok URLs change every time you restart ngrok. You must update `.env` and restart the bot each time.
